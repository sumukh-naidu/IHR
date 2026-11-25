# AI runbooks (AI Specification v1.1 §15.1)

| Runbook | Alerts that link here |
|---|---|
| [Kimi outage](kimi-outage.md) | FollowupLatencyHigh, AiFailureRate, ProviderCircuitOpen (kimi-*) |
| [TTS outage](tts-outage.md) | ProviderCircuitOpen (azure-speech / aws-polly) |
| [GPU / STT failure](gpu-failure.md) | GpuUnavailable, SttLatencyHigh |
| [Prompt rollback](prompt-rollback.md) | RepairRateHigh, quality regressions |

Common facts:
- The AI services never decide workflow. When a job fails, core already has the fallback (S-09):
  next planned question, pre-rendered audio, text display, or the job is retried.
- Logs, metrics and events never contain CV or transcript text; debug captures (A-12) in
  `ihr-generated-private/ai-runs/` are break-glass only.
- Alert rules: `deploy/monitoring/ai-alerts.rules.yml`.
