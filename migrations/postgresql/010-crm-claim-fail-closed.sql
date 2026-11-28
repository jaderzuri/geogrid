BEGIN;

-- Serialize claims. An expired lease is still an unresolved external outcome.
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

    PERFORM pg_advisory_xact_lock(1847219, 2);
    IF EXISTS (SELECT 1 FROM geogrid_crm_outbox WHERE status = 'claimed') THEN
        RETURN;
    END IF;

    RETURN QUERY
    WITH candidate AS (
        SELECT o.event_id
        FROM geogrid_crm_outbox o
        WHERE o.status = 'pending'
          AND o.next_attempt_at <= now()
          AND o.attempts < 5
        ORDER BY o.created_at, o.event_id
        FOR UPDATE
        LIMIT 1
    )
    UPDATE geogrid_crm_outbox o
    SET status = 'claimed',
        attempts = o.attempts + 1,
        claim_owner = p_worker_id,
        claim_token = gen_random_uuid(),
        claim_expires_at = now() + interval '5 minutes',
        updated_at = now()
    FROM candidate c
    WHERE o.event_id = c.event_id
    RETURNING o.*;
END;
$$;

-- Workflow 06 previously passed TRUE for every failure, including transport
-- errors. Its flag alone is insufficient evidence that delivery did not occur.
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
BEGIN
    UPDATE geogrid_crm_outbox
    SET error_category = left(coalesce(p_error_category, 'delivery_error'), 100),
        last_error = left(coalesce(p_error_message, 'CRM adapter delivery failed'), 1000),
        updated_at = now()
    WHERE event_id = p_event_id
      AND status = 'claimed'
      AND claim_token = p_claim_token;

    IF NOT FOUND THEN
        RETURN 'stale_claim';
    END IF;
    RETURN 'claimed';
END;
$$;

COMMIT;
