# IHR V1: AI services

AI-team scope of IHR V1, built to the **IHR V1 AI Specification v1.1** (the ground truth):
`ihr-ai-worker`, `ihr-speech-service`, `libs/py-worker-common` and `contracts/`.

## Layout

| Path | Contents |
|------|----------|
| `contracts/events/` | Envelope (A.2) and one payload schema per AI/speech topic (A.5), plus `processing_error_codes.v1.json` (A.3) |
| `contracts/ai-schemas/` | Artefact schemas (§7, §11.1). `$defs.ModelOutput` is the part the model returns |
| `contracts/fixtures/{ai,speech}/` | Request events, raw model responses, expected artefacts and negative fixtures (§7.7) |
| `contracts/ASSUMPTIONS.md` | Where the spec was silent: choices pending both leads' confirmation |
| `libs/py-worker-common/` | Processing loop (§3.2), Kafka runner, storage, logging allow-list, metrics, health |
| `services/ihr-ai-worker/` | Kimi adapter (§4), repair-once flow (A-07), task registry, mock mode (A.8) |
| `services/ihr-speech-service/` | STT and TTS mocks (§11.3) with the real TTS cache and text guard |
| `deploy/compose/ai-mocks.compose.yml` | Mock services to add to the Platform compose stack |
| `tools/gen_contracts.py`, `tools/gen_fixtures.py` | Regenerate contracts and fixtures (CI checks they are up to date) |

## Develop

```bash
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -e libs/py-worker-common -e services/ihr-ai-worker -e services/ihr-speech-service \
  pytest respx numpy
python tools/gen_contracts.py && python tools/gen_fixtures.py   # after changing a schema
pytest
```

## Run in mock mode

`WORKER_ROLE=standard|realtime AI_PROVIDER=mock python -m ihr_ai_worker.main`
`WORKER_ROLE=stt|tts STT_PROVIDER=mock TTS_PROVIDER_PRIMARY=mock python -m speech.main`

Both need `KAFKA_BOOTSTRAP_SERVERS` and the `S3_*` variables (§3.3).

## Status (Sprint 1)

Done: contracts, fixtures, the worker framework, the Kimi adapter (retries, circuit breaker,
rate limit, error mapping, repair once), and mock mode for every task.
Next, per §15: S2 text extraction, redaction and CV_PARSE; S3 CV_MATCH and STT on GPU;
S4 TTS providers and INTERVIEW_PLAN; S5 FOLLOWUP and ANSWER_ASSESSMENT; S6 FINAL_REPORT.
