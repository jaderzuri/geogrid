-- Additive migration for the public Landing Page, privacy choices,
-- capability-based status/report access, and a CRM T2S integration outbox.
-- Apply once to geogrid_queue after 001-schema.sql and 002-functions.sql.

ALTER TABLE geogrid_requests
    ALTER COLUMN google_maps_url DROP NOT NULL,
    ADD COLUMN IF NOT EXISTS privacy_acknowledged BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS privacy_policy_version TEXT NOT NULL DEFAULT 'legacy',
    ADD COLUMN IF NOT EXISTS marketing_consent BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS geogrid_status_tokens (
    request_id UUID NOT NULL REFERENCES geogrid_requests(request_id) ON DELETE CASCADE,
    token_sha256 CHAR(64) NOT NULL
        CHECK (token_sha256 ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    PRIMARY KEY (request_id, token_sha256)
);

CREATE TABLE IF NOT EXISTS geogrid_crm_outbox (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id UUID NOT NULL REFERENCES geogrid_requests(request_id),
    event_key TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL CHECK (event_type IN (
        'lead_received', 'diagnostic_status_changed'
    )),
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
        'pending', 'claimed', 'delivered', 'failed'
    )),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    claim_token UUID,
    claim_expires_at TIMESTAMPTZ,
    crm_contact_id TEXT,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS geogrid_crm_outbox_pending_idx
    ON geogrid_crm_outbox (created_at, event_id)
    WHERE status IN ('pending', 'claimed');

CREATE OR REPLACE FUNCTION geogrid_enqueue_landing_request(
    p_request_id UUID,
    p_status_token_hash TEXT,
    p_idempotency_key TEXT,
    p_payload_fingerprint TEXT,
    p_campaign_id TEXT,
    p_contact_name TEXT,
    p_company_name TEXT,
    p_email TEXT,
    p_whatsapp TEXT,
    p_city TEXT,
    p_state TEXT,
    p_google_maps_url TEXT,
    p_keyword TEXT,
    p_consent_at TIMESTAMPTZ,
    p_privacy_policy_version TEXT,
    p_marketing_consent BOOLEAN
)
RETURNS TABLE (saved_request_id UUID, saved_status TEXT, duplicate BOOLEAN)
LANGUAGE plpgsql
AS $$
DECLARE
    v_request_id UUID;
    v_fingerprint TEXT;
    v_status TEXT;
    v_duplicate BOOLEAN := FALSE;
    v_default_event_cap NUMERIC(12, 6);
BEGIN
    IF p_status_token_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'Invalid public status capability hash';
    END IF;
    IF coalesce(p_privacy_policy_version, '') = '' THEN
        RAISE EXCEPTION 'Privacy policy version is required';
    END IF;

    INSERT INTO geogrid_requests (
        request_id, idempotency_key, payload_fingerprint, campaign_id,
        contact_name, company_name, email, whatsapp, city, state,
        google_maps_url, keyword, consent_at, privacy_acknowledged,
        privacy_policy_version, marketing_consent
    )
    VALUES (
        p_request_id, p_idempotency_key, p_payload_fingerprint, p_campaign_id,
        p_contact_name, p_company_name, p_email, p_whatsapp, p_city, p_state,
        nullif(p_google_maps_url, ''), p_keyword, p_consent_at, TRUE,
        p_privacy_policy_version, p_marketing_consent
    )
    ON CONFLICT (idempotency_key) DO NOTHING
    RETURNING request_id, status INTO v_request_id, v_status;

    IF v_request_id IS NOT NULL THEN
        SELECT default_event_cap_usd
        INTO v_default_event_cap
        FROM geogrid_budget
        WHERE scope_type = 'system' AND scope_key = 'default';
        INSERT INTO geogrid_budget (scope_type, scope_key, cap_amount_usd)
        VALUES ('event', p_campaign_id, v_default_event_cap)
        ON CONFLICT (scope_type, scope_key) DO NOTHING;
    ELSE
        SELECT r.request_id, r.payload_fingerprint, r.status
        INTO v_request_id, v_fingerprint, v_status
        FROM geogrid_requests r
        WHERE r.idempotency_key = p_idempotency_key
        FOR UPDATE;
        IF v_request_id IS NULL THEN
            RAISE EXCEPTION 'Idempotency conflict; request was not persisted'
                USING ERRCODE = '23505';
        END IF;
        IF v_fingerprint <> p_payload_fingerprint THEN
            IF v_request_id <> p_request_id THEN
                RETURN QUERY SELECT v_request_id, v_status, TRUE;
                RETURN;
            END IF;
            RAISE EXCEPTION 'Idempotency key reused with different request content'
                USING ERRCODE = '23505';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM geogrid_status_tokens st
            WHERE st.request_id = v_request_id
              AND st.token_sha256 = p_status_token_hash
        ) THEN
            IF v_request_id <> p_request_id THEN
                RETURN QUERY SELECT v_request_id, v_status, TRUE;
                RETURN;
            END IF;
            RAISE EXCEPTION 'Duplicate request requires its original status capability'
                USING ERRCODE = '23505';
        END IF;
        v_duplicate := TRUE;
    END IF;

    IF NOT v_duplicate THEN
        INSERT INTO geogrid_status_tokens (request_id, token_sha256)
        VALUES (v_request_id, p_status_token_hash);
    END IF;

    RETURN QUERY SELECT v_request_id, v_status, v_duplicate;
END;
$$;

CREATE OR REPLACE FUNCTION geogrid_capture_crm_outbox_event()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_event_type TEXT;
    v_event_key TEXT;
BEGIN
    IF TG_OP = 'INSERT' THEN
        v_event_type := 'lead_received';
        v_event_key := NEW.request_id::TEXT || ':lead_received';
    ELSIF OLD.status IS DISTINCT FROM NEW.status
       OR OLD.scan_id IS DISTINCT FROM NEW.scan_id THEN
        v_event_type := 'diagnostic_status_changed';
        v_event_key := NEW.request_id::TEXT || ':status:' || NEW.status
            || ':' || coalesce(NEW.scan_id, '');
    ELSE
        RETURN NEW;
    END IF;

    INSERT INTO geogrid_crm_outbox (
        request_id, event_key, event_type, payload
    )
    VALUES (
        NEW.request_id,
        v_event_key,
        v_event_type,
        jsonb_build_object(
            'event_type', v_event_type,
            'source', 'zuri_smart_grid_landing',
            'campaign_id', NEW.campaign_id,
            'contact', jsonb_build_object(
                'name', NEW.contact_name,
                'email', NEW.email,
                'whatsapp', NEW.whatsapp
            ),
            'company', jsonb_build_object(
                'name', NEW.company_name,
                'city', NEW.city,
                'state', NEW.state,
                'google_maps_url', NEW.google_maps_url
            ),
            'consent', jsonb_build_object(
                'privacy_acknowledged', NEW.privacy_acknowledged,
                'privacy_policy_version', NEW.privacy_policy_version,
                'marketing_consent', NEW.marketing_consent,
                'recorded_at', NEW.consent_at
            ),
            'diagnostic', jsonb_build_object(
                'request_id', NEW.request_id,
                'scan_id', NEW.scan_id,
                'status', NEW.status
            ),
            'labels', jsonb_build_array(
                'smart-grid', 'zuri', 'local-diagnostic'
            ),
            'pipeline_mapping_key', 'CONFIGURE_FROM_T2S_DOCUMENTATION',
            'stage_mapping_key', 'CONFIGURE_FROM_T2S_DOCUMENTATION'
        )
    )
    ON CONFLICT (event_key) DO NOTHING;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS geogrid_crm_outbox_insert ON geogrid_requests;
CREATE TRIGGER geogrid_crm_outbox_insert
AFTER INSERT ON geogrid_requests
FOR EACH ROW EXECUTE FUNCTION geogrid_capture_crm_outbox_event();

DROP TRIGGER IF EXISTS geogrid_crm_outbox_status_update ON geogrid_requests;
CREATE TRIGGER geogrid_crm_outbox_status_update
AFTER UPDATE OF status, scan_id ON geogrid_requests
FOR EACH ROW EXECUTE FUNCTION geogrid_capture_crm_outbox_event();

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
    SELECT
        EXISTS (
            SELECT 1 FROM geogrid_status_tokens st
            WHERE st.request_id = r.request_id
              AND st.token_sha256 = p_status_token_hash
        ) AS authorized,
        CASE WHEN EXISTS (
            SELECT 1 FROM geogrid_status_tokens st
            WHERE st.request_id = r.request_id
              AND st.token_sha256 = p_status_token_hash
        ) THEN r.request_id ELSE NULL END,
        CASE WHEN EXISTS (
            SELECT 1 FROM geogrid_status_tokens st
            WHERE st.request_id = r.request_id
              AND st.token_sha256 = p_status_token_hash
        ) THEN r.status ELSE NULL END,
        CASE WHEN EXISTS (
            SELECT 1 FROM geogrid_status_tokens st
            WHERE st.request_id = r.request_id
              AND st.token_sha256 = p_status_token_hash
        ) THEN (
            SELECT count(*)::INTEGER FROM geogrid_tasks t
            WHERE t.request_id = r.request_id
              AND t.state IN ('done', 'no_results', 'failed')
        ) ELSE 0 END,
        CASE WHEN EXISTS (
            SELECT 1 FROM geogrid_status_tokens st
            WHERE st.request_id = r.request_id
              AND st.token_sha256 = p_status_token_hash
        ) THEN r.total_points::INTEGER ELSE 9 END,
        CASE WHEN EXISTS (
            SELECT 1 FROM geogrid_status_tokens st
            WHERE st.request_id = r.request_id
              AND st.token_sha256 = p_status_token_hash
        ) THEN r.finished_at ELSE NULL END,
        EXISTS (
            SELECT 1 FROM geogrid_status_tokens st
            WHERE st.request_id = r.request_id
              AND st.token_sha256 = p_status_token_hash
        ) AND r.scan_id IS NOT NULL AND r.finished_at IS NOT NULL
          AND (
              SELECT count(*) FROM geogrid_tasks t
              WHERE t.request_id = r.request_id
                AND t.state IN ('done', 'no_results', 'failed')
          ) = r.total_points
    FROM geogrid_requests r
    WHERE r.request_id = p_request_id
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
    SELECT
        EXISTS (
            SELECT 1 FROM geogrid_status_tokens st
            WHERE st.request_id = r.request_id
              AND st.token_sha256 = p_status_token_hash
        ),
        r.scan_id IS NOT NULL AND r.finished_at IS NOT NULL
          AND (
              SELECT count(*) FROM geogrid_tasks t
              WHERE t.request_id = r.request_id
                AND t.state IN ('done', 'no_results', 'failed')
          ) = r.total_points,
        CASE WHEN EXISTS (
            SELECT 1 FROM geogrid_status_tokens st
            WHERE st.request_id = r.request_id
              AND st.token_sha256 = p_status_token_hash
        ) AND r.scan_id IS NOT NULL AND r.finished_at IS NOT NULL
          AND (
              SELECT count(*) FROM geogrid_tasks t
              WHERE t.request_id = r.request_id
                AND t.state IN ('done', 'no_results', 'failed')
          ) = r.total_points
        THEN r.scan_id ELSE NULL END
    FROM geogrid_requests r
    WHERE r.request_id = p_request_id
    UNION ALL
    SELECT FALSE, FALSE, NULL::TEXT
    WHERE NOT EXISTS (
        SELECT 1 FROM geogrid_requests WHERE request_id = p_request_id
    );
$$;
