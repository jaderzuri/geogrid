-- Additive lifecycle controls for public status capabilities and CRM delivery.
-- Apply after 003-landing-crm-capability.sql.

ALTER TABLE geogrid_status_tokens
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ
        NOT NULL DEFAULT (now() + interval '30 days'),
    ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS geogrid_status_tokens_expiry_idx
    ON geogrid_status_tokens (expires_at)
    WHERE revoked_at IS NULL;

CREATE OR REPLACE FUNCTION geogrid_revoke_status_token(
    p_request_id UUID,
    p_token_sha256 TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    v_revoked BOOLEAN;
BEGIN
    UPDATE geogrid_status_tokens
    SET revoked_at = COALESCE(revoked_at, now())
    WHERE request_id = p_request_id
      AND token_sha256 = p_token_sha256
      AND revoked_at IS NULL
    RETURNING TRUE INTO v_revoked;
    RETURN COALESCE(v_revoked, FALSE);
END;
$$;

CREATE OR REPLACE FUNCTION geogrid_get_public_status(
    p_request_id UUID,
    p_status_token_hash TEXT
)
RETURNS TABLE (
    authorized BOOLEAN,
    request_id UUID,
    status TEXT,
    progress INTEGER,
    total INTEGER,
    finished_at TIMESTAMPTZ,
    report_ready BOOLEAN
)
LANGUAGE sql
STABLE
AS $$
    WITH scoped AS (
        SELECT
            r.*,
            EXISTS (
                SELECT 1
                FROM geogrid_status_tokens st
                WHERE st.request_id = r.request_id
                  AND st.token_sha256 = p_status_token_hash
                  AND st.revoked_at IS NULL
                  AND st.expires_at > now()
            ) AS capability_valid
        FROM geogrid_requests r
        WHERE r.request_id = p_request_id
    )
    SELECT
        s.capability_valid,
        CASE WHEN s.capability_valid THEN s.request_id ELSE NULL END,
        CASE WHEN s.capability_valid THEN s.status ELSE NULL END,
        CASE WHEN s.capability_valid THEN (
            SELECT count(*)::INTEGER
            FROM geogrid_tasks t
            WHERE t.request_id = s.request_id
              AND t.state IN ('done', 'no_results', 'failed')
        ) ELSE 0 END,
        CASE WHEN s.capability_valid THEN s.total_points::INTEGER ELSE 9 END,
        CASE WHEN s.capability_valid THEN s.finished_at ELSE NULL END,
        s.capability_valid
            AND s.scan_id IS NOT NULL
            AND s.finished_at IS NOT NULL
            AND (
                SELECT count(*)
                FROM geogrid_tasks t
                WHERE t.request_id = s.request_id
                  AND t.state IN ('done', 'no_results', 'failed')
            ) = s.total_points
    FROM scoped s
    UNION ALL
    SELECT FALSE, NULL::UUID, NULL::TEXT, 0, 9, NULL::TIMESTAMPTZ, FALSE
    WHERE NOT EXISTS (
        SELECT 1 FROM geogrid_requests WHERE request_id = p_request_id
    );
$$;

CREATE OR REPLACE FUNCTION geogrid_get_protected_report(
    p_request_id UUID,
    p_status_token_hash TEXT
)
RETURNS TABLE (authorized BOOLEAN, report_ready BOOLEAN, scan_id TEXT)
LANGUAGE sql
STABLE
AS $$
    WITH scoped AS (
        SELECT
            r.scan_id,
            r.finished_at,
            r.total_points,
            EXISTS (
                SELECT 1
                FROM geogrid_status_tokens st
                WHERE st.request_id = r.request_id
                  AND st.token_sha256 = p_status_token_hash
                  AND st.revoked_at IS NULL
                  AND st.expires_at > now()
            ) AS capability_valid,
            (
                SELECT count(*) = r.total_points
                FROM geogrid_tasks t
                WHERE t.request_id = r.request_id
                  AND t.state IN ('done', 'no_results', 'failed')
            ) AS all_points_resolved
        FROM geogrid_requests r
        WHERE r.request_id = p_request_id
    )
    SELECT
        s.capability_valid,
        s.capability_valid
            AND s.scan_id IS NOT NULL
            AND s.finished_at IS NOT NULL
            AND s.all_points_resolved,
        CASE WHEN s.capability_valid
            AND s.scan_id IS NOT NULL
            AND s.finished_at IS NOT NULL
            AND s.all_points_resolved
            THEN s.scan_id ELSE NULL END
    FROM scoped s
    UNION ALL
    SELECT FALSE, FALSE, NULL::TEXT
    WHERE NOT EXISTS (
        SELECT 1 FROM geogrid_requests WHERE request_id = p_request_id
    );
$$;

ALTER TABLE geogrid_crm_outbox
    ADD COLUMN IF NOT EXISTS claim_owner TEXT,
    ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS error_category TEXT;

CREATE INDEX IF NOT EXISTS geogrid_crm_outbox_dispatch_idx
    ON geogrid_crm_outbox (next_attempt_at, created_at, event_id)
    WHERE status IN ('pending', 'claimed');

CREATE OR REPLACE FUNCTION geogrid_claim_crm_outbox(
    p_worker_id TEXT,
    p_limit INTEGER DEFAULT 25
)
RETURNS SETOF geogrid_crm_outbox
LANGUAGE plpgsql
AS $$
BEGIN
    IF coalesce(length(trim(p_worker_id)), 0) NOT BETWEEN 1 AND 100 THEN
        RAISE EXCEPTION 'Invalid CRM worker identifier';
    END IF;

    UPDATE geogrid_crm_outbox
    SET status = 'failed',
        claim_owner = NULL,
        claim_token = NULL,
        claim_expires_at = NULL,
        error_category = 'lease_exhausted',
        last_error = 'CRM delivery lease expired after maximum attempts',
        updated_at = now()
    WHERE status = 'claimed'
      AND claim_expires_at < now()
      AND attempts >= 5;

    RETURN QUERY
    WITH candidates AS (
        SELECT o.event_id
        FROM geogrid_crm_outbox o
        WHERE o.attempts < 5
          AND (
              (o.status = 'pending' AND o.next_attempt_at <= now())
              OR (o.status = 'claimed' AND o.claim_expires_at < now())
          )
        ORDER BY o.created_at, o.event_id
        FOR UPDATE SKIP LOCKED
        LIMIT LEAST(GREATEST(COALESCE(p_limit, 1), 1), 100)
    )
    UPDATE geogrid_crm_outbox o
    SET status = 'claimed',
        attempts = o.attempts + 1,
        claim_owner = p_worker_id,
        claim_token = gen_random_uuid(),
        claim_expires_at = now() + interval '5 minutes',
        updated_at = now()
    FROM candidates c
    WHERE o.event_id = c.event_id
    RETURNING o.*;
END;
$$;

CREATE OR REPLACE FUNCTION geogrid_mark_crm_outbox_delivered(
    p_event_id UUID,
    p_claim_token UUID,
    p_crm_contact_id TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    v_delivered BOOLEAN;
BEGIN
    UPDATE geogrid_crm_outbox
    SET status = 'delivered',
        crm_contact_id = NULLIF(left(p_crm_contact_id, 200), ''),
        delivered_at = now(),
        claim_owner = NULL,
        claim_token = NULL,
        claim_expires_at = NULL,
        last_error = NULL,
        error_category = NULL,
        updated_at = now()
    WHERE event_id = p_event_id
      AND status = 'claimed'
      AND claim_token = p_claim_token
    RETURNING TRUE INTO v_delivered;
    RETURN COALESCE(v_delivered, FALSE);
END;
$$;

CREATE OR REPLACE FUNCTION geogrid_mark_crm_outbox_failure(
    p_event_id UUID,
    p_claim_token UUID,
    p_error_category TEXT,
    p_error_message TEXT,
    p_retry_safe BOOLEAN
)
RETURNS TEXT
LANGUAGE plpgsql
AS $$
DECLARE
    v_attempts INTEGER;
    v_status TEXT;
    v_backoff INTERVAL;
BEGIN
    SELECT attempts INTO v_attempts
    FROM geogrid_crm_outbox
    WHERE event_id = p_event_id
      AND status = 'claimed'
      AND claim_token = p_claim_token
    FOR UPDATE;
    IF NOT FOUND THEN
        RETURN 'stale_claim';
    END IF;

    v_backoff := CASE v_attempts
        WHEN 1 THEN interval '1 minute'
        WHEN 2 THEN interval '5 minutes'
        WHEN 3 THEN interval '15 minutes'
        ELSE interval '1 hour'
    END;
    v_status := CASE WHEN p_retry_safe AND v_attempts < 5
        THEN 'pending' ELSE 'failed' END;

    UPDATE geogrid_crm_outbox
    SET status = v_status,
        next_attempt_at = CASE WHEN v_status = 'pending'
            THEN now() + v_backoff ELSE next_attempt_at END,
        claim_owner = NULL,
        claim_token = NULL,
        claim_expires_at = NULL,
        error_category = left(coalesce(p_error_category, 'delivery_error'), 100),
        last_error = left(coalesce(p_error_message, 'CRM adapter delivery failed'), 1000),
        updated_at = now()
    WHERE event_id = p_event_id;
    RETURN v_status;
END;
$$;
