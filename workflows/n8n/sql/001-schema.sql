-- Apply while connected to geogrid_queue. The n8n queue is isolated from the
-- Smart Grid SQLite database and from n8n's internal database.

CREATE TABLE IF NOT EXISTS geogrid_requests (
    request_id UUID PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload_fingerprint TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    contact_name TEXT NOT NULL,
    company_name TEXT NOT NULL,
    email TEXT NOT NULL,
    whatsapp TEXT NOT NULL,
    city TEXT NOT NULL,
    state TEXT NOT NULL,
    google_maps_url TEXT NOT NULL,
    keyword TEXT NOT NULL,
    consent_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN (
            'queued', 'claimed', 'business_resolved', 'scan_created',
            'submitting', 'submission_uncertain', 'submitted', 'processing',
            'partial', 'done', 'failed', 'budget_blocked', 'manual_review'
        )),
    claim_owner TEXT,
    claim_token UUID,
    claim_expires_at TIMESTAMPTZ,
    claimed_at TIMESTAMPTZ,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    scan_id TEXT UNIQUE,
    external_business_id TEXT,
    business_place_id TEXT,
    business_lat DOUBLE PRECISION CHECK (business_lat BETWEEN -90 AND 90),
    business_lng DOUBLE PRECISION CHECK (business_lng BETWEEN -180 AND 180),
    total_points SMALLINT NOT NULL DEFAULT 9 CHECK (total_points = 9),
    budget_reserved_usd NUMERIC(12, 6) NOT NULL DEFAULT 0
        CHECK (budget_reserved_usd >= 0),
    actual_cost_usd NUMERIC(12, 6) NOT NULL DEFAULT 0
        CHECK (actual_cost_usd >= 0),
    next_poll_at TIMESTAMPTZ,
    error_category TEXT,
    error_code INTEGER,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    CHECK (consent_at IS NOT NULL),
    CHECK (
        status <> 'submitting'
        OR (scan_id IS NOT NULL AND claim_token IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS geogrid_requests_queue_idx
    ON geogrid_requests (created_at, request_id)
    WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS geogrid_requests_recovery_idx
    ON geogrid_requests (claim_expires_at)
    WHERE status IN ('claimed', 'submitting');
CREATE INDEX IF NOT EXISTS geogrid_requests_poll_idx
    ON geogrid_requests (next_poll_at)
    WHERE status IN ('submitted', 'processing', 'partial');
CREATE INDEX IF NOT EXISTS geogrid_requests_campaign_idx
    ON geogrid_requests (campaign_id, created_at);

CREATE TABLE IF NOT EXISTS geogrid_tasks (
    task_row_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_id UUID NOT NULL REFERENCES geogrid_requests(request_id),
    point_index SMALLINT NOT NULL CHECK (point_index BETWEEN 0 AND 8),
    row_index SMALLINT NOT NULL CHECK (row_index BETWEEN 0 AND 2),
    col_index SMALLINT NOT NULL CHECK (col_index BETWEEN 0 AND 2),
    latitude DOUBLE PRECISION NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude DOUBLE PRECISION NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    tag TEXT NOT NULL UNIQUE,
    task_id TEXT UNIQUE,
    state TEXT NOT NULL DEFAULT 'prepared'
        CHECK (state IN (
            'prepared', 'submission_started', 'submission_uncertain',
            'submitted', 'polling', 'pending', 'done', 'no_results', 'failed'
        )),
    dataforseo_status_code INTEGER,
    dataforseo_status_message TEXT,
    error_category TEXT,
    error_message TEXT,
    task_cost_usd NUMERIC(12, 6) NOT NULL DEFAULT 0
        CHECK (task_cost_usd >= 0),
    results JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(results) = 'array'),
    post_attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (post_attempt_count >= 0),
    poll_attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (poll_attempt_count >= 0),
    poll_owner TEXT,
    poll_token UUID,
    poll_expires_at TIMESTAMPTZ,
    next_poll_at TIMESTAMPTZ,
    smartgrid_reported_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ,
    UNIQUE (request_id, point_index),
    UNIQUE (request_id, row_index, col_index),
    CHECK (point_index = row_index * 3 + col_index)
);

CREATE INDEX IF NOT EXISTS geogrid_tasks_poll_queue_idx
    ON geogrid_tasks (next_poll_at, task_row_id)
    WHERE state IN ('submitted', 'pending');
CREATE INDEX IF NOT EXISTS geogrid_tasks_expired_lease_idx
    ON geogrid_tasks (poll_expires_at)
    WHERE state = 'polling';
CREATE INDEX IF NOT EXISTS geogrid_tasks_request_state_idx
    ON geogrid_tasks (request_id, state);
CREATE INDEX IF NOT EXISTS geogrid_tasks_unreported_failure_idx
    ON geogrid_tasks (created_at, task_row_id)
    WHERE state = 'failed' AND smartgrid_reported_at IS NULL;

CREATE TABLE IF NOT EXISTS geogrid_budget (
    scope_type TEXT NOT NULL CHECK (scope_type IN ('system', 'daily', 'event')),
    scope_key TEXT NOT NULL,
    currency CHAR(3) NOT NULL DEFAULT 'USD' CHECK (currency = 'USD'),
    cap_amount_usd NUMERIC(12, 6),
    spent_amount_usd NUMERIC(12, 6) NOT NULL DEFAULT 0
        CHECK (spent_amount_usd >= 0),
    reserved_amount_usd NUMERIC(12, 6) NOT NULL DEFAULT 0
        CHECK (reserved_amount_usd >= 0),
    max_pending_tasks INTEGER,
    new_diagnostics_per_minute INTEGER,
    estimated_task_cost_usd NUMERIC(12, 6),
    daily_cap_usd NUMERIC(12, 6),
    default_event_cap_usd NUMERIC(12, 6),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (scope_type, scope_key),
    CHECK (cap_amount_usd IS NULL OR cap_amount_usd >= 0),
    CHECK (max_pending_tasks IS NULL OR max_pending_tasks >= 0),
    CHECK (
        new_diagnostics_per_minute IS NULL
        OR new_diagnostics_per_minute >= 1
    ),
    CHECK (
        estimated_task_cost_usd IS NULL
        OR estimated_task_cost_usd > 0
    ),
    CHECK (daily_cap_usd IS NULL OR daily_cap_usd >= 0),
    CHECK (default_event_cap_usd IS NULL OR default_event_cap_usd >= 0)
);

CREATE TABLE IF NOT EXISTS geogrid_execution_logs (
    log_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_id UUID REFERENCES geogrid_requests(request_id),
    workflow_name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info'
        CHECK (severity IN ('info', 'warning', 'error')),
    details JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(details) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS geogrid_execution_logs_request_idx
    ON geogrid_execution_logs (request_id, created_at);
CREATE INDEX IF NOT EXISTS geogrid_execution_logs_event_idx
    ON geogrid_execution_logs (event_type, created_at);

-- Fail closed until an operator explicitly configures caps and a conservative
-- per-task estimate based on the current DataForSEO price sheet.
INSERT INTO geogrid_budget (
    scope_type, scope_key, cap_amount_usd, max_pending_tasks,
    new_diagnostics_per_minute, estimated_task_cost_usd
)
VALUES ('system', 'default', NULL, 90, 1, NULL)
ON CONFLICT (scope_type, scope_key) DO NOTHING;
