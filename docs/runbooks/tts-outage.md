# Runbook: TTS outage (Azure Speech primary, Amazon Polly secondary)

**Alerts:** `ProviderCircuitOpen{provider="azure-speech"|"aws-polly"}`.

## What the system already does
- Azure failure (timeout 6 s, 429, 5xx, bad audio) → Polly for the same request (A-28).
- 5 consecutive failures open a provider's circuit for 60 s; requests go straight to the other provider.
- Both fail → `.failed PROVIDER_UNAVAILABLE`; core shows the question text with "audio unavailable" (S-09).
- Pre-rendered audio (plan questions, fixed texts) is served from the cache and is unaffected.

## Check
1. `ihr_tts_requests_total` by `provider, outcome`; `ihr_provider_circuit_open`.
2. Azure: Speech resource health (Central India). AWS: Polly in ap-south-1, IAM user `polly:SynthesizeSpeech`.
3. Only Azure down → Polly carries the load (voice differs; accepted). No action beyond monitoring.

## Act
4. Credentials: rotate `AZURE_SPEECH_KEY` / AWS keys if 401/403 (failures counted as provider failures).
5. Both providers down > 15 min: tell HR operations that follow-up questions are shown as text; interviews
   continue. Planned questions keep their cached audio.
6. Recovery is automatic (half-open trial after 60 s).

## Test (go-live checklist)
Block Azure egress in QA → requests are served by Polly within the 6 s timeout; unblock → Azure resumes.
