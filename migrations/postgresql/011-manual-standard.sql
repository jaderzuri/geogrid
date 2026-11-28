BEGIN;

ALTER TABLE geogrid_budget
    ADD COLUMN IF NOT EXISTS estimated_priority_task_cost_usd NUMERIC(12,6)
        CHECK (estimated_priority_task_cost_usd > 0),
    ADD COLUMN IF NOT EXISTS manual_standard_enabled BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE geogrid_requests
    ADD COLUMN IF NOT EXISTS manual_business_id BIGINT,
    ADD COLUMN IF NOT EXISTS manual_batch_id UUID,
    ADD COLUMN IF NOT EXISTS manual_unit_cost_ceiling_usd NUMERIC(12,6),
    ADD COLUMN IF NOT EXISTS grid_size SMALLINT NOT NULL DEFAULT 3,
    ADD COLUMN IF NOT EXISTS spacing_km DOUBLE PRECISION NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS standard_priority SMALLINT NOT NULL DEFAULT 1;
ALTER TABLE geogrid_requests DROP CONSTRAINT IF EXISTS geogrid_requests_total_points_check;
ALTER TABLE geogrid_requests ADD CONSTRAINT geogrid_requests_total_points_check
    CHECK (grid_size IN (3,5,7,9,11,13) AND total_points = grid_size * grid_size
        AND spacing_km > 0 AND spacing_km <= 50 AND standard_priority IN (1,2)
        AND ((manual_business_id IS NULL AND manual_batch_id IS NULL
              AND grid_size = 3 AND spacing_km = 1 AND standard_priority = 1)
          OR (manual_business_id IS NOT NULL AND manual_business_id > 0 AND manual_batch_id IS NOT NULL
              AND total_points <= 100
              AND manual_unit_cost_ceiling_usd IS NOT NULL AND manual_unit_cost_ceiling_usd > 0)));
ALTER TABLE geogrid_tasks ADD COLUMN IF NOT EXISTS grid_size SMALLINT NOT NULL DEFAULT 3;
ALTER TABLE geogrid_tasks DROP CONSTRAINT IF EXISTS geogrid_tasks_check,
    DROP CONSTRAINT IF EXISTS geogrid_tasks_point_index_check,
    DROP CONSTRAINT IF EXISTS geogrid_tasks_row_index_check,
    DROP CONSTRAINT IF EXISTS geogrid_tasks_col_index_check;
ALTER TABLE geogrid_tasks ADD CONSTRAINT geogrid_tasks_point_index_check CHECK (point_index BETWEEN 0 AND 168),
    ADD CONSTRAINT geogrid_tasks_row_index_check CHECK (row_index BETWEEN 0 AND 12),
    ADD CONSTRAINT geogrid_tasks_col_index_check CHECK (col_index BETWEEN 0 AND 12),
    ADD CONSTRAINT geogrid_tasks_check CHECK (
        grid_size IN (3,5,7,9,11,13) AND row_index < grid_size AND col_index < grid_size
        AND point_index = row_index * grid_size + col_index
    );

CREATE TABLE IF NOT EXISTS geogrid_manual_standard_batches (
    batch_id UUID PRIMARY KEY,
    operator_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS geogrid_requests_manual_batch_idx ON geogrid_requests(manual_batch_id)
    WHERE manual_batch_id IS NOT NULL;

CREATE OR REPLACE FUNCTION geogrid_capture_crm_outbox_event()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_event_type TEXT;
    v_event_key TEXT;
BEGIN
    IF NEW.manual_batch_id IS NOT NULL THEN RETURN NEW; END IF;
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
    v_unit_cost NUMERIC(12, 6);
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
    SELECT v_pending + coalesce(sum(total_points), 0) INTO v_pending
    FROM geogrid_requests WHERE status IN ('claimed','scan_created')
        AND budget_reservation_state = 'reserved';

    SELECT * INTO v_request FROM geogrid_requests
    WHERE status = 'queued' AND task_post_authorization_count = 0
      AND budget_reserved_usd = 0
      AND (next_attempt_at IS NULL OR next_attempt_at <= now())
      AND attempt_count < v_policy.max_attempts
      AND total_points + v_pending <= v_control.max_pending_tasks
      AND (manual_batch_id IS NULL OR v_control.manual_standard_enabled)
      AND (standard_priority = 1 OR v_control.estimated_priority_task_cost_usd > 0)
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

    v_unit_cost := CASE v_request.standard_priority
        WHEN 2 THEN v_control.estimated_priority_task_cost_usd
        ELSE v_control.estimated_task_cost_usd END;
    v_reserved := v_request.total_points * v_unit_cost;
    IF v_request.manual_batch_id IS NOT NULL
       AND v_unit_cost > v_request.manual_unit_cost_ceiling_usd THEN
        UPDATE geogrid_requests SET status = 'budget_blocked',
            error_category = 'budget_limit', error_message = 'Quoted unit price exceeded',
            updated_at = now() WHERE request_id = v_request.request_id;
        RETURN;
    END IF;
    IF v_daily.cap_amount_usd IS NULL OR v_event.cap_amount_usd IS NULL
       OR v_daily.spent_amount_usd + v_daily.reserved_amount_usd + v_reserved > least(v_daily.cap_amount_usd, v_control.daily_cap_usd)
       OR v_event.spent_amount_usd + v_event.reserved_amount_usd + v_reserved > least(v_event.cap_amount_usd, v_control.default_event_cap_usd) THEN
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
    SELECT * INTO v_request FROM geogrid_requests
    WHERE request_id = p_request_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Queue request not found';
    END IF;
    IF v_request.status <> 'claimed'
       OR v_request.claim_token IS DISTINCT FROM p_claim_token
       OR v_request.claim_expires_at IS NULL
       OR v_request.claim_expires_at <= now()
       OR v_request.task_post_authorization_count <> 0
       OR v_request.budget_reservation_state <> 'reserved' THEN
        RAISE EXCEPTION 'Request claim is stale, unauthorized, or no longer owned';
    END IF;
    IF coalesce(p_scan_id, '') = '' OR coalesce(p_business_id, '') = ''
       OR p_center_lat NOT BETWEEN -90 AND 90
       OR p_center_lng NOT BETWEEN -180 AND 180 THEN
        RAISE EXCEPTION 'Verified scan and business coordinates are required';
    END IF;
    IF jsonb_typeof(p_points) <> 'array'
       OR jsonb_array_length(p_points) <> v_request.total_points THEN
        RAISE EXCEPTION 'Grid size must match the reserved point count';
    END IF;

    SELECT count(*) INTO v_count
    FROM (
        SELECT DISTINCT (point->>'row')::SMALLINT AS r,
                        (point->>'col')::SMALLINT AS c
        FROM jsonb_array_elements(p_points) AS elements(point)
    ) cells
    WHERE r BETWEEN 0 AND (v_request.grid_size - 1) AND c BETWEEN 0 AND (v_request.grid_size - 1);
    IF v_count <> v_request.total_points THEN
        RAISE EXCEPTION 'Grid coordinates must contain each grid cell exactly once';
    END IF;
    IF EXISTS (SELECT 1 FROM geogrid_tasks WHERE request_id = p_request_id) THEN
        RAISE EXCEPTION 'Tasks were already prepared for this request';
    END IF;

    INSERT INTO geogrid_tasks (
        request_id, point_index, row_index, col_index, latitude,
        longitude, tag, state, grid_size
    )
    SELECT p_request_id,
           (point->>'row')::SMALLINT * v_request.grid_size + (point->>'col')::SMALLINT,
           (point->>'row')::SMALLINT, (point->>'col')::SMALLINT,
           (point->>'lat')::DOUBLE PRECISION, (point->>'lng')::DOUBLE PRECISION,
           p_request_id::TEXT || ':' || (point->>'row') || ':' || (point->>'col'),
           'prepared', v_request.grid_size
    FROM jsonb_array_elements(p_points) AS elements(point);

    UPDATE geogrid_requests
    SET status = 'scan_created', scan_id = p_scan_id,
        external_business_id = p_business_id,
        business_lat = p_center_lat, business_lng = p_center_lng,
        claim_expires_at = now() + interval '10 minutes', updated_at = now()
    WHERE request_id = p_request_id;
    INSERT INTO geogrid_execution_logs (
        request_id, workflow_name, event_type, details
    )
    VALUES (
        p_request_id, '02-processador-standard', 'grid_prepared',
        jsonb_build_object('scan_id', p_scan_id, 'point_count', v_request.total_points)
    );
    RETURN TRUE;
END;
$$;

CREATE OR REPLACE FUNCTION geogrid_authorize_task_post(
    p_request_id UUID,
    p_claim_token UUID,
    p_scan_id TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    v_request geogrid_requests%ROWTYPE;
    v_count INTEGER;
BEGIN
    PERFORM pg_advisory_xact_lock(1847219, 1);
    SELECT * INTO v_request FROM geogrid_requests
    WHERE request_id = p_request_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Queue request not found';
    END IF;
    IF v_request.status <> 'scan_created'
       OR v_request.scan_id IS DISTINCT FROM p_scan_id
       OR v_request.claim_token IS DISTINCT FROM p_claim_token
       OR v_request.claim_expires_at IS NULL
       OR v_request.claim_expires_at <= now()
       OR v_request.task_post_authorization_count <> 0
       OR v_request.task_post_authorized_at IS NOT NULL
       OR v_request.budget_reservation_state <> 'reserved'
       OR v_request.budget_reserved_usd <= 0 THEN
        RAISE EXCEPTION 'Paid task_post is not authorized for this live claim';
    END IF;

    IF v_request.manual_batch_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM geogrid_budget AS sys
        JOIN geogrid_budget AS day ON day.scope_type = 'daily'
            AND day.scope_key = v_request.budget_reserved_scope_daily_key
        JOIN geogrid_budget AS event ON event.scope_type = 'event' AND event.scope_key = v_request.campaign_id
        WHERE sys.scope_type = 'system' AND sys.scope_key = 'default'
          AND sys.enabled AND sys.manual_standard_enabled
          AND day.scope_key = to_char((now() AT TIME ZONE 'UTC')::date, 'YYYY-MM-DD')
          AND day.cap_amount_usd IS NOT NULL AND sys.daily_cap_usd IS NOT NULL
          AND event.cap_amount_usd IS NOT NULL AND sys.default_event_cap_usd IS NOT NULL
          AND day.spent_amount_usd + day.reserved_amount_usd <= least(day.cap_amount_usd, sys.daily_cap_usd)
          AND event.spent_amount_usd + event.reserved_amount_usd <= least(event.cap_amount_usd, sys.default_event_cap_usd)
          AND CASE v_request.standard_priority WHEN 2 THEN sys.estimated_priority_task_cost_usd
                ELSE sys.estimated_task_cost_usd END BETWEEN 0.000001 AND
                    v_request.budget_reserved_usd / v_request.total_points
    ) THEN
        RAISE EXCEPTION 'Manual Standard financial approval is no longer valid';
    END IF;

    SELECT count(*) INTO v_count FROM geogrid_tasks
    WHERE request_id = p_request_id AND state = 'prepared' AND task_id IS NULL;
    IF v_count <> v_request.total_points OR EXISTS (
        SELECT 1 FROM geogrid_tasks
        WHERE request_id = p_request_id AND state <> 'prepared'
    ) THEN
        RAISE EXCEPTION 'The reserved grid must have exactly one unsubmitted task per cell';
    END IF;

    UPDATE geogrid_tasks
    SET state = 'submission_started', post_attempt_count = 1, updated_at = now()
    WHERE request_id = p_request_id AND state = 'prepared';
    UPDATE geogrid_requests
    SET status = 'submitting',
        task_post_authorized_at = now(),
        task_post_authorized_claim_token = p_claim_token,
        task_post_authorization_count = 1,
        updated_at = now()
    WHERE request_id = p_request_id;
    INSERT INTO geogrid_execution_logs (
        request_id, workflow_name, event_type, details
    )
    VALUES (
        p_request_id, '02-processador-standard', 'paid_submission_authorized',
        jsonb_build_object('scan_id', p_scan_id, 'point_count', v_request.total_points,
                           'authorization_count', 1)
    );
    RETURN TRUE;
END;
$$;

CREATE OR REPLACE FUNCTION geogrid_save_post_response(
    p_request_id UUID,
    p_claim_token UUID,
    p_task_results JSONB,
    p_total_cost_usd NUMERIC
)
RETURNS TEXT
LANGUAGE plpgsql
AS $$
DECLARE
    v_request geogrid_requests%ROWTYPE;
    v_new_status TEXT;
    v_uncertain BOOLEAN;
    v_cost_known BOOLEAN := p_total_cost_usd IS NOT NULL;
    v_cost NUMERIC(12, 6);
    v_failed_count INTEGER;
    v_updated INTEGER;
BEGIN
    SELECT * INTO v_request FROM geogrid_requests
    WHERE request_id = p_request_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Queue request not found';
    END IF;
    IF v_request.task_post_response_saved_at IS NOT NULL
       AND v_request.task_post_authorized_claim_token = p_claim_token THEN
        IF v_request.task_post_response_digest IS DISTINCT FROM
           md5(p_task_results::text || ':' || coalesce(p_total_cost_usd::text, 'unknown')) THEN
            RAISE EXCEPTION 'Conflicting repeated task_post response';
        END IF;
        RETURN v_request.status;
    END IF;
    IF v_request.status NOT IN ('submitting', 'submission_uncertain')
       OR v_request.task_post_authorization_count <> 1
       OR v_request.task_post_authorized_claim_token IS DISTINCT FROM p_claim_token
       OR p_claim_token IS NULL
       OR v_request.task_post_response_saved_at IS NOT NULL
       OR jsonb_typeof(p_task_results) <> 'array'
       OR jsonb_array_length(p_task_results) <> v_request.total_points THEN
        RAISE EXCEPTION 'Task response cannot be persisted by this claim';
    END IF;
    IF p_total_cost_usd IS NOT NULL
       AND (p_total_cost_usd < 0 OR p_total_cost_usd > 999999.999999
            OR p_total_cost_usd IN ('NaN'::NUMERIC, 'Infinity'::NUMERIC)
            OR p_total_cost_usd <> round(p_total_cost_usd, 6)) THEN
        RAISE EXCEPTION 'DataForSEO total cost must fit the supported USD precision';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM jsonb_array_elements(p_task_results) AS elements(item)
        WHERE nullif(item->>'cost', '') IS NOT NULL
          AND (
              (item->>'cost')::NUMERIC < 0
              OR (item->>'cost')::NUMERIC IN ('NaN'::NUMERIC, 'Infinity'::NUMERIC)
          )
    ) THEN
        RAISE EXCEPTION 'DataForSEO task cost must be a finite non-negative amount';
    END IF;
    IF (
        SELECT count(DISTINCT ((item->>'row')::SMALLINT * v_request.grid_size +
                               (item->>'col')::SMALLINT))
        FROM jsonb_array_elements(p_task_results) AS elements(item)
        WHERE (item->>'row')::SMALLINT BETWEEN 0 AND (v_request.grid_size - 1)
          AND (item->>'col')::SMALLINT BETWEEN 0 AND (v_request.grid_size - 1)
    ) <> v_request.total_points THEN
        RAISE EXCEPTION 'task_post response must map exactly once to every grid cell';
    END IF;

    v_cost := CASE WHEN v_cost_known
                   THEN greatest(p_total_cost_usd, 0) ELSE 0 END;
    UPDATE geogrid_tasks t
    SET task_id = nullif(item->>'task_id', ''),
        dataforseo_status_code = nullif(item->>'status_code', '')::INTEGER,
        dataforseo_status_message = left(item->>'status_message', 1000),
        task_cost_usd = CASE
            WHEN nullif(item->>'cost', '') IS NULL THEN 0
            ELSE greatest((item->>'cost')::NUMERIC, 0)
        END,
        task_cost_known = nullif(item->>'cost', '') IS NOT NULL,
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
      AND t.col_index = (item->>'col')::SMALLINT
      AND t.state IN ('submission_started', 'submission_uncertain');
    GET DIAGNOSTICS v_updated = ROW_COUNT;
    IF v_updated <> v_request.total_points THEN
        RAISE EXCEPTION 'Expected the complete grid of atomically armed tasks for response persistence';
    END IF;

    SELECT bool_or(state = 'submission_uncertain'),
           count(*) FILTER (WHERE state = 'failed')
    INTO v_uncertain, v_failed_count
    FROM geogrid_tasks WHERE request_id = p_request_id;
    v_uncertain := coalesce(v_uncertain, TRUE);
    v_new_status := CASE
        WHEN v_uncertain THEN 'submission_uncertain'
        WHEN v_failed_count = v_request.total_points THEN 'failed'
        WHEN v_failed_count > 0 THEN 'partial'
        ELSE 'submitted'
    END;

    IF v_cost_known AND v_request.budget_reserved_scope_daily_key IS NOT NULL THEN
        UPDATE geogrid_budget
        SET spent_amount_usd = spent_amount_usd + v_cost,
            reserved_amount_usd = CASE
                WHEN NOT v_uncertain
                    THEN reserved_amount_usd - v_request.budget_reserved_usd
                ELSE reserved_amount_usd
            END,
            updated_at = now()
        WHERE (scope_type, scope_key) IN (
            ('daily', v_request.budget_reserved_scope_daily_key),
            ('event', v_request.campaign_id)
        )
          AND (
              v_uncertain
              OR reserved_amount_usd >= v_request.budget_reserved_usd
          );
        GET DIAGNOSTICS v_updated = ROW_COUNT;
        IF v_updated <> 2 THEN
            RAISE EXCEPTION 'Could not reconcile known cost in both budget scopes';
        END IF;
    END IF;

    UPDATE geogrid_requests
    SET status = v_new_status,
        actual_cost_usd = v_cost,
        actual_cost_known = v_cost_known,
        actual_cost_accounted = v_cost_known
            AND v_request.budget_reserved_scope_daily_key IS NOT NULL,
        budget_reserved_usd = CASE
            WHEN v_uncertain OR NOT v_cost_known
                 OR v_request.budget_reserved_scope_daily_key IS NULL THEN budget_reserved_usd
            ELSE 0
        END,
        budget_reservation_state = CASE
            WHEN v_request.budget_reservation_state = 'legacy_unreconciled'
                THEN 'legacy_unreconciled'
            WHEN v_uncertain THEN 'held_uncertain'
            WHEN NOT v_cost_known THEN 'held_unknown_cost'
            ELSE 'consumed'
        END,
        task_post_response_saved_at = now(),
        task_post_response_digest =
            md5(p_task_results::text || ':' || coalesce(p_total_cost_usd::text, 'unknown')),
        claim_owner = NULL, claim_token = NULL, claim_expires_at = NULL,
        next_poll_at = CASE WHEN v_new_status = 'submitted'
                            THEN now() + interval '1 minute' ELSE now() END,
        finished_at = CASE WHEN v_new_status = 'failed' THEN now() ELSE NULL END,
        updated_at = now()
    WHERE request_id = p_request_id;
    IF NOT v_uncertain AND v_cost_known
       AND v_request.budget_reserved_scope_daily_key IS NOT NULL THEN
        UPDATE geogrid_requests
        SET budget_reserved_scope_daily_key = NULL
        WHERE request_id = p_request_id;
    END IF;

    INSERT INTO geogrid_execution_logs (
        request_id, workflow_name, event_type, severity, details
    )
    VALUES (
        p_request_id, '02-processador-standard', 'task_post_response_saved',
        CASE WHEN v_uncertain OR NOT v_cost_known THEN 'warning' ELSE 'info' END,
        jsonb_build_object(
            'status', v_new_status, 'actual_cost_usd', v_cost,
            'cost_known', v_cost_known, 'task_count', v_request.total_points,
            'failed_count', v_failed_count
        )
    );
    RETURN v_new_status;
END;
$$;

CREATE OR REPLACE FUNCTION geogrid_reconcile_post_cost(
    p_request_id UUID, p_total_cost_usd NUMERIC, p_evidence_reference TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    v_request geogrid_requests%ROWTYPE;
    v_updated INTEGER;
BEGIN
    PERFORM pg_advisory_xact_lock(1847219, 1);
    IF p_total_cost_usd IS NULL OR p_total_cost_usd < 0
       OR p_total_cost_usd > 999999.999999
       OR p_total_cost_usd IN ('NaN'::NUMERIC, 'Infinity'::NUMERIC)
       OR p_total_cost_usd <> round(p_total_cost_usd, 6)
       OR length(trim(coalesce(p_evidence_reference, ''))) < 8 THEN
        RAISE EXCEPTION 'Valid cost and evidence reference required';
    END IF;
    SELECT * INTO v_request FROM geogrid_requests
    WHERE request_id = p_request_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Queue request not found';
    END IF;
    IF v_request.actual_cost_accounted THEN
        IF v_request.actual_cost_usd <> p_total_cost_usd THEN
            RAISE EXCEPTION 'Conflicting already reconciled cost';
        END IF;
        RETURN TRUE;
    END IF;
    IF v_request.actual_cost_known AND v_request.actual_cost_usd <> p_total_cost_usd THEN
        RAISE EXCEPTION 'Conflicting previously recorded cost';
    END IF;
    IF v_request.task_post_response_saved_at IS NULL
       OR v_request.budget_reservation_state <> 'held_unknown_cost'
       OR v_request.budget_reserved_scope_daily_key IS NULL
       OR (SELECT count(*) FROM geogrid_tasks WHERE request_id = p_request_id) <> v_request.total_points
       OR EXISTS (SELECT 1 FROM geogrid_tasks
                  WHERE request_id = p_request_id
                    AND task_id IS NULL AND state <> 'failed') THEN
        RAISE EXCEPTION 'Cost cannot be reconciled without a complete response and verified reservation';
    END IF;
    UPDATE geogrid_budget
    SET spent_amount_usd = spent_amount_usd + p_total_cost_usd,
        reserved_amount_usd = reserved_amount_usd - v_request.budget_reserved_usd,
        updated_at = now()
    WHERE (scope_type, scope_key) IN (
        ('daily', v_request.budget_reserved_scope_daily_key),
        ('event', v_request.campaign_id)
    ) AND reserved_amount_usd >= v_request.budget_reserved_usd;
    GET DIAGNOSTICS v_updated = ROW_COUNT;
    IF v_updated <> 2 THEN
        RAISE EXCEPTION 'Could not reconcile cost in both budget scopes';
    END IF;
    UPDATE geogrid_requests
    SET actual_cost_usd = p_total_cost_usd, actual_cost_known = TRUE,
        actual_cost_accounted = TRUE,
        budget_reserved_usd = 0, budget_reservation_state = 'consumed',
        budget_reserved_scope_daily_key = NULL, updated_at = now()
    WHERE request_id = p_request_id;
    INSERT INTO geogrid_execution_logs(request_id, workflow_name, event_type, details)
    VALUES (p_request_id, 'financial-reconciliation', 'post_cost_reconciled',
        jsonb_build_object('actual_cost_usd', p_total_cost_usd,
                           'evidence_reference', left(p_evidence_reference, 200)));
    RETURN TRUE;
END;
$$;

CREATE OR REPLACE FUNCTION geogrid_ready_to_finalize(p_request_id UUID)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
AS $$
    SELECT count(*) = (SELECT total_points FROM geogrid_requests WHERE request_id = p_request_id)
       AND count(*) FILTER (WHERE state IN ('done', 'no_results', 'failed')) = (SELECT total_points FROM geogrid_requests WHERE request_id = p_request_id)
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
    IF v_total <> v_request.total_points OR v_terminal <> v_request.total_points THEN
        RAISE EXCEPTION 'Cannot finalize while points are unresolved';
    END IF;

    UPDATE geogrid_requests
    SET status = CASE
            WHEN v_failed = v_request.total_points THEN 'failed'
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

COMMIT;
