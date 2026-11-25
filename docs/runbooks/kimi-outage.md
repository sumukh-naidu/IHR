# Runbook: Kimi (Moonshot) outage or degradation

**Alerts:** `ProviderCircuitOpen{provider=~"kimi-.*"}`, `AiFailureRate`, `FollowupLatencyHigh`.

## What the system already does
- Retries per A-08 (standard: 2 retries; realtime: 1, only with ≥ 4 s left), then the circuit opens after
  repeated failures and jobs fail fast with `PROVIDER_UNAVAILABLE` (TRANSIENT, retryable).
- Core retries standard jobs (CV, plan, assessment, report) later; nothing is lost.
- FOLLOWUP: core moves to the next planned question after 12 s (S-09). Candidates are not blocked.
- INTERVIEW_PLAN: core uses the fallback bank plan if no plan is ready before the interview.

## Check (5 min)
1. Grafana → AI overview: `ihr_ai_jobs_total{outcome="failed"}` by `error_code`.
   - `PROVIDER_UNAVAILABLE` / `PROVIDER_TIMEOUT` → provider side. Continue below.
   - `PROVIDER_RATE_LIMITED` → our RPM/concurrency vs the key's limit (step 4).
   - `INTERNAL_ERROR` with `ihr_ai_kimi_config_errors_total` rising → 401/403/404: key or model slot problem (step 5).
2. Moonshot status page / account console.
3. `kubectl logs deploy/ihr-ai-worker | grep kimi_` (event names only; no content is logged).

## Act
4. Rate limited: lower `KIMI_RPM_LIMIT` / `KIMI_MAX_CONCURRENCY` to the key's real limit, redeploy; request a
   higher limit from Moonshot.
5. Config error: verify `KIMI_API_KEY` secret, and `python tools/kimi_list_models.py` (both model slots exist).
6. Full outage > 30 min: inform HR operations that CV analysis and reports are delayed (interviews continue
   with planned questions and no follow-ups). Do **not** switch to another provider without the AI lead —
   data-processing terms (S-24) cover Moonshot only.
7. Recovery: the circuit half-opens automatically; check `ihr_ai_jobs_total{outcome="succeeded"}` recovers and
   core's retried jobs drain.

## Afterwards
Incident note: start/end, failed job counts by task, whether any candidate-facing turn exceeded S-09 budgets.
