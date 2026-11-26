UPDATE geogrid_budget
SET daily_cap_usd = 100000,
    default_event_cap_usd = 100000,
    estimated_task_cost_usd = 0.0001,
    max_pending_tasks = 10000,
    new_diagnostics_per_minute = 60,
    enabled = TRUE,
    updated_at = now()
WHERE scope_type = 'system' AND scope_key = 'default';
