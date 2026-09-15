-- Observability (docs/plan.md week-5/6 hardening, D-031): read-only views over the ops tables the pipeline,
-- retrieval and agent already write. No new writes, no duplicated state: each view answers one operator question.

-- Model usage and cost by day, purpose and model (LLM calls and Voyage embeddings share ops.llm_calls).
CREATE VIEW ops.v_model_usage_daily AS
SELECT date_trunc('day', called_at)::date AS day, purpose, provider, model,
       count(*) AS calls,
       count(*) FILTER (WHERE outcome = 'ok') AS ok,
       count(*) FILTER (WHERE outcome = 'invalid_output') AS invalid_output,
       count(*) FILTER (WHERE outcome = 'error') AS errors,
       count(*) FILTER (WHERE outcome = 'error' AND error LIKE '429%') AS rate_limited,
       coalesce(sum(input_tokens), 0) AS input_tokens,
       coalesce(sum(output_tokens), 0) AS output_tokens,
       round(coalesce(sum(cost_usd), 0), 4) AS cost_usd,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50_latency_ms,
       percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms
FROM ops.llm_calls
GROUP BY 1, 2, 3, 4;

-- Pipeline health per source: batches by status, stale, and the latest arrival.
CREATE VIEW ops.v_pipeline_health AS
SELECT source,
       count(*) AS batches,
       count(*) FILTER (WHERE status = 'validated') AS validated,
       count(*) FILTER (WHERE status = 'published') AS published,
       count(*) FILTER (WHERE status = 'blocked') AS blocked,
       count(*) FILTER (WHERE status = 'failed') AS failed,
       count(*) FILTER (WHERE stale) AS stale,
       coalesce(sum(row_count), 0) AS rows_received,
       max(received_at) AS last_received_at
FROM ops.batches
GROUP BY source;

-- Exception backlog: what is open, how old, and whose it is.
CREATE VIEW ops.v_exception_backlog AS
SELECT source, rule_id, severity, status, coalesce(owner, '(unassigned)') AS owner,
       count(*) AS exceptions,
       min(created_at) AS oldest_created_at,
       round(extract(epoch FROM now() - min(created_at)) / 86400.0, 1) AS oldest_age_days
FROM ops.exceptions
WHERE status <> 'resolved'
GROUP BY 1, 2, 3, 4, 5;

-- Agent runs: outcomes, fallbacks, and how long approvals have been waiting.
CREATE VIEW ops.v_agent_runs AS
SELECT status, outcome, gate->>'disposition' AS gate_disposition, decision,
       count(*) AS runs,
       count(*) FILTER (WHERE fallback) AS fallbacks,
       round(avg(iterations), 2) AS mean_tool_calls,
       min(started_at) FILTER (WHERE status = 'awaiting_approval') AS oldest_awaiting_since
FROM ops.agent_runs
GROUP BY 1, 2, 3, 4;

-- Tool usage by the agent: calls, empty results, errors and latency per tool.
CREATE VIEW ops.v_tool_usage AS
SELECT tool,
       count(*) AS calls,
       count(*) FILTER (WHERE outcome = 'empty') AS empty,
       count(*) FILTER (WHERE outcome = 'error') AS errors,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50_latency_ms,
       percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_latency_ms,
       max(called_at) AS last_called_at
FROM ops.tool_calls
GROUP BY tool;
