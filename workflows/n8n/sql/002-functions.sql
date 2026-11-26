-- Apply while connected to geogrid_queue. These functions are the queue's
-- atomicity boundary; n8n workflows must not implement claim logic as a
-- separate SELECT followed by UPDATE.

CREATE OR REPLACE FUNCTION geogrid_enqueue_request(
    p_request_id UUID,
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
    p_consent_at TIMESTAMPTZ
)
RETURNS TABLE (saved_request_id UUID, saved_status TEXT, duplicate BOOLEAN)
LANGUAGE plpgsql
AS $$
DECLARE
    v_request_id UUID;
    v_fingerprint TEXT;
    v_status TEXT;
    v_event_cap NUMERIC(12, 6);
BEGIN
    INSERT INTO geogrid_requests (
        request_id, idempotency_key, payload_fingerprint, campaign_id,
        contact_name, company_name, email, whatsapp, city, state,
        google_maps_url, keyword, consent_at
    )
    VALUES (
        p_request_id, p_idempotency_key, p_payload_fingerprint, p_campaign_id,
        p_contact_name, p_company_name, p_email, p_whatsapp, p_city, p_state,
        p_google_maps_url, p_keyword, p_consent_at
    )
    ON CONFLICT (idempotency_key) DO NOTHING
    RETURNING request_id, status INTO v_request_id, v_status;

    IF v_request_id IS NOT NULL THEN
        SELECT default_event_cap_usd
        INTO v_event_cap
        FROM geogrid_budget
        WHERE scope_type = 'system' AND scope_key = 'default';

        INSERT INTO geogrid_budget (scope_type, scope_key, cap_amount_usd)
        VALUES ('event', p_campaign_id, v_event_cap)
        ON CONFLICT (scope_type, scope_key) DO NOTHING;

        INSERT INTO geogrid_execution_logs (
            request_id, workflow_name, event_type, details
        )
        VALUES (
            v_request_id, '01-recepcao-leads', 'request_persisted',
            jsonb_build_object('campaign_id', p_campaign_id)
        );
        RETURN QUERY SELECT v_request_id, v_status, FALSE;
        RETURN;
    END IF;

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
        RAISE EXCEPTION 'Idempotency key reused with different request content'
            USING ERRCODE = '23505';
    END IF;

    RETURN QUERY SELECT v_request_id, v_status, TRUE;
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
    v_daily_key TEXT := to_char((now() AT TIME ZONE 'UTC')::date, 'YYYY-MM-DD');
    v_reserved NUMERIC(12, 6);
    v_pending BIGINT;
    v_claims_last_minute BIGINT;
BEGIN
    -- Serializes the rate limiter and budget reservation across all workers.
    PERFORM pg_advisory_xact_lock(1847219, 1);
    SELECT * INTO v_control
    FROM geogrid_budget
    WHERE scope_type = 'system' AND scope_key = 'default'
    FOR UPDATE;

    IF NOT FOUND OR NOT v_control.enabled
       OR v_control.max_pending_tasks IS NULL
       OR v_control.new_diagnostics_per_minute IS NULL
       OR v_control.estimated_task_cost_usd IS NULL
       OR v_control.estimated_task_cost_usd <= 0
       OR v_control.daily_cap_usd IS NULL
       OR v_control.default_event_cap_usd IS NULL THEN
        RETURN;
    END IF;

    -- Safe to release an expired claim only before paid submission has begun.
    UPDATE geogrid_requests
    SET status = 'queued', claim_owner = NULL, claim_token = NULL,
        claim_expires_at = NULL, updated_at = now()
    WHERE status = 'claimed' AND claim_expires_at < now();

    SELECT count(*) INTO v_claims_last_minute
    FROM geogrid_requests
    WHERE claimed_at >= now() - interval '1 minute';
    IF v_claims_last_minute >= v_control.new_diagnostics_per_minute THEN
        RETURN;
    END IF;

    SELECT count(*) INTO v_pending
    FROM geogrid_tasks
    WHERE state IN (
        'submission_started', 'submission_uncertain', 'submitted',
        'polling', 'pending'
    );
    IF v_pending + 9 > v_control.max_pending_tasks THEN
        RETURN;
    END IF;

    SELECT * INTO v_request
    FROM geogrid_requests
    WHERE status = 'queued'
    ORDER BY created_at, request_id
    LIMIT 1
    FOR UPDATE SKIP LOCKED;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    INSERT INTO geogrid_budget (scope_type, scope_key, cap_amount_usd)
    VALUES ('daily', v_daily_key, v_control.daily_cap_usd)
    ON CONFLICT (scope_type, scope_key) DO NOTHING;
    SELECT * INTO v_daily FROM geogrid_budget
    WHERE scope_type = 'daily' AND scope_key = v_daily_key
    FOR UPDATE;
    SELECT * INTO v_event FROM geogrid_budget
    WHERE scope_type = 'event' AND scope_key = v_request.campaign_id
    FOR UPDATE;

    v_reserved := 9 * v_control.estimated_task_cost_usd;
    IF v_daily.cap_amount_usd IS NULL
       OR v_event.cap_amount_usd IS NULL
       OR v_daily.spent_amount_usd + v_daily.reserved_amount_usd + v_reserved
            > v_daily.cap_amount_usd
       OR v_event.spent_amount_usd + v_event.reserved_amount_usd + v_reserved
            > v_event.cap_amount_usd THEN
        UPDATE geogrid_requests
        SET status = 'budget_blocked',
            error_category = 'budget_limit',
            error_message = 'Required daily or campaign budget is unavailable',
            updated_at = now()
        WHERE request_id = v_request.request_id;
        INSERT INTO geogrid_execution_logs (
            request_id, workflow_name, event_type, severity, details
        )
        VALUES (
            v_request.request_id, '02-processador-standard',
            'budget_reservation_denied', 'warning',
            jsonb_build_object('daily_scope', v_daily_key, 'event_scope', v_request.campaign_id)
        );
        RETURN;
    END IF;

    UPDATE geogrid_budget
    SET reserved_amount_usd = reserved_amount_usd + v_reserved,
        updated_at = now()
    WHERE (scope_type, scope_key) IN (
        ('daily', v_daily_key), ('event', v_request.campaign_id)
    );

    UPDATE geogrid_requests
    SET status = 'claimed', claim_owner = p_worker_id,
        claim_token = gen_random_uuid(),
        claim_expires_at = now() + interval '10 minutes',
        claimed_at = now(), attempt_count = attempt_count + 1,
        budget_reserved_usd = v_reserved, updated_at = now(),
        error_category = NULL, error_code = NULL, error_message = NULL
    WHERE request_id = v_request.request_id
    RETURNING * INTO v_request;

    INSERT INTO geogrid_execution_logs (
        request_id, workflow_name, event_type, details
    )
    VALUES (
        v_request.request_id, '02-processador-standard',
        'request_claimed',
        jsonb_build_object('worker_id', p_worker_id, 'reserved_usd', v_reserved)
    );
    RETURN NEXT v_request;
END;
$$;

CREATE OR REPLACE FUNCTION geogrid_prepare_tasks(
    p_request_id UUID,
    p_claim_token UUID,
    p_scan_id TEXT,
    p_business_id TEXT,
    p_center_lat DOUBLE PRECISION,
    p_center_lng DOUBLE PRECISION,
    p_points JSONB
)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    v_request geogrid_requests%ROWTYPE;
    v_count INTEGER;
BEGIN
    SELECT * INTO v_request
    FROM geogrid_requests
    WHERE request_id = p_request_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Queue request not found';
    END IF;
    IF v_request.status = 'submitting' AND v_request.scan_id = p_scan_id THEN
        RETURN TRUE;
    END IF;
    IF v_request.status <> 'claimed'
       OR v_request.claim_token IS DISTINCT FROM p_claim_token
       OR v_request.claim_expires_at < now() THEN
        RAISE EXCEPTION 'Request claim is stale or no longer owned';
    END IF;
    IF jsonb_typeof(p_points) <> 'array'
       OR jsonb_array_length(p_points) <> 9 THEN
        RAISE EXCEPTION 'Exactly nine backend-generated grid points are required';
    END IF;

    SELECT count(*) INTO v_count
    FROM (
        SELECT DISTINCT (point->>'row')::SMALLINT AS r, (point->>'col')::SMALLINT AS c
        FROM jsonb_array_elements(p_points) AS elements(point)
    ) cells
    WHERE r BETWEEN 0 AND 2 AND c BETWEEN 0 AND 2;
    IF v_count <> 9 THEN
        RAISE EXCEPTION 'Grid coordinates must contain each 3x3 cell exactly once';
    END IF;

    INSERT INTO geogrid_tasks (
        request_id, point_index, row_index, col_index, latitude,
        longitude, tag, state
    )
    SELECT
        p_request_id,
        (point->>'row')::SMALLINT * 3 + (point->>'col')::SMALLINT,
        (point->>'row')::SMALLINT,
        (point->>'col')::SMALLINT,
        (point->>'lat')::DOUBLE PRECISION,
        (point->>'lng')::DOUBLE PRECISION,
        p_request_id::TEXT || ':' || (point->>'row') || ':' || (point->>'col'),
        'prepared'
    FROM jsonb_array_elements(p_points) AS elements(point)
    ON CONFLICT (request_id, point_index) DO NOTHING;

    SELECT count(*) INTO v_count
    FROM geogrid_tasks
    WHERE request_id = p_request_id;
    IF v_count <> 9 THEN
        RAISE EXCEPTION 'Persisted grid does not contain exactly nine tasks';
    END IF;

    UPDATE geogrid_tasks
    SET state = 'submission_started', post_attempt_count = post_attempt_count + 1,
        updated_at = now()
    WHERE request_id = p_request_id AND state = 'prepared';

    UPDATE geogrid_requests
    SET status = 'submitting', scan_id = p_scan_id,
        external_business_id = p_business_id,
        business_lat = p_center_lat, business_lng = p_center_lng,
        claim_expires_at = now() + interval '10 minutes',
        updated_at = now()
    WHERE request_id = p_request_id;

    INSERT INTO geogrid_execution_logs (
        request_id, workflow_name, event_type, details
    )
    VALUES (
        p_request_id, '02-processador-standard', 'paid_submission_armed',
        jsonb_build_object('scan_id', p_scan_id, 'point_count', 9)
    );
    RETURN TRUE;
END;
$$;

CREATE OR REPLACE FUNCTION geogrid_mark_submission_uncertain(
    p_request_id UUID,
    p_reason TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE geogrid_requests
    SET status = 'submission_uncertain',
        error_category = 'technical_error',
        error_message = left(coalesce(p_reason, 'Submission outcome is unknown'), 1000),
        claim_owner = NULL, claim_token = NULL,
        claim_expires_at = NULL, updated_at = now()
    WHERE request_id = p_request_id
      AND status IN ('submitting', 'submission_uncertain');

    UPDATE geogrid_tasks
    SET state = 'submission_uncertain', updated_at = now()
    WHERE request_id = p_request_id AND state = 'submission_started';

    INSERT INTO geogrid_execution_logs (
        request_id, workflow_name, event_type, severity, details
    )
    VALUES (
        p_request_id, '02-processador-standard',
        'paid_submission_requires_reconciliation', 'error',
        jsonb_build_object('reason', left(coalesce(p_reason, 'unknown'), 500))
    );
    RETURN FOUND;
END;
$$;

CREATE OR REPLACE FUNCTION geogrid_release_prepaid_reservation(
    p_request_id UUID,
    p_status TEXT,
    p_reason TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    v_request geogrid_requests%ROWTYPE;
    v_daily_key TEXT;
BEGIN
    IF p_status NOT IN ('queued', 'manual_review') THEN
        RAISE EXCEPTION 'Pre-submission release status must be queued or manual_review';
    END IF;
    SELECT * INTO v_request FROM geogrid_requests
    WHERE request_id = p_request_id FOR UPDATE;
    IF NOT FOUND OR v_request.status NOT IN ('claimed', 'business_resolved', 'scan_created') THEN
        RETURN FALSE;
    END IF;
    v_daily_key := to_char((v_request.claimed_at AT TIME ZONE 'UTC')::date, 'YYYY-MM-DD');

    UPDATE geogrid_budget
    SET reserved_amount_usd = greatest(0, reserved_amount_usd - v_request.budget_reserved_usd),
        updated_at = now()
    WHERE (scope_type, scope_key) IN (
        ('daily', v_daily_key), ('event', v_request.campaign_id)
    );
    UPDATE geogrid_requests
    SET status = p_status, claim_owner = NULL, claim_token = NULL,
        claim_expires_at = NULL, budget_reserved_usd = 0,
        error_category = CASE WHEN p_status = 'queued' THEN 'technical_error'
                              ELSE 'business_not_verified' END,
        error_message = left(coalesce(p_reason, 'Pre-submission processing did not complete'), 1000),
        updated_at = now()
    WHERE request_id = p_request_id;
    INSERT INTO geogrid_execution_logs (
        request_id, workflow_name, event_type, severity, details
    )
    VALUES (
        p_request_id, '02-processador-standard',
        CASE WHEN p_status = 'queued' THEN 'safe_pre_submission_retry'
             ELSE 'manual_review_required' END,
        CASE WHEN p_status = 'queued' THEN 'warning' ELSE 'error' END,
        jsonb_build_object('reason', left(coalesce(p_reason, 'unknown'), 500))
    );
    RETURN TRUE;
END;
$$;

CREATE OR REPLACE FUNCTION geogrid_save_post_response(
    p_request_id UUID,
    p_task_results JSONB,
    p_total_cost_usd NUMERIC
)
RETURNS TEXT
LANGUAGE plpgsql
AS $$
DECLARE
    v_request geogrid_requests%ROWTYPE;
    v_daily_key TEXT;
    v_new_status TEXT;
    v_uncertain BOOLEAN;
    v_cost_unknown BOOLEAN;
    v_failed_count INTEGER;
    v_cost NUMERIC(12, 6) := greatest(coalesce(p_total_cost_usd, 0), 0);
BEGIN
    SELECT * INTO v_request FROM geogrid_requests
    WHERE request_id = p_request_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Queue request not found';
    END IF;
    IF v_request.status IN (
        'submitted', 'processing', 'partial', 'done', 'failed', 'submission_uncertain'
    ) AND EXISTS (
        SELECT 1 FROM geogrid_tasks
        WHERE request_id = p_request_id
          AND state IN ('submitted', 'submission_uncertain', 'failed')
    ) THEN
        RETURN v_request.status;
    END IF;
    IF v_request.status <> 'submitting'
       OR jsonb_typeof(p_task_results) <> 'array'
       OR jsonb_array_length(p_task_results) <> 9 THEN
        RAISE EXCEPTION 'Unexpected or incomplete task_post response';
    END IF;

    IF (
        SELECT count(DISTINCT ((item->>'row')::SMALLINT * 3 + (item->>'col')::SMALLINT))
        FROM jsonb_array_elements(p_task_results) AS elements(item)
        WHERE (item->>'row')::SMALLINT BETWEEN 0 AND 2
          AND (item->>'col')::SMALLINT BETWEEN 0 AND 2
    ) <> 9 THEN
        RAISE EXCEPTION 'task_post response must map exactly once to every grid cell';
    END IF;

    UPDATE geogrid_tasks t
    SET task_id = nullif(item->>'task_id', ''),
        dataforseo_status_code = nullif(item->>'status_code', '')::INTEGER,
        dataforseo_status_message = left(item->>'status_message', 1000),
        task_cost_usd = greatest(coalesce(nullif(item->>'cost', '')::NUMERIC, 0), 0),
        error_category = nullif(item->>'error_category', ''),
        error_message = left(item->>'error_message', 1000),
        state = CASE
            WHEN nullif(item->>'task_id', '') IS NOT NULL THEN 'submitted'
            WHEN nullif(item->>'status_code', '')::INTEGER IN (401, 402)
                 OR nullif(item->>'status_code', '')::INTEGER BETWEEN 40000 AND 49999
                THEN 'failed'
            ELSE 'submission_uncertain'
        END,
        next_poll_at = CASE WHEN nullif(item->>'task_id', '') IS NOT NULL
                            THEN now() ELSE NULL END,
        updated_at = now()
    FROM jsonb_array_elements(p_task_results) AS elements(item)
    WHERE t.request_id = p_request_id
      AND t.row_index = (item->>'row')::SMALLINT
      AND t.col_index = (item->>'col')::SMALLINT;

    SELECT bool_or(state = 'submission_uncertain'),
           count(*) FILTER (WHERE state = 'failed')
    INTO v_uncertain, v_failed_count
    FROM geogrid_tasks WHERE request_id = p_request_id;
    v_uncertain := coalesce(v_uncertain, TRUE);
    v_cost_unknown := p_total_cost_usd IS NULL;

    v_new_status := CASE
        WHEN coalesce(v_uncertain, TRUE) THEN 'submission_uncertain'
        WHEN v_failed_count = 9 THEN 'failed'
        WHEN v_failed_count > 0 THEN 'partial'
        ELSE 'submitted'
    END;

    v_daily_key := to_char((v_request.claimed_at AT TIME ZONE 'UTC')::date, 'YYYY-MM-DD');
    UPDATE geogrid_budget
    SET reserved_amount_usd = CASE WHEN v_uncertain OR v_cost_unknown THEN reserved_amount_usd
                                   ELSE greatest(0, reserved_amount_usd - v_request.budget_reserved_usd) END,
        spent_amount_usd = spent_amount_usd + v_cost, updated_at = now()
    WHERE (scope_type, scope_key) IN (
        ('daily', v_daily_key), ('event', v_request.campaign_id)
    );

    UPDATE geogrid_requests
    SET status = v_new_status, actual_cost_usd = v_cost,
        budget_reserved_usd = CASE
            WHEN v_uncertain OR v_cost_unknown THEN budget_reserved_usd ELSE 0 END,
        claim_owner = NULL, claim_token = NULL, claim_expires_at = NULL,
        next_poll_at = CASE WHEN v_new_status = 'submitted'
                            THEN now() + interval '1 minute' ELSE now() END,
        finished_at = CASE WHEN v_new_status = 'failed' THEN now() ELSE NULL END,
        updated_at = now()
    WHERE request_id = p_request_id;

    INSERT INTO geogrid_execution_logs (
        request_id, workflow_name, event_type, severity, details
    )
    VALUES (
        p_request_id, '02-processador-standard', 'task_post_response_saved',
        CASE WHEN v_uncertain OR v_cost_unknown THEN 'warning' ELSE 'info' END,
        jsonb_build_object(
            'status', v_new_status, 'actual_cost_usd', v_cost,
            'cost_known', NOT v_cost_unknown,
            'task_count', 9, 'failed_count', v_failed_count
        )
    );
    RETURN v_new_status;
END;
$$;

CREATE OR REPLACE FUNCTION geogrid_claim_pending_tasks(
    p_worker_id TEXT,
    p_limit INTEGER DEFAULT 20
)
RETURNS TABLE (
    request_id UUID,
    scan_id TEXT,
    business_id TEXT,
    task_id TEXT,
    row_index SMALLINT,
    col_index SMALLINT,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    claim_token UUID
)
LANGUAGE plpgsql
AS $$
BEGIN
    IF p_limit < 1 OR p_limit > 100 THEN
        RAISE EXCEPTION 'Poll batch limit must be between 1 and 100';
    END IF;

    UPDATE geogrid_tasks
    SET state = 'submitted', poll_owner = NULL, poll_token = NULL,
        poll_expires_at = NULL, updated_at = now()
    WHERE state = 'polling' AND poll_expires_at < now();

    RETURN QUERY
    WITH selected AS (
        SELECT t.task_row_id
        FROM geogrid_tasks t
        JOIN geogrid_requests r USING (request_id)
        WHERE t.state IN ('submitted', 'pending')
          AND t.task_id IS NOT NULL
          AND coalesce(t.next_poll_at, now()) <= now()
          AND r.status IN ('submitted', 'processing', 'partial')
        ORDER BY coalesce(t.next_poll_at, now()), t.task_row_id
        LIMIT p_limit
        FOR UPDATE OF t SKIP LOCKED
    ),
    claimed AS (
        UPDATE geogrid_tasks t
        SET state = 'polling', poll_owner = p_worker_id,
            poll_token = gen_random_uuid(),
            poll_expires_at = now() + interval '5 minutes',
            poll_attempt_count = poll_attempt_count + 1,
            updated_at = now()
        FROM selected s
        WHERE t.task_row_id = s.task_row_id
        RETURNING t.*
    )
    SELECT c.request_id, r.scan_id, r.external_business_id, c.task_id,
           c.row_index, c.col_index, c.latitude, c.longitude, c.poll_token
    FROM claimed c
    JOIN geogrid_requests r USING (request_id);
END;
$$;

CREATE OR REPLACE FUNCTION geogrid_record_task_result(
    p_request_id UUID,
    p_row SMALLINT,
    p_col SMALLINT,
    p_task_id TEXT,
    p_poll_token UUID,
    p_state TEXT,
    p_status_code INTEGER,
    p_status_message TEXT,
    p_error_category TEXT,
    p_error_message TEXT,
    p_results JSONB
)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    v_task geogrid_tasks%ROWTYPE;
BEGIN
    IF p_state NOT IN ('pending', 'done', 'no_results', 'failed') THEN
        RAISE EXCEPTION 'Invalid normalized task state';
    END IF;

    UPDATE geogrid_tasks
    SET state = p_state,
        dataforseo_status_code = p_status_code,
        dataforseo_status_message = left(p_status_message, 1000),
        error_category = p_error_category,
        error_message = left(p_error_message, 1000),
        results = CASE WHEN p_state = 'done'
                       THEN coalesce(p_results, '[]'::jsonb) ELSE '[]'::jsonb END,
        next_poll_at = CASE WHEN p_state = 'pending'
                            THEN now() + interval '1 minute' ELSE NULL END,
        smartgrid_reported_at = CASE WHEN p_state = 'pending'
                                     THEN smartgrid_reported_at ELSE now() END,
        poll_owner = NULL, poll_token = NULL, poll_expires_at = NULL,
        resolved_at = CASE WHEN p_state = 'pending' THEN NULL ELSE now() END,
        updated_at = now()
    WHERE request_id = p_request_id AND row_index = p_row AND col_index = p_col
      AND task_id = p_task_id AND poll_token = p_poll_token
      AND state = 'polling'
    RETURNING * INTO v_task;

    IF NOT FOUND THEN
        RETURN FALSE;
    END IF;

    UPDATE geogrid_requests
    SET status = CASE
            WHEN status = 'submitted' THEN 'processing'
            WHEN status = 'processing' AND p_state = 'failed'
                THEN 'partial'
            ELSE status
        END,
        updated_at = now()
    WHERE request_id = p_request_id;

    INSERT INTO geogrid_execution_logs (
        request_id, workflow_name, event_type, severity, details
    )
    VALUES (
        p_request_id, '03-recuperacao-resultados', 'task_result_recorded',
        CASE WHEN p_state = 'failed' THEN 'warning' ELSE 'info' END,
        jsonb_build_object(
            'row', p_row, 'col', p_col, 'state', p_state,
            'status_code', p_status_code
        )
    );
    RETURN TRUE;
END;
$$;

CREATE OR REPLACE FUNCTION geogrid_list_unreported_failures(p_limit INTEGER DEFAULT 100)
RETURNS TABLE (
    request_id UUID,
    scan_id TEXT,
    row_index SMALLINT,
    col_index SMALLINT,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    task_id TEXT,
    status_code INTEGER,
    status_message TEXT,
    error_category TEXT,
    error_message TEXT
)
LANGUAGE sql
STABLE
AS $$
    SELECT r.request_id, r.scan_id, t.row_index, t.col_index,
           t.latitude, t.longitude, t.task_id, t.dataforseo_status_code,
           t.dataforseo_status_message, t.error_category, t.error_message
    FROM geogrid_tasks t
    JOIN geogrid_requests r USING (request_id)
    WHERE t.state = 'failed' AND t.smartgrid_reported_at IS NULL
      AND r.scan_id IS NOT NULL
    ORDER BY t.created_at, t.task_row_id
    LIMIT greatest(1, least(p_limit, 500));
$$;

CREATE OR REPLACE FUNCTION geogrid_mark_smartgrid_reported(
    p_request_id UUID,
    p_row SMALLINT,
    p_col SMALLINT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE geogrid_tasks
    SET smartgrid_reported_at = now(), updated_at = now()
    WHERE request_id = p_request_id AND row_index = p_row AND col_index = p_col
      AND state = 'failed' AND smartgrid_reported_at IS NULL;
    RETURN FOUND;
END;
$$;

CREATE OR REPLACE FUNCTION geogrid_mark_failed_points_reported(
    p_request_id UUID,
    p_points JSONB
)
RETURNS INTEGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_updated INTEGER;
BEGIN
    UPDATE geogrid_tasks t
    SET smartgrid_reported_at = now(), updated_at = now()
    FROM jsonb_to_recordset(p_points) AS p(row_index SMALLINT, col_index SMALLINT)
    WHERE t.request_id = p_request_id
      AND t.row_index = p.row_index AND t.col_index = p.col_index
      AND t.state = 'failed' AND t.smartgrid_reported_at IS NULL;
    GET DIAGNOSTICS v_updated = ROW_COUNT;
    RETURN v_updated;
END;
$$;

CREATE OR REPLACE FUNCTION geogrid_ready_to_finalize(p_request_id UUID)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
AS $$
    SELECT count(*) = 9
       AND count(*) FILTER (WHERE state IN ('done', 'no_results', 'failed')) = 9
    FROM geogrid_tasks
    WHERE request_id = p_request_id;
$$;

CREATE OR REPLACE FUNCTION geogrid_finalize_request(p_request_id UUID)
RETURNS TEXT
LANGUAGE plpgsql
AS $$
DECLARE
    v_request geogrid_requests%ROWTYPE;
    v_total INTEGER;
    v_terminal INTEGER;
    v_success INTEGER;
    v_failed INTEGER;
    v_daily_key TEXT;
BEGIN
    SELECT * INTO v_request
    FROM geogrid_requests WHERE request_id = p_request_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Queue request not found';
    END IF;
    IF v_request.status IN ('done', 'partial', 'failed') THEN
        RETURN v_request.status;
    END IF;

    SELECT count(*),
           count(*) FILTER (WHERE state IN ('done', 'no_results', 'failed')),
           count(*) FILTER (WHERE state IN ('done', 'no_results')),
           count(*) FILTER (WHERE state = 'failed')
    INTO v_total, v_terminal, v_success, v_failed
    FROM geogrid_tasks WHERE request_id = p_request_id;
    IF v_total <> 9 OR v_terminal <> 9 THEN
        RAISE EXCEPTION 'Cannot finalize while points are unresolved';
    END IF;

    UPDATE geogrid_requests
    SET status = CASE
            WHEN v_failed = 9 THEN 'failed'
            WHEN v_failed > 0 THEN 'partial'
            ELSE 'done'
        END,
        finished_at = now(), updated_at = now()
    WHERE request_id = p_request_id
    RETURNING status INTO v_request.status;

    v_daily_key := to_char((v_request.claimed_at AT TIME ZONE 'UTC')::date, 'YYYY-MM-DD');
    INSERT INTO geogrid_execution_logs (
        request_id, workflow_name, event_type, details
    )
    VALUES (
        p_request_id, '03-recuperacao-resultados', 'request_finalized',
        jsonb_build_object(
            'status', v_request.status, 'successful_or_empty_points', v_success,
            'failed_points', v_failed, 'actual_cost_usd', v_request.actual_cost_usd
        )
    );
    RETURN v_request.status;
END;
$$;

CREATE OR REPLACE FUNCTION geogrid_recover_expired_work()
RETURNS TABLE (requeued_claims INTEGER, uncertain_submissions INTEGER)
LANGUAGE plpgsql
AS $$
DECLARE
    v_requeued INTEGER;
    v_uncertain INTEGER;
BEGIN
    UPDATE geogrid_requests
    SET status = 'queued', claim_owner = NULL, claim_token = NULL,
        claim_expires_at = NULL, updated_at = now()
    WHERE status = 'claimed' AND claim_expires_at < now();
    GET DIAGNOSTICS v_requeued = ROW_COUNT;

    UPDATE geogrid_tasks
    SET state = 'submission_uncertain', updated_at = now()
    WHERE state = 'submission_started'
      AND request_id IN (
          SELECT request_id FROM geogrid_requests
          WHERE status = 'submitting' AND claim_expires_at < now()
      );
    UPDATE geogrid_requests
    SET status = 'submission_uncertain',
        error_category = 'technical_error',
        error_message = 'Worker lease expired after paid submission was armed; manual reconciliation required',
        claim_owner = NULL, claim_token = NULL,
        claim_expires_at = NULL, updated_at = now()
    WHERE status = 'submitting' AND claim_expires_at < now();
    GET DIAGNOSTICS v_uncertain = ROW_COUNT;

    RETURN QUERY SELECT v_requeued, v_uncertain;
END;
$$;
