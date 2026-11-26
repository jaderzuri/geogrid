-- Additive hardening for one-time paid task_post authorization and reservation
-- recovery. Apply after 005 (or after 004 outside the homologation Compose).
BEGIN;

ALTER TABLE geogrid_requests
    ADD COLUMN IF NOT EXISTS budget_reserved_scope_daily_key TEXT,
    ADD COLUMN IF NOT EXISTS budget_reservation_state TEXT NOT NULL DEFAULT 'none',
    ADD COLUMN IF NOT EXISTS task_post_authorized_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS task_post_authorized_claim_token UUID,
    ADD COLUMN IF NOT EXISTS task_post_authorization_count SMALLINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS task_post_response_saved_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS task_post_response_digest TEXT,
    ADD COLUMN IF NOT EXISTS actual_cost_known BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS actual_cost_accounted BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE geogrid_tasks
    ADD COLUMN IF NOT EXISTS task_cost_known BOOLEAN NOT NULL DEFAULT FALSE;

-- Existing requests that crossed the old "submitting" boundary are never
-- eligible for a new paid POST. Their previous response/cost cannot be
-- reconstructed reliably, so preserve any reservation and mark costs unknown.
UPDATE geogrid_requests
SET task_post_authorization_count = 1,
    task_post_authorized_at = coalesce(claimed_at, updated_at, now()),
    task_post_authorized_claim_token =
        coalesce(task_post_authorized_claim_token, claim_token, gen_random_uuid()),
    task_post_response_saved_at = CASE
        WHEN status IN ('submitted', 'processing', 'partial', 'done', 'failed')
            THEN coalesce(updated_at, now())
        ELSE task_post_response_saved_at
    END,
    budget_reservation_state = CASE
        WHEN budget_reserved_usd > 0 AND status = 'submission_uncertain'
            THEN 'held_uncertain'
        WHEN budget_reserved_usd > 0
             AND status IN ('submitting', 'submitted', 'processing', 'partial',
                            'done', 'failed')
            THEN 'held_unknown_cost'
        WHEN status IN ('submitted', 'processing', 'partial', 'done', 'failed')
            THEN 'consumed'
        ELSE budget_reservation_state
    END
WHERE task_post_authorization_count = 0 AND status IN (
    'submitting', 'submission_uncertain', 'submitted', 'processing',
    'partial', 'done', 'failed'
);

UPDATE geogrid_requests
SET budget_reservation_state = 'legacy_unreconciled'
WHERE budget_reserved_usd > 0
  AND budget_reserved_scope_daily_key IS NULL
  AND budget_reservation_state IN ('none', 'held_uncertain', 'held_unknown_cost');

UPDATE geogrid_requests
SET status = 'submission_uncertain',
    error_category = 'technical_error',
    error_message = 'Legacy submission was in progress during authorization hardening; manual reconciliation required',
    claim_owner = NULL, claim_token = NULL, claim_expires_at = NULL,
    budget_reservation_state = CASE
        WHEN budget_reserved_usd > 0 AND budget_reserved_scope_daily_key IS NULL
            THEN 'legacy_unreconciled'
        WHEN budget_reserved_usd > 0 THEN 'held_uncertain'
        ELSE budget_reservation_state
    END,
    updated_at = now()
WHERE status = 'submitting';

UPDATE geogrid_requests
SET status = 'manual_review',
    error_category = 'budget_limit',
    error_message = 'Legacy reservation requires verified original daily scope',
    claim_owner = NULL, claim_token = NULL, claim_expires_at = NULL,
    updated_at = now()
WHERE budget_reserved_usd > 0
  AND budget_reservation_state = 'legacy_unreconciled'
  AND task_post_authorization_count = 0
  AND status IN ('queued', 'claimed', 'scan_created');

UPDATE geogrid_tasks
SET state = 'submission_uncertain', updated_at = now()
WHERE state = 'submission_started'
  AND request_id IN (
      SELECT request_id FROM geogrid_requests
      WHERE status = 'submission_uncertain'
  );

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'geogrid_requests_budget_reservation_state_check'
    ) THEN
        ALTER TABLE geogrid_requests
            ADD CONSTRAINT geogrid_requests_budget_reservation_state_check
            CHECK (budget_reservation_state IN (
                'none', 'reserved', 'released', 'consumed',
                'held_uncertain', 'held_unknown_cost', 'legacy_unreconciled'
            ));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'geogrid_requests_task_post_authorization_count_check'
    ) THEN
        ALTER TABLE geogrid_requests
            ADD CONSTRAINT geogrid_requests_task_post_authorization_count_check
            CHECK (task_post_authorization_count BETWEEN 0 AND 1);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'geogrid_requests_authorization_token_check'
    ) THEN
        ALTER TABLE geogrid_requests
            ADD CONSTRAINT geogrid_requests_authorization_token_check
            CHECK (
                (task_post_authorization_count = 0
                    AND task_post_authorized_at IS NULL
                    AND task_post_authorized_claim_token IS NULL)
                OR
                (task_post_authorization_count = 1
                    AND task_post_authorized_at IS NOT NULL
                    AND task_post_authorized_claim_token IS NOT NULL)
            );
    END IF;
END;
$$;

-- A privileged operator must supply independently verified evidence for the
-- original scope; neither the claim date nor budget balance proves its origin.
CREATE OR REPLACE FUNCTION geogrid_reconcile_legacy_reservation(
    p_request_id UUID, p_daily_scope TEXT, p_evidence_reference TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    v_request geogrid_requests%ROWTYPE;
    v_count INTEGER;
BEGIN
    PERFORM pg_advisory_xact_lock(1847219, 1);
    IF p_daily_scope IS NULL OR p_daily_scope !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
       OR to_char(p_daily_scope::date, 'YYYY-MM-DD') <> p_daily_scope
       OR length(trim(coalesce(p_evidence_reference, ''))) < 8 THEN
        RAISE EXCEPTION 'Verified original daily scope and evidence reference required';
    END IF;
    SELECT * INTO v_request FROM geogrid_requests
    WHERE request_id = p_request_id FOR UPDATE;
    IF NOT FOUND OR v_request.budget_reservation_state <> 'legacy_unreconciled'
       OR v_request.budget_reserved_scope_daily_key IS NOT NULL
       OR v_request.budget_reserved_usd <= 0 THEN
        RAISE EXCEPTION 'Legacy reservation is not awaiting reconciliation';
    END IF;
    PERFORM 1 FROM geogrid_budget
    WHERE (scope_type, scope_key) IN (
        ('daily', p_daily_scope), ('event', v_request.campaign_id)
    ) ORDER BY scope_type, scope_key FOR UPDATE;
    SELECT count(*) INTO v_count FROM geogrid_budget
    WHERE (scope_type, scope_key) IN (
        ('daily', p_daily_scope), ('event', v_request.campaign_id)
    ) AND reserved_amount_usd >= v_request.budget_reserved_usd;
    IF v_count <> 2 THEN
        RAISE EXCEPTION 'Verified scopes do not contain the reservation';
    END IF;
    IF v_request.task_post_authorization_count = 0 THEN
        UPDATE geogrid_budget
        SET reserved_amount_usd = reserved_amount_usd - v_request.budget_reserved_usd,
            updated_at = now()
        WHERE (scope_type, scope_key) IN (
            ('daily', p_daily_scope), ('event', v_request.campaign_id)
        );
        UPDATE geogrid_requests
        SET budget_reserved_usd = 0, budget_reservation_state = 'released',
            updated_at = now()
        WHERE request_id = p_request_id;
    ELSE
        UPDATE geogrid_requests
        SET budget_reserved_scope_daily_key = p_daily_scope,
            budget_reservation_state = CASE
                WHEN task_post_response_saved_at IS NULL THEN 'held_uncertain'
                ELSE 'held_unknown_cost'
            END,
            updated_at = now()
        WHERE request_id = p_request_id;
    END IF;
    INSERT INTO geogrid_execution_logs(request_id, workflow_name, event_type, details)
    VALUES (p_request_id, 'financial-reconciliation', 'legacy_reservation_reconciled',
        jsonb_build_object('daily_scope', p_daily_scope,
                           'evidence_reference', left(p_evidence_reference, 200)));
    RETURN TRUE;
END;
$$;

CREATE OR REPLACE FUNCTION geogrid_release_expired_claim_reservations()
RETURNS INTEGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_request geogrid_requests%ROWTYPE;
    v_requeued INTEGER := 0;
    v_updated INTEGER;
BEGIN
    FOR v_request IN
        SELECT *
        FROM geogrid_requests
        WHERE status IN ('claimed', 'scan_created')
          AND claim_expires_at < now()
          AND task_post_authorization_count = 0
        ORDER BY request_id
        FOR UPDATE SKIP LOCKED
    LOOP
        IF v_request.budget_reserved_usd > 0 THEN
            IF v_request.budget_reservation_state <> 'reserved'
               OR v_request.budget_reserved_scope_daily_key IS NULL THEN
                RAISE EXCEPTION 'Expired request has inconsistent reservation state';
            END IF;
            UPDATE geogrid_budget
            SET reserved_amount_usd = reserved_amount_usd - v_request.budget_reserved_usd,
                updated_at = now()
            WHERE (scope_type, scope_key) IN (
                ('daily', v_request.budget_reserved_scope_daily_key),
                ('event', v_request.campaign_id)
            )
              AND reserved_amount_usd >= v_request.budget_reserved_usd;
            GET DIAGNOSTICS v_updated = ROW_COUNT;
            IF v_updated <> 2 THEN
                RAISE EXCEPTION 'Could not release expired reservation from both scopes';
            END IF;
        ELSIF v_request.budget_reservation_state = 'reserved' THEN
            RAISE EXCEPTION 'Reserved state has no positive reserved amount';
        END IF;

        DELETE FROM geogrid_tasks
        WHERE request_id = v_request.request_id AND state = 'prepared';
        UPDATE geogrid_requests
        SET status = 'queued',
            claim_owner = NULL,
            claim_token = NULL,
            claim_expires_at = NULL,
            budget_reserved_usd = 0,
            budget_reserved_scope_daily_key = NULL,
            budget_reservation_state = CASE
                WHEN v_request.budget_reserved_usd > 0 THEN 'released'
                ELSE 'none'
            END,
            scan_id = NULL,
            external_business_id = NULL,
            business_lat = NULL,
            business_lng = NULL,
            updated_at = now()
        WHERE request_id = v_request.request_id;
        INSERT INTO geogrid_execution_logs (
            request_id, workflow_name, event_type, severity, details
        )
        VALUES (
            v_request.request_id, '02-processador-standard',
            'expired_pre_submission_reservation_released', 'warning',
            jsonb_build_object(
                'released_usd', v_request.budget_reserved_usd,
                'daily_scope', v_request.budget_reserved_scope_daily_key
            )
        );
        v_requeued := v_requeued + 1;
    END LOOP;
    RETURN v_requeued;
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
    PERFORM pg_advisory_xact_lock(1847219, 1);
    PERFORM geogrid_release_expired_claim_reservations();

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

    SELECT count(*) INTO v_claims_last_minute
    FROM geogrid_requests
    WHERE claimed_at >= now() - interval '1 minute';
    IF v_claims_last_minute >= v_control.new_diagnostics_per_minute THEN
        RETURN;
    END IF;

    SELECT count(*) INTO v_pending
    FROM geogrid_tasks
    WHERE state IN ('submission_started', 'submission_uncertain',
                    'submitted', 'polling', 'pending');
    IF v_pending + 9 > v_control.max_pending_tasks THEN
        RETURN;
    END IF;

    SELECT * INTO v_request
    FROM geogrid_requests
    WHERE status = 'queued'
      AND task_post_authorization_count = 0
      AND budget_reserved_usd = 0
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
    IF v_daily.cap_amount_usd IS NULL OR v_event.cap_amount_usd IS NULL
       OR v_daily.spent_amount_usd + v_daily.reserved_amount_usd + v_reserved
            > v_daily.cap_amount_usd
       OR v_event.spent_amount_usd + v_event.reserved_amount_usd + v_reserved
            > v_event.cap_amount_usd THEN
        UPDATE geogrid_requests
        SET status = 'budget_blocked', error_category = 'budget_limit',
            error_message = 'Required daily or campaign budget is unavailable',
            updated_at = now()
        WHERE request_id = v_request.request_id;
        INSERT INTO geogrid_execution_logs (
            request_id, workflow_name, event_type, severity, details
        )
        VALUES (
            v_request.request_id, '02-processador-standard',
            'budget_reservation_denied', 'warning',
            jsonb_build_object('daily_scope', v_daily_key,
                               'event_scope', v_request.campaign_id)
        );
        RETURN;
    END IF;

    UPDATE geogrid_budget
    SET reserved_amount_usd = reserved_amount_usd + v_reserved, updated_at = now()
    WHERE (scope_type, scope_key) IN (
        ('daily', v_daily_key), ('event', v_request.campaign_id)
    );
    UPDATE geogrid_requests
    SET status = 'claimed',
        claim_owner = p_worker_id,
        claim_token = gen_random_uuid(),
        claim_expires_at = now() + interval '10 minutes',
        claimed_at = now(),
        attempt_count = attempt_count + 1,
        budget_reserved_usd = v_reserved,
        budget_reserved_scope_daily_key = v_daily_key,
        budget_reservation_state = 'reserved',
        actual_cost_usd = 0,
        actual_cost_known = FALSE,
        updated_at = now(),
        error_category = NULL,
        error_code = NULL,
        error_message = NULL
    WHERE request_id = v_request.request_id
    RETURNING * INTO v_request;

    INSERT INTO geogrid_execution_logs (
        request_id, workflow_name, event_type, details
    )
    VALUES (
        v_request.request_id, '02-processador-standard', 'request_claimed',
        jsonb_build_object('worker_id', p_worker_id, 'reserved_usd', v_reserved,
                           'daily_scope', v_daily_key)
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
       OR jsonb_array_length(p_points) <> 9 THEN
        RAISE EXCEPTION 'Exactly nine backend-generated grid points are required';
    END IF;

    SELECT count(*) INTO v_count
    FROM (
        SELECT DISTINCT (point->>'row')::SMALLINT AS r,
                        (point->>'col')::SMALLINT AS c
        FROM jsonb_array_elements(p_points) AS elements(point)
    ) cells
    WHERE r BETWEEN 0 AND 2 AND c BETWEEN 0 AND 2;
    IF v_count <> 9 THEN
        RAISE EXCEPTION 'Grid coordinates must contain each 3x3 cell exactly once';
    END IF;
    IF EXISTS (SELECT 1 FROM geogrid_tasks WHERE request_id = p_request_id) THEN
        RAISE EXCEPTION 'Tasks were already prepared for this request';
    END IF;

    INSERT INTO geogrid_tasks (
        request_id, point_index, row_index, col_index, latitude,
        longitude, tag, state
    )
    SELECT p_request_id,
           (point->>'row')::SMALLINT * 3 + (point->>'col')::SMALLINT,
           (point->>'row')::SMALLINT, (point->>'col')::SMALLINT,
           (point->>'lat')::DOUBLE PRECISION, (point->>'lng')::DOUBLE PRECISION,
           p_request_id::TEXT || ':' || (point->>'row') || ':' || (point->>'col'),
           'prepared'
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
        jsonb_build_object('scan_id', p_scan_id, 'point_count', 9)
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

    SELECT count(*) INTO v_count FROM geogrid_tasks
    WHERE request_id = p_request_id AND state = 'prepared' AND task_id IS NULL;
    IF v_count <> 9 OR EXISTS (
        SELECT 1 FROM geogrid_tasks
        WHERE request_id = p_request_id AND state <> 'prepared'
    ) THEN
        RAISE EXCEPTION 'Exactly nine unsubmitted prepared tasks are required';
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
        jsonb_build_object('scan_id', p_scan_id, 'point_count', 9,
                           'authorization_count', 1)
    );
    RETURN TRUE;
END;
$$;

DROP FUNCTION IF EXISTS geogrid_mark_submission_uncertain(UUID, TEXT);
CREATE OR REPLACE FUNCTION geogrid_mark_submission_uncertain(
    p_request_id UUID,
    p_claim_token UUID,
    p_reason TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    v_updated INTEGER;
BEGIN
    UPDATE geogrid_requests
    SET status = 'submission_uncertain',
        error_category = 'technical_error',
        error_message = left(coalesce(p_reason, 'Submission outcome is unknown'), 1000),
        budget_reservation_state = CASE
            WHEN budget_reserved_usd > 0 THEN 'held_uncertain'
            ELSE budget_reservation_state
        END,
        claim_owner = NULL, claim_token = NULL,
        claim_expires_at = NULL, updated_at = now()
    WHERE request_id = p_request_id
      AND status = 'submitting'
      AND task_post_authorization_count = 1
      AND claim_token = p_claim_token
      AND task_post_authorized_claim_token = p_claim_token
      AND task_post_response_saved_at IS NULL;
    GET DIAGNOSTICS v_updated = ROW_COUNT;
    IF v_updated = 0 THEN
        RETURN FALSE;
    END IF;
    UPDATE geogrid_tasks
    SET state = 'submission_uncertain', updated_at = now()
    WHERE request_id = p_request_id AND state = 'submission_started';
    INSERT INTO geogrid_execution_logs (
        request_id, workflow_name, event_type, severity, details
    )
    VALUES (
        p_request_id, '02-processador-standard',
        'paid_submission_requires_reconciliation', 'error',
        jsonb_build_object(
            'reason', left(coalesce(p_reason, 'unknown'), 500),
            'authorization_count', 1
        )
    );
    RETURN TRUE;
END;
$$;

DROP FUNCTION IF EXISTS geogrid_release_prepaid_reservation(UUID, TEXT, TEXT);
CREATE OR REPLACE FUNCTION geogrid_release_prepaid_reservation(
    p_request_id UUID,
    p_claim_token UUID,
    p_status TEXT,
    p_reason TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
DECLARE
    v_request geogrid_requests%ROWTYPE;
    v_updated INTEGER;
BEGIN
    PERFORM pg_advisory_xact_lock(1847219, 1);
    IF p_status NOT IN ('queued', 'manual_review') THEN
        RAISE EXCEPTION 'Pre-submission release status must be queued or manual_review';
    END IF;
    SELECT * INTO v_request FROM geogrid_requests
    WHERE request_id = p_request_id FOR UPDATE;
    IF NOT FOUND OR v_request.status NOT IN ('claimed', 'scan_created')
       OR v_request.claim_token IS DISTINCT FROM p_claim_token
       OR v_request.claim_expires_at IS NULL
       OR v_request.claim_expires_at <= now()
       OR v_request.task_post_authorization_count <> 0
       OR v_request.task_post_authorized_at IS NOT NULL THEN
        RETURN FALSE;
    END IF;

    IF v_request.budget_reserved_usd > 0 THEN
        IF v_request.budget_reservation_state <> 'reserved'
           OR v_request.budget_reserved_scope_daily_key IS NULL THEN
            RAISE EXCEPTION 'Request reservation state is inconsistent';
        END IF;
        UPDATE geogrid_budget
        SET reserved_amount_usd = reserved_amount_usd - v_request.budget_reserved_usd,
            updated_at = now()
        WHERE (scope_type, scope_key) IN (
            ('daily', v_request.budget_reserved_scope_daily_key),
            ('event', v_request.campaign_id)
        )
          AND reserved_amount_usd >= v_request.budget_reserved_usd;
        GET DIAGNOSTICS v_updated = ROW_COUNT;
        IF v_updated <> 2 THEN
            RAISE EXCEPTION 'Could not release reservation from both budget scopes';
        END IF;
    ELSIF v_request.budget_reservation_state = 'reserved' THEN
        RAISE EXCEPTION 'Reserved state has no positive reserved amount';
    END IF;

    DELETE FROM geogrid_tasks
    WHERE request_id = p_request_id AND state = 'prepared';
    UPDATE geogrid_requests
    SET status = p_status, claim_owner = NULL, claim_token = NULL,
        claim_expires_at = NULL, budget_reserved_usd = 0,
        budget_reserved_scope_daily_key = NULL,
        budget_reservation_state = CASE
            WHEN v_request.budget_reserved_usd > 0 THEN 'released'
            ELSE 'none'
        END,
        scan_id = NULL, external_business_id = NULL,
        business_lat = NULL, business_lng = NULL,
        error_category = CASE WHEN p_status = 'queued'
                              THEN 'technical_error' ELSE 'business_not_verified' END,
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
        jsonb_build_object('reason', left(coalesce(p_reason, 'unknown'), 500),
                           'released_usd', v_request.budget_reserved_usd)
    );
    RETURN TRUE;
END;
$$;

DROP FUNCTION IF EXISTS geogrid_save_post_response(UUID, JSONB, NUMERIC);
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
       OR jsonb_array_length(p_task_results) <> 9 THEN
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
        SELECT count(DISTINCT ((item->>'row')::SMALLINT * 3 +
                               (item->>'col')::SMALLINT))
        FROM jsonb_array_elements(p_task_results) AS elements(item)
        WHERE (item->>'row')::SMALLINT BETWEEN 0 AND 2
          AND (item->>'col')::SMALLINT BETWEEN 0 AND 2
    ) <> 9 THEN
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
    IF v_updated <> 9 THEN
        RAISE EXCEPTION 'Expected nine atomically armed tasks for response persistence';
    END IF;

    SELECT bool_or(state = 'submission_uncertain'),
           count(*) FILTER (WHERE state = 'failed')
    INTO v_uncertain, v_failed_count
    FROM geogrid_tasks WHERE request_id = p_request_id;
    v_uncertain := coalesce(v_uncertain, TRUE);
    v_new_status := CASE
        WHEN v_uncertain THEN 'submission_uncertain'
        WHEN v_failed_count = 9 THEN 'failed'
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
            'cost_known', v_cost_known, 'task_count', 9,
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
       OR (SELECT count(*) FROM geogrid_tasks WHERE request_id = p_request_id) <> 9
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

CREATE OR REPLACE FUNCTION geogrid_recover_expired_work()
RETURNS TABLE (requeued_claims INTEGER, uncertain_submissions INTEGER)
LANGUAGE plpgsql
AS $$
DECLARE
    v_requeued INTEGER;
    v_uncertain INTEGER;
BEGIN
    PERFORM pg_advisory_xact_lock(1847219, 1);
    v_requeued := geogrid_release_expired_claim_reservations();

    UPDATE geogrid_tasks
    SET state = 'submission_uncertain', updated_at = now()
    WHERE state = 'submission_started'
      AND request_id IN (
          SELECT request_id FROM geogrid_requests
          WHERE status = 'submitting'
            AND task_post_authorization_count = 1
            AND claim_expires_at < now()
      );
    UPDATE geogrid_requests
    SET status = 'submission_uncertain',
        error_category = 'technical_error',
        error_message = 'Worker lease expired after paid submission was armed; manual reconciliation required',
        budget_reservation_state = CASE
            WHEN budget_reserved_usd > 0 THEN 'held_uncertain'
            ELSE budget_reservation_state
        END,
        claim_owner = NULL, claim_token = NULL,
        claim_expires_at = NULL, updated_at = now()
    WHERE status = 'submitting'
      AND task_post_authorization_count = 1
      AND claim_expires_at < now();
    GET DIAGNOSTICS v_uncertain = ROW_COUNT;
    RETURN QUERY SELECT v_requeued, v_uncertain;
END;
$$;

COMMIT;
