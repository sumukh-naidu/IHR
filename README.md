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
| `docs/prompts/` | Prompt files v1.0.0 (§7.0–7.6) and `active.yaml` (A-11) |
| `docs/architecture/adr/` | Decision change records (§1.4) |
| `eval/` | Evaluation harness skeleton and §13.3 gates; datasets via Git LFS |
| `.github/workflows/contracts-check.yml` | CI: contracts up to date, tests, offline eval |
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

Local model for integration testing (until the Kimi key arrives; ASSUMPTIONS #19):

```
llama-server -m /Users/infra/idox_ai/models/Qwen3.6-35B-A3B-UD-IQ4_XS.gguf \
  --alias qwen3.6-35b-a3b --port 8081 -c 32768 --jinja
AI_PROVIDER=local LOCAL_LLM_BASE_URL=http://127.0.0.1:8081/v1 python -m ihr_ai_worker.main
```

Same adapter as Kimi (JSON mode, retries, breaker). Eval gate results only count on Kimi.
`WORKER_ROLE=stt|tts STT_PROVIDER=mock TTS_PROVIDER_PRIMARY=mock python -m speech.main`

Both need `KAFKA_BOOTSTRAP_SERVERS` and the `S3_*` variables (§3.3).

## Status (Sprint 7)

Done: contracts, fixtures, worker framework, Kimi adapter (retries, circuit breaker, rate
limit, error mapping, repair once), mock mode for every task, prompt loader (A-11), debug
capture (A-12), contracts-check CI, eval harness skeleton, `tools/kimi_list_models.py` (S-11).
Open S1 exit items: mocks in the Platform compose stack, Kimi smoke call (needs key).

Sprint 2 (CV_PARSE) built: ClamAV INSTREAM, magic-byte sniffing, pdfplumber + OCR fallback
(pypdfium2 + Tesseract), python-docx, LibreOffice DOC conversion, normalisation and §6.2 warnings,
local identity (A-17), the 10-step redaction (§5.2), injection escaping/detection incl. hidden
text (§10), the shared evidence verifier (A-23), CV_PARSE validation and enrichment (A-18, A-19).
Code: `ihr_ai_worker/{extraction,privacy,enrich,pipelines}/`, `evidence.py`.
`AI_PROVIDER=kimi|local` runs the real CV_PARSE; other tasks are still fixtures.
Open S2 items: Testcontainers integration (ClamAV EICAR, SeaweedFS, Kafka), eval on cv-48 (needs dataset).

Sprint 3 built: CV_MATCH (exact requirement set, quote rules, A-20 deterministic EXPERIENCE override,
question policy A-21 with `policy/questions.py`, language policy A-22, `docs/prompts/policy_lexicon.yaml`,
`skill_aliases.json` ~400 entries) and STT (`speech/stt.py`: PyAV 16 kHz decode, A-26 parameters,
segment post-processing, A-27 flags, warm-up before readiness, CUDA OOM reload, `Dockerfile.stt`,
`tools/stt_benchmark.py`). `STT_PROVIDER=faster-whisper` runs the real model.
Open S3 items: L4 GPU benchmark (needs GPU + audio-40), CV_MATCH eval on cv-48 rubrics (needs dataset).

Sprint 4 built: INTERVIEW_PLAN (`pipelines/interview_plan.py`, `plan_allocation.py`: A-25 allocation,
per-question policy / similarity < 0.85 / anchor checks, fallback-bank replacement, all-fallback plan instead of
.failed) and TTS (`speech/tts.py`: cache, `speech/lexicon.yaml`, SSML, Azure primary + Polly failover with
per-provider breakers, audio validation, PII guard). `TTS_PROVIDER_PRIMARY=azure TTS_PROVIDER_SECONDARY=polly`.
Open S4 items: live Azure/Polly calls (need credentials), plans-20 human ratings (needs dataset).

Sprint 5 built: FOLLOWUP (`pipelines/followup.py`: realtime, 12 s, ≤ 1 retry, no repair, policy/consistency
failure → NEXT_QUESTION/NOT_USEFUL) and ANSWER_ASSESSMENT (`pipelines/answer_assessment.py`: exact competency set,
segment-verified quotes ±1, null/reason rules, language policy, WARMUP/CLOSING unscored, low transcript quality →
LOW confidence, no speech → code-built output, injection flag).
Open S5 items: answers-160 / follow-up human ratings (need datasets), FOLLOWUP p95 ≤ 6 s (needs Kimi).

Sprint 6 built: FINAL_REPORT (`pipelines/final_report.py`: no-numbers regex, evidence refs T# → turn/segment,
integrity-word check on limitations, language policy, computedScores attached unchanged). All six AI tasks now
run for real with `AI_PROVIDER=kimi|local`. A local end-to-end run (speech → STT → assessment → report) passes.
Open S6 items: first full evaluation run on Kimi with the datasets (§13.3 gates).

Sprint 7–8 built: §12.1 metrics aligned + `deploy/monitoring/ai-alerts.rules.yml` (all §12.2 alerts) +
`grafana-ihr-ai.json`; evaluation harness computing every §13.3 gate from the datasets (`eval/run.py`,
`metrics.py`, `runners.py`, `datasets.py`); counterfactual bias suite (`eval/bias.py`, A-33); sentinel PII leak
tests; load test `tools/loadtest.py` (30 concurrent interviews: PASS at design load); runbooks (`docs/runbooks/`);
prompt immutability + freeze (`tools/prompt_lock.py`, CI).
Open: datasets (A-31) and the Kimi key for the gate runs; HR calibration (UAT); prompt freeze after sign-off.
