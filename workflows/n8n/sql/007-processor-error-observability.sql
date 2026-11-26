-- Structured, sanitized processor failures. Apply after 006 with workers stopped.
BEGIN;

CREATE OR REPLACE FUNCTION geogrid_record_processor_error(
    p_request_id UUID, p_claim_token UUID, p_node TEXT, p_category TEXT,
    p_code TEXT, p_execution_id TEXT
)
RETURNS BOOLEAN LANGUAGE plpgsql AS $$
DECLARE
    r geogrid_requests%ROWTYPE;
    v_stage TEXT;
    v_message TEXT;
    v_pre_status TEXT;
    v_phase TEXT;
    v_category TEXT;
    v_recoverable BOOLEAN;
    v_manual BOOLEAN;
    v_transitioned BOOLEAN;
BEGIN
    v_stage := CASE p_node
        WHEN 'Has Verifiable Place ID' THEN 'place_id'
        WHEN 'Extract Exact Google Place ID' THEN 'place_id'
        WHEN 'Resolve Exact Place Details' THEN 'places'
        WHEN 'Validate Resolved Business' THEN 'identity'
        WHEN 'Register Business in Smart Grid' THEN 'business_registration'
        WHEN 'Create Idempotent Smart Grid Scan' THEN 'scan_registration'
        WHEN 'Build Nine Standard Tasks' THEN 'grid'
        WHEN 'Persist Grid Before Paid Submission' THEN 'grid_persistence'
        WHEN 'Authorize Paid task_post Exactly Once' THEN 'authorization'
        WHEN 'Paid DataForSEO task_post - No Automatic Retry' THEN 'task_post'
        WHEN 'Associate and Classify task_post Response' THEN 'response_mapping'
        WHEN 'Persist DataForSEO IDs and Cost' THEN 'response_persistence'
        WHEN 'Log Persisted Submission Outcomes' THEN 'response_observability'
        WHEN 'Build Failed Submission Point Callbacks' THEN 'callback_build'
        WHEN 'Report Failed Submission Points' THEN 'callback_delivery'
        WHEN 'Record Failed Point Callback Delivery' THEN 'callback_persistence'
        ELSE NULL
    END;
    v_message := CASE p_category
        WHEN 'BUSINESS_NOT_FOUND' THEN 'Business was not found by the provider'
        WHEN 'BUSINESS_AMBIGUOUS' THEN 'Business identity needs verification'
        WHEN 'INVALID_PLACE_ID' THEN 'Place identifier is invalid or missing'
        WHEN 'BUSINESS_IDENTITY_MISMATCH' THEN 'Business identity does not match'
        WHEN 'BUSINESS_LOCATION_MISMATCH' THEN 'Business location does not match'
        WHEN 'CONNECTION_ERROR' THEN 'Integration connection failed'
        WHEN 'TIMEOUT' THEN 'Integration timed out'
        WHEN 'RATE_LIMIT' THEN 'Integration rate limit reached'
        WHEN 'AUTHENTICATION_ERROR' THEN 'Integration authentication failed'
        WHEN 'API_CONTRACT_ERROR' THEN 'Integration returned an invalid contract'
        WHEN 'PERSISTENCE_ERROR' THEN 'Persistence operation failed'
        WHEN 'BUDGET_LIMIT' THEN 'Budget limit reached'
        WHEN 'SUBMISSION_UNCERTAIN' THEN 'Paid submission outcome is uncertain'
        WHEN 'PERMANENT_ERROR' THEN 'Integration reported a permanent error'
        ELSE NULL
    END;
    IF v_stage IS NULL OR v_message IS NULL
       OR p_execution_id IS NULL OR p_execution_id !~ '^[A-Za-z0-9._:-]{1,100}$'
       OR (p_code IS NOT NULL AND p_code !~ '^[A-Z0-9_-]{1,32}$') THEN
        RAISE EXCEPTION 'Invalid sanitized processor failure';
    END IF;
    PERFORM pg_advisory_xact_lock(1847219, 1);
    SELECT * INTO r FROM geogrid_requests WHERE request_id = p_request_id FOR UPDATE;
    IF NOT FOUND OR p_claim_token IS NULL THEN
        RAISE EXCEPTION 'Processor request or claim not found';
    END IF;
    IF r.task_post_authorization_count = 0 THEN
        v_phase := 'before_authorization';
        v_pre_status := CASE WHEN p_node IN (
            'Create Idempotent Smart Grid Scan',
            'Persist Grid Before Paid Submission',
            'Authorize Paid task_post Exactly Once'
        ) THEN 'queued' ELSE 'manual_review' END;
        v_transitioned := geogrid_release_prepaid_reservation(
            p_request_id, p_claim_token, v_pre_status, v_message);
        IF NOT v_transitioned THEN
            RAISE EXCEPTION 'Stale or unauthorized processor claim';
        END IF;
        v_category := p_category;
        v_manual := v_pre_status = 'manual_review';
    ELSIF r.task_post_authorized_claim_token = p_claim_token
          AND r.task_post_response_saved_at IS NULL THEN
        v_phase := 'after_authorization';
        IF r.status = 'submission_uncertain' THEN
            v_transitioned := TRUE;
        ELSE
            v_transitioned := geogrid_mark_submission_uncertain(
                p_request_id, p_claim_token, v_message);
        END IF;
        IF NOT v_transitioned THEN
            RAISE EXCEPTION 'Cannot mark paid submission uncertain';
        END IF;
        v_category := 'SUBMISSION_UNCERTAIN';
        v_manual := TRUE;
    ELSIF r.task_post_authorized_claim_token = p_claim_token
          AND r.task_post_response_saved_at IS NOT NULL
          AND v_stage IN ('response_observability', 'callback_build',
                          'callback_delivery', 'callback_persistence') THEN
        v_phase := 'after_response_persisted';
        v_category := p_category;
        v_manual := TRUE;
    ELSE
        RAISE EXCEPTION 'Processor claim does not own failure';
    END IF;
    v_recoverable := v_phase <> 'after_authorization'
        AND p_category IN ('CONNECTION_ERROR', 'TIMEOUT', 'RATE_LIMIT',
                           'PERSISTENCE_ERROR');
    INSERT INTO geogrid_execution_logs(request_id, workflow_name, event_type, severity, details)
    VALUES (p_request_id, '02-processador-standard', 'processor_error', 'error',
        jsonb_build_object(
            'node', p_node, 'stage', v_stage, 'category', v_category,
            'cause_category', p_category, 'code', p_code, 'message', v_message,
            'attempt', r.attempt_count, 'execution_id', p_execution_id,
            'recoverable', v_recoverable, 'manual_intervention', v_manual,
            'paid_phase', v_phase
        ));
    RETURN TRUE;
END;
$$;

CREATE OR REPLACE FUNCTION geogrid_log_post_outcomes(
    p_request_id UUID, p_claim_token UUID, p_execution_id TEXT
)
RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE
    r geogrid_requests%ROWTYPE;
    v_count INTEGER;
BEGIN
    IF p_execution_id IS NULL OR p_execution_id !~ '^[A-Za-z0-9._:-]{1,100}$' THEN
        RAISE EXCEPTION 'Invalid processor execution id';
    END IF;
    SELECT * INTO r FROM geogrid_requests WHERE request_id = p_request_id FOR UPDATE;
    IF NOT FOUND OR p_claim_token IS NULL
       OR r.task_post_authorized_claim_token IS DISTINCT FROM p_claim_token
       OR r.task_post_response_saved_at IS NULL THEN
        RAISE EXCEPTION 'Persisted submission is not owned by this authorization';
    END IF;
    INSERT INTO geogrid_execution_logs(request_id, workflow_name, event_type, severity, details)
    SELECT p_request_id, '02-processador-standard', 'processor_error', 'error',
           jsonb_build_object(
               'node', 'Associate and Classify task_post Response',
               'stage', 'task_post_result',
               'category', CASE
                   WHEN t.state = 'submission_uncertain' THEN 'SUBMISSION_UNCERTAIN'
                   WHEN t.dataforseo_status_code IN (401, 40100, 40104) THEN 'AUTHENTICATION_ERROR'
                   WHEN t.dataforseo_status_code IN (40202, 40209, 429) THEN 'RATE_LIMIT'
                   WHEN t.dataforseo_status_code = 40203 THEN 'BUDGET_LIMIT'
                   ELSE 'PERMANENT_ERROR'
               END,
               'code', t.dataforseo_status_code,
               'message', CASE WHEN t.state = 'submission_uncertain'
                   THEN 'Paid submission outcome is uncertain'
                   ELSE 'Provider rejected a submitted task' END,
               'attempt', r.attempt_count, 'execution_id', p_execution_id,
               'recoverable', FALSE, 'manual_intervention', TRUE,
               'paid_phase', 'after_authorization',
               'row', t.row_index, 'column', t.col_index
           )
    FROM geogrid_tasks t
    WHERE t.request_id = p_request_id
      AND t.state IN ('failed', 'submission_uncertain')
      AND NOT EXISTS (
          SELECT 1 FROM geogrid_execution_logs l
          WHERE l.request_id = p_request_id
            AND l.event_type = 'processor_error'
            AND l.details->>'stage' = 'task_post_result'
            AND l.details->>'execution_id' = p_execution_id
            AND (l.details->>'row')::INTEGER = t.row_index
            AND (l.details->>'column')::INTEGER = t.col_index
      );
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;

CREATE OR REPLACE FUNCTION geogrid_log_processor_budget_block()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = 'budget_blocked' AND OLD.status IS DISTINCT FROM NEW.status THEN
        INSERT INTO geogrid_execution_logs(request_id, workflow_name, event_type, severity, details)
        VALUES (NEW.request_id, '02-processador-standard', 'processor_error', 'warning',
            jsonb_build_object(
                'node', 'Atomically Claim One Request', 'stage', 'budget',
                'category', 'BUDGET_LIMIT', 'cause_category', 'BUDGET_LIMIT',
                'code', NULL, 'message', 'Budget limit reached',
                'attempt', NEW.attempt_count + 1,
                'execution_id', current_setting('geogrid.processor_execution_id', true),
                'recoverable', TRUE, 'manual_intervention', TRUE,
                'paid_phase', 'before_authorization'
            ));
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS geogrid_processor_budget_block ON geogrid_requests;
CREATE TRIGGER geogrid_processor_budget_block AFTER UPDATE OF status ON geogrid_requests
FOR EACH ROW EXECUTE FUNCTION geogrid_log_processor_budget_block();

CREATE OR REPLACE FUNCTION geogrid_claim_processor_request(p_execution_id TEXT)
RETURNS SETOF geogrid_requests LANGUAGE plpgsql AS $$
BEGIN
    IF p_execution_id IS NULL OR p_execution_id !~ '^[A-Za-z0-9._:-]{1,100}$' THEN
        RAISE EXCEPTION 'Invalid processor execution id';
    END IF;
    PERFORM set_config('geogrid.processor_execution_id', p_execution_id, TRUE);
    RETURN QUERY SELECT * FROM geogrid_claim_next_request('processor-' || p_execution_id);
END;
$$;

COMMIT;
