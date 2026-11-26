-- Apply after 007 with workers stopped. All paid requests stay one-shot.
BEGIN;

ALTER TABLE geogrid_requests
    ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS geogrid_retry_policy (
    policy_key TEXT PRIMARY KEY CHECK (policy_key = 'processor'),
    max_attempts INTEGER NOT NULL CHECK (max_attempts BETWEEN 1 AND 10),
    base_delay_seconds INTEGER NOT NULL CHECK (base_delay_seconds BETWEEN 1 AND 3600),
    max_delay_seconds INTEGER NOT NULL CHECK (max_delay_seconds BETWEEN 1 AND 86400),
    jitter_percent INTEGER NOT NULL CHECK (jitter_percent BETWEEN 0 AND 100),
    response_recovery_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (max_delay_seconds >= base_delay_seconds)
);
ALTER TABLE geogrid_retry_policy
    ADD COLUMN IF NOT EXISTS response_recovery_enabled BOOLEAN NOT NULL DEFAULT TRUE;

INSERT INTO geogrid_retry_policy(
    policy_key, max_attempts, base_delay_seconds, max_delay_seconds, jitter_percent
) VALUES ('processor', 5, 10, 900, 20)
ON CONFLICT (policy_key) DO NOTHING;

CREATE TABLE IF NOT EXISTS geogrid_paid_response_journal (
    request_id UUID PRIMARY KEY REFERENCES geogrid_requests(request_id),
    authorization_claim_token UUID NOT NULL,
    execution_id TEXT NOT NULL CHECK (execution_id ~ '^[A-Za-z0-9._:-]{1,100}$'),
    response_body JSONB NOT NULL CHECK (jsonb_typeof(response_body) = 'object'),
    response_digest TEXT NOT NULL CHECK (response_digest ~ '^[a-f0-9]{32}$'),
    captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    recovery_attempts INTEGER NOT NULL DEFAULT 0 CHECK (recovery_attempts >= 0),
    recovery_token UUID,
    recovery_lease_expires_at TIMESTAMPTZ,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    recovered_at TIMESTAMPTZ,
    last_error TEXT,
    CHECK ((recovery_token IS NULL) = (recovery_lease_expires_at IS NULL))
);

CREATE INDEX IF NOT EXISTS geogrid_paid_response_recovery_idx
    ON geogrid_paid_response_journal(next_attempt_at, captured_at)
    WHERE recovered_at IS NULL;

CREATE OR REPLACE FUNCTION geogrid_journal_paid_response(
    p_request_id UUID, p_claim_token UUID, p_execution_id TEXT, p_response JSONB
)
RETURNS BOOLEAN LANGUAGE plpgsql AS $$
DECLARE
    r geogrid_requests%ROWTYPE;
    v_digest TEXT;
BEGIN
    IF p_response IS NULL OR jsonb_typeof(p_response) <> 'object'
       OR p_execution_id IS NULL
       OR p_execution_id !~ '^[A-Za-z0-9._:-]{1,100}$' THEN
        RAISE EXCEPTION 'Invalid DataForSEO response journal entry';
    END IF;
    SELECT * INTO r FROM geogrid_requests
    WHERE request_id = p_request_id FOR UPDATE;
    IF NOT FOUND OR r.task_post_authorization_count <> 1
       OR r.task_post_authorized_claim_token IS DISTINCT FROM p_claim_token
       OR p_claim_token IS NULL
       OR r.status NOT IN ('submitting', 'submission_uncertain', 'submitted',
                           'processing', 'partial', 'done', 'failed') THEN
        RAISE EXCEPTION 'Response is not owned by the paid authorization';
    END IF;
    v_digest := md5(p_response::text);
    INSERT INTO geogrid_paid_response_journal(
        request_id, authorization_claim_token, execution_id, response_body,
        response_digest
    ) VALUES (
        p_request_id, p_claim_token, p_execution_id, p_response, v_digest
    )
    ON CONFLICT (request_id) DO NOTHING;
    IF NOT FOUND THEN
        IF NOT EXISTS (
            SELECT 1 FROM geogrid_paid_response_journal
            WHERE request_id = p_request_id
              AND authorization_claim_token = p_claim_token
              AND response_digest = v_digest
        ) THEN
            RAISE EXCEPTION 'Conflicting repeated DataForSEO response';
        END IF;
    END IF;
    RETURN TRUE;
END;
$$;

CREATE OR REPLACE FUNCTION geogrid_claim_paid_response_recovery(p_execution_id TEXT)
RETURNS TABLE(
    request_id UUID, authorization_claim_token UUID, execution_id TEXT,
    response_body JSONB, point_map JSONB, recovery_token UUID
)
LANGUAGE plpgsql AS $$
DECLARE
    j geogrid_paid_response_journal%ROWTYPE;
    p geogrid_retry_policy%ROWTYPE;
BEGIN
    IF p_execution_id IS NULL OR p_execution_id !~ '^[A-Za-z0-9._:-]{1,100}$' THEN
        RAISE EXCEPTION 'Invalid recovery execution id';
    END IF;
    SELECT * INTO p FROM geogrid_retry_policy WHERE policy_key = 'processor';
    IF NOT FOUND OR NOT p.response_recovery_enabled THEN RETURN; END IF;
    SELECT journal.* INTO j
    FROM geogrid_paid_response_journal journal
    WHERE journal.recovered_at IS NULL
      AND journal.next_attempt_at <= now()
      AND (journal.recovery_lease_expires_at IS NULL
           OR journal.recovery_lease_expires_at <= now())
      AND journal.recovery_attempts < p.max_attempts
    ORDER BY journal.next_attempt_at, journal.captured_at
    LIMIT 1
    FOR UPDATE SKIP LOCKED;
    IF NOT FOUND THEN RETURN; END IF;

    UPDATE geogrid_paid_response_journal
    SET recovery_attempts = recovery_attempts + 1,
        recovery_token = gen_random_uuid(),
        recovery_lease_expires_at = now() + interval '5 minutes',
        last_error = NULL
    WHERE geogrid_paid_response_journal.request_id = j.request_id
    RETURNING * INTO j;

    RETURN QUERY SELECT j.request_id, j.authorization_claim_token, j.execution_id,
        j.response_body,
        (SELECT jsonb_agg(jsonb_build_object(
            'row', t.row_index, 'col', t.col_index, 'tag', t.tag,
            'lat', t.latitude, 'lng', t.longitude
        ) ORDER BY t.point_index)
         FROM geogrid_tasks t WHERE t.request_id = j.request_id),
        j.recovery_token;
END;
$$;

CREATE OR REPLACE FUNCTION geogrid_mark_paid_response_recovered(
    p_request_id UUID, p_recovery_token UUID
)
RETURNS BOOLEAN LANGUAGE plpgsql AS $$
BEGIN
    UPDATE geogrid_paid_response_journal j
    SET recovered_at = now(), recovery_token = NULL,
        recovery_lease_expires_at = NULL, last_error = NULL
    WHERE j.request_id = p_request_id
      AND j.recovery_token IS NOT DISTINCT FROM p_recovery_token
      AND EXISTS (
          SELECT 1 FROM geogrid_requests r
          WHERE r.request_id = j.request_id
            AND r.task_post_response_saved_at IS NOT NULL
      );
    RETURN FOUND;
END;
$$;

CREATE OR REPLACE FUNCTION geogrid_record_processor_error(
    p_request_id UUID, p_claim_token UUID, p_node TEXT, p_category TEXT,
    p_code TEXT, p_execution_id TEXT, p_retry_after_seconds INTEGER
)
RETURNS BOOLEAN LANGUAGE plpgsql AS $$
DECLARE
    v_recorded BOOLEAN;
    v_log_id BIGINT;
    v_delay_seconds INTEGER;
    v_node TEXT := CASE WHEN p_node = 'Journal DataForSEO Paid Response'
                        THEN 'Persist DataForSEO IDs and Cost' ELSE p_node END;
BEGIN
    IF p_retry_after_seconds IS NOT NULL
       AND (p_retry_after_seconds < 0 OR p_retry_after_seconds > 86400) THEN
        RAISE EXCEPTION 'Retry-After is outside the supported bound';
    END IF;
    v_recorded := geogrid_record_processor_error(
        p_request_id, p_claim_token, v_node, p_category, p_code, p_execution_id
    );
    IF p_retry_after_seconds IS NOT NULL THEN
        SELECT log_id INTO v_log_id FROM geogrid_execution_logs
        WHERE request_id = p_request_id AND event_type = 'processor_error'
          AND details->>'execution_id' = p_execution_id
        ORDER BY created_at DESC, log_id DESC LIMIT 1;
        UPDATE geogrid_execution_logs SET details = details ||
            jsonb_build_object('node', p_node, 'retry_after_seconds', p_retry_after_seconds)
        WHERE log_id = v_log_id;
        UPDATE geogrid_requests SET next_attempt_at = greatest(
            coalesce(next_attempt_at, now()),
            now() + make_interval(secs => p_retry_after_seconds)
        ) WHERE request_id = p_request_id
          AND status = 'queued' AND task_post_authorization_count = 0;
        SELECT ceil(extract(epoch FROM next_attempt_at - now()))::INTEGER
        INTO v_delay_seconds FROM geogrid_requests WHERE request_id = p_request_id;
        UPDATE geogrid_execution_logs SET details = details ||
            jsonb_build_object('scheduled_delay_seconds', greatest(
                coalesce((details->>'scheduled_delay_seconds')::INTEGER, 0),
                v_delay_seconds
            ))
        WHERE log_id = v_log_id;
    END IF;
    RETURN v_recorded;
END;
$$;

CREATE OR REPLACE FUNCTION geogrid_claim_next_request(p_worker_id TEXT)
RETURNS SETOF geogrid_requests
LANGUAGE plpgsql
AS $$
DECLARE
    v_control geogrid_budget%ROWTYPE;
    v_daily geogrid_budget%ROWTYPE;
    v_event geogrid_budget%ROWTYPE;
    v_request geogrid_requests%ROWTYPE;
    v_policy geogrid_retry_policy%ROWTYPE;
    v_daily_key TEXT := to_char((now() AT TIME ZONE 'UTC')::date, 'YYYY-MM-DD');
    v_reserved NUMERIC(12, 6);
    v_pending BIGINT;
    v_claims_last_minute BIGINT;
BEGIN
    PERFORM pg_advisory_xact_lock(1847219, 1);
    PERFORM geogrid_release_expired_claim_reservations();

    SELECT * INTO v_control FROM geogrid_budget
    WHERE scope_type = 'system' AND scope_key = 'default' FOR UPDATE;
    IF NOT FOUND THEN RETURN; END IF;
    SELECT * INTO v_policy FROM geogrid_retry_policy WHERE policy_key = 'processor';
    IF NOT FOUND OR NOT v_control.enabled
       OR v_control.max_pending_tasks IS NULL
       OR v_control.new_diagnostics_per_minute IS NULL
       OR v_control.estimated_task_cost_usd IS NULL
       OR v_control.estimated_task_cost_usd <= 0
       OR v_control.daily_cap_usd IS NULL
       OR v_control.default_event_cap_usd IS NULL THEN
        RETURN;
    END IF;

    UPDATE geogrid_requests
    SET status = 'manual_review', error_category = 'retry_limit',
        error_message = 'Maximum safe pre-submission attempts reached',
        finished_at = now(), updated_at = now()
    WHERE status = 'queued' AND task_post_authorization_count = 0
      AND attempt_count >= v_policy.max_attempts;

    SELECT count(*) INTO v_claims_last_minute
    FROM geogrid_requests WHERE claimed_at >= now() - interval '1 minute';
    IF v_claims_last_minute >= v_control.new_diagnostics_per_minute THEN RETURN; END IF;

    SELECT count(*) INTO v_pending FROM geogrid_tasks
    WHERE state IN ('submission_started', 'submission_uncertain',
                    'submitted', 'polling', 'pending');
    IF v_pending + 9 > v_control.max_pending_tasks THEN RETURN; END IF;

    SELECT * INTO v_request FROM geogrid_requests
    WHERE status = 'queued' AND task_post_authorization_count = 0
      AND budget_reserved_usd = 0
      AND (next_attempt_at IS NULL OR next_attempt_at <= now())
      AND attempt_count < v_policy.max_attempts
    ORDER BY coalesce(next_attempt_at, created_at), created_at, request_id
    LIMIT 1 FOR UPDATE SKIP LOCKED;
    IF NOT FOUND THEN RETURN; END IF;

    INSERT INTO geogrid_budget(scope_type, scope_key, cap_amount_usd)
    VALUES ('daily', v_daily_key, v_control.daily_cap_usd)
    ON CONFLICT (scope_type, scope_key) DO NOTHING;
    SELECT * INTO v_daily FROM geogrid_budget
    WHERE scope_type = 'daily' AND scope_key = v_daily_key FOR UPDATE;
    SELECT * INTO v_event FROM geogrid_budget
    WHERE scope_type = 'event' AND scope_key = v_request.campaign_id FOR UPDATE;

    v_reserved := 9 * v_control.estimated_task_cost_usd;
    IF v_daily.cap_amount_usd IS NULL OR v_event.cap_amount_usd IS NULL
       OR v_daily.spent_amount_usd + v_daily.reserved_amount_usd + v_reserved > v_daily.cap_amount_usd
       OR v_event.spent_amount_usd + v_event.reserved_amount_usd + v_reserved > v_event.cap_amount_usd THEN
        UPDATE geogrid_requests SET status = 'budget_blocked',
            error_category = 'budget_limit',
            error_message = 'Required daily or campaign budget is unavailable',
            updated_at = now() WHERE request_id = v_request.request_id;
        INSERT INTO geogrid_execution_logs(request_id, workflow_name, event_type, severity, details)
        VALUES (v_request.request_id, '02-processador-standard',
            'budget_reservation_denied', 'warning',
            jsonb_build_object('daily_scope', v_daily_key,
                               'event_scope', v_request.campaign_id));
        RETURN;
    END IF;

    UPDATE geogrid_budget SET reserved_amount_usd = reserved_amount_usd + v_reserved,
        updated_at = now()
    WHERE (scope_type, scope_key) IN (('daily', v_daily_key), ('event', v_request.campaign_id));
    UPDATE geogrid_requests SET status = 'claimed', claim_owner = p_worker_id,
        claim_token = gen_random_uuid(), claim_expires_at = now() + interval '10 minutes',
        claimed_at = now(), attempt_count = attempt_count + 1,
        budget_reserved_usd = v_reserved, budget_reserved_scope_daily_key = v_daily_key,
        budget_reservation_state = 'reserved', actual_cost_usd = 0,
        actual_cost_known = FALSE, next_attempt_at = NULL, updated_at = now(),
        error_category = NULL, error_code = NULL, error_message = NULL
    WHERE request_id = v_request.request_id RETURNING * INTO v_request;

    INSERT INTO geogrid_execution_logs(request_id, workflow_name, event_type, details)
    VALUES (v_request.request_id, '02-processador-standard', 'request_claimed',
        jsonb_build_object('worker_id', p_worker_id,
                           'reserved_usd', v_reserved, 'daily_scope', v_daily_key));
    RETURN NEXT v_request;
END;
$$;

CREATE OR REPLACE FUNCTION geogrid_schedule_safe_retry()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    r geogrid_requests%ROWTYPE;
    p geogrid_retry_policy%ROWTYPE;
    v_retry_after INTEGER;
    v_base NUMERIC;
    v_delay NUMERIC;
BEGIN
    IF NEW.event_type NOT IN ('processor_error', 'expired_pre_submission_reservation_released')
       OR (NEW.event_type = 'processor_error'
           AND (NEW.details->>'paid_phase' <> 'before_authorization'
                OR coalesce((NEW.details->>'recoverable')::BOOLEAN, FALSE) IS NOT TRUE)) THEN
        RETURN NEW;
    END IF;
    SELECT * INTO r FROM geogrid_requests WHERE request_id = NEW.request_id FOR UPDATE;
    SELECT * INTO p FROM geogrid_retry_policy WHERE policy_key = 'processor';
    IF NOT FOUND OR r.task_post_authorization_count <> 0
       OR r.status NOT IN ('queued', 'manual_review') THEN
        RETURN NEW;
    END IF;
    IF r.attempt_count >= p.max_attempts THEN
        UPDATE geogrid_requests SET status = 'manual_review',
            next_attempt_at = NULL, finished_at = now(),
            error_category = 'retry_limit',
            error_message = 'Maximum safe pre-submission attempts reached',
            updated_at = now() WHERE request_id = r.request_id;
        UPDATE geogrid_execution_logs SET details = details ||
            jsonb_build_object('recoverable', FALSE, 'manual_intervention', TRUE,
                               'retry_limit_reached', TRUE)
        WHERE log_id = NEW.log_id;
        RETURN NEW;
    END IF;
    v_base := least(p.max_delay_seconds::NUMERIC,
        p.base_delay_seconds::NUMERIC * power(2::NUMERIC, greatest(r.attempt_count - 1, 0)));
    v_delay := v_base * (1 + ((random() * 2 - 1) * p.jitter_percent / 100.0));
    v_retry_after := CASE
        WHEN NEW.details->>'retry_after_seconds' ~ '^[0-9]{1,6}$'
            THEN least((NEW.details->>'retry_after_seconds')::INTEGER, 86400)
        ELSE 0
    END;
    v_delay := greatest(v_delay, v_retry_after);
    UPDATE geogrid_requests SET status = 'queued',
        next_attempt_at = now() + make_interval(secs => v_delay::INTEGER),
        finished_at = NULL, updated_at = now()
    WHERE request_id = r.request_id;
    UPDATE geogrid_execution_logs SET details = details ||
        jsonb_build_object('scheduled_delay_seconds', v_delay::INTEGER,
                           'retry_after_seconds', v_retry_after)
    WHERE log_id = NEW.log_id;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS geogrid_schedule_safe_retry ON geogrid_execution_logs;
CREATE TRIGGER geogrid_schedule_safe_retry
AFTER INSERT ON geogrid_execution_logs
FOR EACH ROW EXECUTE FUNCTION geogrid_schedule_safe_retry();

COMMIT;
