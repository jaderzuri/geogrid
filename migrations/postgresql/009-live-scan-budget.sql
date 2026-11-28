-- DataForSEO Live uses the existing geogrid_budget ledger. This migration
-- stores only Live request idempotency and per-query reconciliation state.
BEGIN;

ALTER TABLE geogrid_budget
    ADD COLUMN IF NOT EXISTS estimated_live_task_cost_usd NUMERIC(12, 6);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'geogrid_budget_live_task_cost_check'
          AND conrelid = 'geogrid_budget'::regclass
    ) THEN
        ALTER TABLE geogrid_budget
            ADD CONSTRAINT geogrid_budget_live_task_cost_check
            CHECK (
                estimated_live_task_cost_usd IS NULL
                OR estimated_live_task_cost_usd > 0
            );
    END IF;
END;
$$;

CREATE TABLE IF NOT EXISTS geogrid_live_scans (
    idempotency_key UUID PRIMARY KEY,
    request_fingerprint TEXT NOT NULL,
    operator_id TEXT NOT NULL CHECK (length(operator_id) BETWEEN 1 AND 100),
    business_id BIGINT NOT NULL,
    grid_size SMALLINT NOT NULL
        CHECK (grid_size IN (3, 5, 7, 9, 11, 13)),
    keyword_count SMALLINT NOT NULL CHECK (keyword_count BETWEEN 1 AND 10),
    total_queries INTEGER NOT NULL CHECK (
        total_queries = grid_size * grid_size * keyword_count
    ),
    unit_cost_usd NUMERIC(12, 6) NOT NULL CHECK (unit_cost_usd > 0),
    reserved_usd NUMERIC(12, 6) NOT NULL CHECK (reserved_usd > 0),
    remaining_reserved_usd NUMERIC(12, 6) NOT NULL
        CHECK (remaining_reserved_usd >= 0),
    actual_cost_usd NUMERIC(12, 6) NOT NULL DEFAULT 0
        CHECK (actual_cost_usd >= 0),
    budget_daily_key TEXT NOT NULL,
    budget_event_key TEXT NOT NULL DEFAULT 'manual-live',
    claim_token UUID NOT NULL,
    scan_ids BIGINT[],
    status TEXT NOT NULL CHECK (status IN (
        'reserved', 'queued', 'running', 'completed', 'failed',
        'uncertain', 'cost_overrun', 'released'
    )),
    reconciliation_reference TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    CHECK (remaining_reserved_usd <= reserved_usd),
    CHECK (scan_ids IS NULL OR cardinality(scan_ids) = keyword_count)
);

CREATE INDEX IF NOT EXISTS geogrid_live_scans_active_idx
    ON geogrid_live_scans (created_at)
    WHERE status IN ('reserved', 'queued', 'running', 'uncertain', 'cost_overrun');

CREATE TABLE IF NOT EXISTS geogrid_live_scan_queries (
    idempotency_key UUID NOT NULL
        REFERENCES geogrid_live_scans(idempotency_key),
    scan_index SMALLINT NOT NULL CHECK (scan_index BETWEEN 0 AND 9),
    point_index SMALLINT NOT NULL CHECK (point_index BETWEEN 0 AND 168),
    state TEXT NOT NULL CHECK (state IN ('authorized', 'completed', 'uncertain', 'reconciled')),
    actual_cost_usd NUMERIC(12, 6)
        CHECK (actual_cost_usd IS NULL OR actual_cost_usd >= 0),
    authorized_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    PRIMARY KEY (idempotency_key, scan_index, point_index),
    CHECK (
        (state = 'completed' AND actual_cost_usd IS NOT NULL AND completed_at IS NOT NULL)
        OR (state = 'reconciled' AND actual_cost_usd IS NULL AND completed_at IS NOT NULL)
        OR (state IN ('authorized', 'uncertain')
            AND actual_cost_usd IS NULL AND completed_at IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS geogrid_live_scan_queries_state_idx
    ON geogrid_live_scan_queries (idempotency_key, state);

COMMIT;
