# IHR V1 — AI ⇄ Platform Integration Confirmation

Written for: the Platform team (ihr-web, ihr-core-api, ihr-media-worker) and the leads who finalise the Platform Frontend and Backend Specification.

## Document control

| Item | Value |
|---|---|
| AI specification version and date | IHR V1 AI Specification **v1.1**, 24 November 2026 (status: Approved for implementation). Appendix A of that document is the shared integration contract and is identical in the Platform V1.1 document. |
| Reference implementation | Repository `IHR`, branch `ihr_ai_services`. Last commit `757d285` (Sprint 1), plus uncommitted Phase 2 (CV_PARSE) work in the working tree. The event and artefact schema files under `contracts/events/` and `contracts/ai-schemas/` are identical to commit `757d285`. `contracts/ai-schemas/skill_aliases.json` (a lookup table, not a message contract) is new. |
| Contract files this document is derived from | `contracts/events/*.v1.json`, `contracts/ai-schemas/*.v1.json`, `contracts/fixtures/**`, `contracts/ASSUMPTIONS.md` |
| Date of this confirmation | 25 November 2026 |
| AI technical owner | AI lead (the AI Specification names the role, "Owner: AI lead"; no individual name is recorded in the specification or the repository — the AI team adds the name before this is sent) |
| Overall confirmation status | **Confirmed, with 24 pending items.** Topics, payload schemas, error codes, storage layout, retry and idempotency behaviour are confirmed as written in Appendix A and chapters 3–7 and 11 of the AI Specification. The pending items are details the specification leaves open, or places where the AI-team schemas contain an assumption that both leads have not yet approved (`contracts/ASSUMPTIONS.md`). They are listed in section 0 and repeated in the section they affect. |

### How to read this document

Every contract carries up to three labels.

- **Confirmed design**: a decision in the approved AI Specification v1.1. The specification section is given in brackets, for example (A.5) or (§7.3).
- **Implementation status**: whether the code exists today. Possible values: *implemented*, *in progress*, *pending (phase)*.
- **Pending confirmation**: a detail that is not finalised. It is written in this exact form:

> **Status:** Pending confirmation  
> **Detail:** what has not been finalised  
> **Platform impact:** what the Platform team cannot safely finalise until this is confirmed

No value, enum, field name or behaviour in this document was invented to fill a gap. Where the specification and the schema files differ, both are stated.

**Where the examples come from.** Every JSON example was produced by running the repository's real code (`AI_PROVIDER=mock`, `STT_PROVIDER=mock`, TTS mock; see P-24 for the TTS variable name) and fixtures through the same validation the workers use, and each one is valid against its schema file. Therefore `run.provider` shows `mock` and `run.promptVersion` is `null` in the examples. The shape of production RunMeta is shown once, verbatim from A.3, in section H.1. Identifiers in fixtures (UUIDs, `deadlineAt: 2099-…`) are test values.

## 0. Register of pending items

| ID | Topic | Section |
|---|---|---|
| P-01 | interview_plan.v1: `order` and `kind` fields | C |
| P-02 | Fallback-bank questions inside a plan | C |
| P-03 | Plan-level `PlanSource` vs per-question `source` | C |
| P-04 | Allowed question types for a plan | C |
| P-05 | TTS result details not fixed by the spec | E |
| P-06 | Definition of `normalisedText` in `cacheKey` | E |
| P-07 | Mock TTS: Appendix A.8 vs §11.3 | E |
| P-08 | `assessmentStatus` values in the final-report request | F |
| P-09 | How skipped or unanswered turns are conveyed to FINAL_REPORT | F |
| P-10 | Required fields of `competencies[]` in the final-report request | F |
| P-11 | `priorContext` field names and the meaning of "0–2" | F |
| P-12 | `proctoringSummary` shape and counting rules | F |
| P-13 | Empty `question.competencyCodes` for WARMUP and CLOSING turns | F |
| P-14 | Envelope file review and strictness | B |
| P-15 | Layout of `.completed` payloads | B |
| P-16 | `RunMeta` nullability and `validationStatus` values | H |
| P-17 | Shared policy lexicon file | H |
| P-18 | Detecting a request that never produces a result | H |
| P-19 | Roll-out order for additive schema changes | B |
| P-20 | Platform-provided infrastructure the AI services depend on | I |
| P-21 | Encrypted CV documents | B |
| P-22 | Numbering of the `T#` turn labels in `final_report.v1` | F |
| P-23 | INTERVIEW_PLAN behaviour when the model provider is down | B |
| P-24 | Name of the TTS mock switch | I |

## 0.1 Implementation status

| Capability | Implementation status |
|---|---|
| Contracts: envelope, 24 topic schemas, 8 artefact schemas, error-code table, fixtures, CI check (`contracts-check`) | **Implemented** (Sprint 1) |
| Worker framework (§3.2 loop): validation, dead-lettering, de-duplication, deadlines, idempotent replay, backpressure, SIGTERM handling | **Implemented** and unit-tested. Not yet exercised against a real Kafka broker or S3 store: the integration tests (Testcontainers) are scheduled later. |
| `AI_PROVIDER=mock` for all six AI tasks (fixture replay with schema validation, real objects, real events) | **Implemented.** Compose file: `deploy/compose/ai-mocks.compose.yml`. |
| `STT_PROVIDER=mock` and the TTS mock | **Implemented** (section D and E describe their exact behaviour). |
| CV_PARSE with a real model | **Implemented** (Phase 2, Sprint 2): extraction, OCR, redaction, injection defences, validation, enrichment. Run so far only against a local stand-in model; the Kimi API key has not been provided yet. Uncommitted. |
| CV_MATCH, real STT (faster-whisper) | **Pending** — Phase 3 (Sprint 3: 23 Dec 2026 – 5 Jan 2027) |
| INTERVIEW_PLAN, real TTS (Azure, Polly) | **Pending** — Phase 4 (Sprint 4: 6–19 Jan 2027) |
| FOLLOWUP, ANSWER_ASSESSMENT | **Pending** — Phase 5 (Sprint 5: 20 Jan – 2 Feb 2027) |
| FINAL_REPORT | **Pending** — Phase 6 (Sprint 6: 3–16 Feb 2027) |
| Shared policy lexicon `docs/prompts/policy_lexicon.yaml` | **Pending** — Phase 3 (see P-17) |

While a task is pending, the worker in mock mode returns the fixture for that task whatever the input is. A mock `.completed` event is structurally identical to the production one; only `run` values and the content of the artefact differ.

## A. AI service interfaces and responsibilities

### A.1 Services (confirmed design: A-02, A-29, S-01; implementation: see 0.1)

| Deployment | Code / image | Kafka consumer group | Consumes | Produces | Responsibility |
|---|---|---|---|---|---|
| `ihr-ai-worker` | image `ihr-ai-worker`, `WORKER_ROLE=standard` | `ihr-ai-worker` | `ihr.cv.parse.requested`, `ihr.cv.match.requested`, `ihr.interview.plan.requested`, `ihr.interview.answer-assessment.requested`, `ihr.interview.final-report.requested` | the `.completed` / `.failed` topic of each | CV text extraction, redaction and structured profile; CV-versus-requirement evidence; interview question plan; per-answer assessment; reviewer report narrative |
| `ihr-ai-worker-realtime` | same image, `WORKER_ROLE=realtime` | `ihr-ai-worker-realtime` | `ihr.interview.followup.requested` only | `ihr.interview.followup.completed` / `.failed` | Follow-up decision during a live interview. Separate deployment so that bulk CV work can never delay a live interview (A-02). |
| `ihr-speech-stt` | image `ihr-speech-service`, `WORKER_ROLE=stt` | `ihr-speech-stt` | `ihr.speech.transcription.requested` | `ihr.speech.transcription.completed` / `.failed` | Speech-to-text of one answer clip |
| `ihr-speech-tts` | image `ihr-speech-service`, `WORKER_ROLE=tts` | `ihr-speech-tts` | `ihr.speech.synthesis.requested` | `ihr.speech.synthesis.completed` / `.failed` | Text-to-speech of one text, with a content-addressed cache |

`ihr-media-worker` and the `ihr.media.*` topics belong to the Platform team (S-01). They are not covered here.

### A.2 Which service handles each operation

| `ProcessingJobType` (Appendix B) | Request topic | Handled by | Platform input (summary) | AI output (summary) |
|---|---|---|---|---|
| `CV_PARSE` | `ihr.cv.parse.requested` | `ihr-ai-worker` | ObjectRef of the uploaded CV, `originalExtension`, `redactionHints` | `cv_profile.v1` object, redacted CV text object, and inline `identity`, `pageCount`, `ocrUsed`, `textChars`, `warnings` |
| `CV_MATCH` | `ihr.cv.match.requested` | `ihr-ai-worker` | profile and text ObjectRefs from the parse result, job, 1–30 requirements | `cv_match.v1` object (status and evidence per requirement). Core computes score and band (S-05). |
| `INTERVIEW_PLAN` | `ihr.interview.plan.requested` | `ihr-ai-worker` | competencies with anchors and fallback questions, job context, question count, optional profile ObjectRef | `interview_plan.v1` object; inline `questionCount`, `fallbackUsedCount` |
| `FOLLOWUP` | `ihr.interview.followup.requested` | `ihr-ai-worker-realtime` | question with rubric, transcript ObjectRef | inline decision: `ASK_FOLLOW_UP` or `NEXT_QUESTION`, with an optional follow-up question |
| `ANSWER_ASSESSMENT` | `ihr.interview.answer-assessment.requested` | `ihr-ai-worker` | question with rubric, tagged competencies with anchors, transcript ObjectRef and quality, `priorContext` | `answer_assessment.v1` object |
| `FINAL_REPORT` | `ihr.interview.final-report.requested` | `ihr-ai-worker` | competencies, `computedScores`, all turns with ObjectRefs, `proctoringSummary` | `final_report.v1` object (narrative only, no numbers) |
| `TRANSCRIPTION` | `ihr.speech.transcription.requested` | `ihr-speech-stt` | ObjectRef of the answer audio, `vocabularyHints`, `maxDurationSeconds` | `transcript.v1` object; inline `durationMs`, `segmentCount`, `avgConfidence`, `speechDetected`, `qualityFlags` |
| `SYNTHESIS` | `ihr.speech.synthesis.requested` | `ihr-speech-tts` | `text`, `purpose`, `voiceProfileKey`, `cacheKey` | MP3 object; inline `durationMs`, `provider`, `voiceId`, `cacheHit` |

### A.3 Communication mechanism (confirmed design: S-02)

- Integration is **only** Kafka events plus S3 object references. There are no HTTP calls between core and the workers, and no shared database. The workers never write to the database (§12.3); core stores `run` metadata itself (table `ai_runs`).
- Every request has exactly one result: `<base>.completed` or `<base>.failed`. All processing is asynchronous.
- The services also serve `GET /health/live`, `/health/ready`, `/metrics` and `/version` on `HEALTH_PORT` (default 8080). These are for Kubernetes probes and Prometheus and are not an integration API (A-01).

### A.4 What the Platform Backend must provide

1. **Objects must exist before a request is sent**, and each ObjectRef must carry the correct `sha256` and `sizeBytes` of the stored bytes. The worker downloads the object and verifies the hash; a difference fails the job with `INPUT_CHECKSUM_MISMATCH` (permanent).
   - Documents are uploaded by the browser by presigned URL (A.6), so core has to obtain the `sha256` of the uploaded object before it dispatches `cv.parse`.
   - Answer audio is uploaded by the browser; the same applies before `transcription.requested`.
2. **Order of dependent requests** (each request reads the result of an earlier one):
   - `cv.match` needs `cvProfile` and `cvText` from the `cv.parse.completed` event.
   - `interview.plan` takes `cvProfile` from the parse result, or `null` when there is no parsed CV.
   - `followup` and `answer-assessment` need the transcript ObjectRef from `transcription.completed`.
   - `final-report` needs the transcript and assessment ObjectRefs (nullable) and statuses of every turn, plus the scores computed by core.
3. **Rules enforced by core, not by the AI** (confirmed design):
   - Follow-up budget (S-08): the worker does not re-check it. Core sends `followup.requested` only when the budget allows.
   - Scores, bands and evidence level (S-05, S-06): the AI never outputs them. For the final report, core sends `computedScores` and the worker attaches it unchanged.
   - `cacheKey` for TTS (A.5), `deadlineAt` for every request (A.4), `attempt` numbering (S-17).
   - TTS texts must not contain candidate data (S-10).
4. **Redaction hints** (`redactionHints.fullName`, `email`, `phone`) are sent with `cv.parse`, `followup`, `answer-assessment` and `final-report` requests. They are used only for local redaction before any model call and are never logged or sent to the model.
5. **Fallbacks when the AI is unavailable** are Platform behaviour and are already decided in S-09, S-17 and S-18; they are summarised in H.5.

## B. Kafka events and message contracts

### B.1 Event envelope (all topics)

**Confirmed design:** A.2. **Implementation:** schema `contracts/events/envelope.v1.json` implemented (drafted by the AI team, see P-14). Producers validate before publishing; consumers validate on receipt. A message that fails validation on receipt goes to the dead-letter topic and is never partly processed (A.1).

| Path | Type | Required | Constraints |
|---|---|---|---|
| `eventId` | string | yes | uuid |
| `eventType` | string | yes | pattern `^ihr\.[a-z0-9.\-]+$` |
| `eventVersion` | const `1` | yes |  |
| `occurredAt` | string | yes | UTC timestamp `YYYY-MM-DDTHH:MM:SS.mmmZ` (milliseconds and `Z` required) |
| `correlationId` | string | yes | minLen 1 |
| `causationId` | string, nullable | yes | minLen 1 |
| `producer` | enum | yes | values: `ihr-core-api`, `ihr-ai-worker`, `ihr-speech-service`, `ihr-media-worker` |
| `entityType` | enum | yes | values: `CANDIDATE_DOCUMENT`, `APPLICATION`, `INTERVIEW`, `INTERVIEW_TURN`, `MEDIA_ASSET`, `TTS_TEXT` |
| `entityId` | string | yes | minLen 1 |
| `payload` | object (map) | yes |  |

Ownership of each field:

| Field | Set by Platform on requests | Set by AI service on results |
|---|---|---|
| `eventId` | new UUID (version 7 per A.2) for every message | new UUIDv7 for every result |
| `eventType` | the request topic name | the result topic name, for example `ihr.cv.parse.completed` |
| `eventVersion` | `1` | `1` |
| `occurredAt` | current UTC time, `YYYY-MM-DDTHH:MM:SS.mmmZ` | current UTC time, same format |
| `correlationId` | for a request caused by an HTTP request: the request's correlation id (equals the W3C trace-id when `traceparent` is present); copied unchanged through the chain | copied unchanged from the request |
| `causationId` | `null` when caused by an HTTP request, otherwise the `eventId` of the causing message | the `eventId` of the request |
| `producer` | `ihr-core-api` | `ihr-ai-worker` or `ihr-speech-service` |
| `entityType`, `entityId` | the business entity of the job (see table below) | copied unchanged from the request |
| Kafka key | per A.4 (see table below) | the request's Kafka key, unchanged |
| Kafka headers | `traceparent`, `content-type: application/json`, `ihr-schema: <topic>.v1` (A.2) | `content-type: application/json`, `ihr-schema: <result topic>.v1`, and `traceparent` copied from the request when present |

The AI workers read only `traceparent` from request headers and do not reject a message that lacks `content-type` or `ihr-schema` (implementation).

Values used per topic in the AI-team fixtures (the spec lists the six `entityType` values but not which one each topic uses; see P-14):

| Topic (request) | `entityType` in fixture | `entityId` in fixture is the payload's | Kafka key (A.2 / A.4) |
|---|---|---|---|
| `ihr.cv.parse.requested` | `CANDIDATE_DOCUMENT` | `documentId` | `documentId` |
| `ihr.cv.match.requested` | `APPLICATION` | `applicationId` | `applicationId` |
| `ihr.interview.plan.requested` | `INTERVIEW` | `interviewId` | `interviewId` |
| `ihr.interview.followup.requested` | `INTERVIEW_TURN` | `turnId` | `interviewId` |
| `ihr.interview.answer-assessment.requested` | `INTERVIEW_TURN` | `turnId` | `interviewId` |
| `ihr.interview.final-report.requested` | `INTERVIEW` | `interviewId` | `interviewId` |
| `ihr.speech.transcription.requested` | `INTERVIEW_TURN` | `turnId` | `interviewId` |
| `ihr.speech.synthesis.requested` | `TTS_TEXT` | `cacheKey` | `interviewId`, or `cacheKey` for pre-render |

> **P-14 — Envelope file review and strictness**  
> **Status:** Pending confirmation  
> **Detail:** `envelope.v1.json` is Platform-owned (A.1) but was drafted by the AI team from A.2 for Platform review (ASSUMPTIONS #5), and the review has not happened. Points to confirm: `occurredAt` and every payload `deadlineAt` must match `YYYY-MM-DDTHH:MM:SS.mmmZ` (milliseconds and `Z` mandatory; ASSUMPTIONS #17); `eventId` is accepted if it is any UUID (version 7 is not enforced by the schema); `eventType` must match `^ihr\.[a-z0-9.\-]+$` and equal the topic; `correlationId` only needs a minimum length of 1. The `entityType`/`entityId` values used per topic in the fixtures (table in B.1) are not stated per topic in the spec; the AI workers copy both unchanged to results.  
> **Platform impact:** A timestamp without milliseconds or with a `+00:00` offset is rejected and the message is dead-lettered. Platform must confirm the envelope schema and the per-topic `entityType`/`entityId` convention.


Full request envelope (the `cv.parse` fixture), as an example of the complete message:

```json
{
  "eventId": "0192f000-0000-7000-8000-000000000001",
  "eventType": "ihr.cv.parse.requested",
  "eventVersion": 1,
  "occurredAt": "2026-12-02T09:36:49.120Z",
  "correlationId": "8f2a6c1d9b3e4f5a8f2a6c1d9b3e4f5a",
  "causationId": null,
  "producer": "ihr-core-api",
  "entityType": "CANDIDATE_DOCUMENT",
  "entityId": "0b8c3f1e-6a41-4a8e-9a0e-2b4c0d6c1f10",
  "payload": {
    "jobId": "9c1f0001-0000-4000-8000-000000000001",
    "attempt": 1,
    "deadlineAt": "2099-01-01T00:00:00.000Z",
    "outputSchemaVersion": "cv_profile.v1",
    "documentId": "0b8c3f1e-6a41-4a8e-9a0e-2b4c0d6c1f10",
    "candidateId": "1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f",
    "document": {
      "bucket": "ihr-candidate-private",
      "key": "candidate/1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f/documents/0b8c3f1e-6a41-4a8e-9a0e-2b4c0d6c1f10/original",
      "sha256": "eba4483b62e2ebc8990afaae93dd061f6a2d35f490a0aa11d9229c721c00bf60",
      "sizeBytes": 19,
      "mimeType": "application/pdf"
    },
    "originalExtension": "pdf",
    "redactionHints": {
      "fullName": "Asha Verma",
      "email": "asha.verma@example.com",
      "phone": "+91 98765 43210"
    }
  }
}
```

### B.2 Fields common to every request and result (A.3)

| Field | Present in | Meaning |
|---|---|---|
| `jobId` (uuid) | every `.requested`, `.completed`, `.failed` | `processing_jobs.id` in core. The idempotency key: the same `jobId` always produces the same output object key. |
| `attempt` (integer ≥ 1) | same | Dispatch attempt number set by core. Output keys include `jobId` but not `attempt`: a re-dispatch overwrites the same key. |
| `deadlineAt` (timestamp) | every `.requested` | The worker must stop and emit `.failed` `DEADLINE_EXCEEDED` if it cannot finish by this time. A request whose deadline has already passed is not processed and gets `DEADLINE_EXCEEDED`. Format `YYYY-MM-DDTHH:MM:SS.mmmZ` (see P-14). |
| `outputSchemaVersion` (string) | every `.requested` and `.completed` | For example `transcript.v1`. A worker rejects a version it does not implement with `.failed` `UNSUPPORTED_SCHEMA_VERSION` (permanent). |
| `output` | `.completed` | An ObjectRef, or an inline object where noted (follow-up), or `{cvProfile, cvText}` for parse. |
| `run` (RunMeta) | `.completed`; `.failed` (nullable) | See H.1. |

Deadlines set by core, per topic (A.4):

| Topic | Deadline | Critical path |
|---|---|---|
| `ihr.cv.parse.requested` | +10 min | no |
| `ihr.cv.match.requested` | +10 min | no |
| `ihr.interview.plan.requested` | +5 min (BASELINE), +90 s (REPLAN) | REPLAN only |
| `ihr.interview.followup.requested` | +12 s | yes |
| `ihr.interview.answer-assessment.requested` | +10 min | no |
| `ihr.interview.final-report.requested` | +20 min | no |
| `ihr.speech.transcription.requested` | +25 s (live turn), +10 min (re-run) | yes |
| `ihr.speech.synthesis.requested` | +15 s (live), +10 min (pre-render) | yes (follow-ups) |

Topic settings (A.4): 6 partitions; replication factor 3 and `min.insync.replicas=2` in QA/PROD (1/1 in LOCAL/DEV); retention 7 days; `cleanup.policy=delete`. Every topic has a dead-letter twin `<topic>.dlq` with retention 30 days.

> **P-15 — Layout of `.completed` payloads**  
> **Status:** Pending confirmation  
> **Detail:** A.5 lists "inline extras" for several `.completed` events without saying where they live. Implemented: top-level payload fields beside `jobId`, `attempt`, `output`, `outputSchemaVersion` and `run` (ASSUMPTIONS #6). For `ihr.cv.parse.completed`, `output` is the object `{cvProfile: ObjectRef, cvText: ObjectRef}` (ASSUMPTIONS #7).  
> **Platform impact:** Platform consumer schemas must read extras from the top level of the payload, and for parse must read both ObjectRefs from `output`.


### B.3 `ihr.cv.parse.requested` → `ihr.cv.parse.completed` / `.failed`

Producer → consumer: `ihr-core-api` → `ihr-ai-worker`; results `ihr-ai-worker` → `ihr-core-api`. Kafka key `documentId`. **Implementation: implemented** (Phase 2); real model run only with a stand-in so far.

Request payload (A.5):

| Path | Type | Required | Constraints |
|---|---|---|---|
| `jobId` | string | yes | uuid |
| `attempt` | integer | yes | min 1 |
| `deadlineAt` | string | yes | UTC timestamp `YYYY-MM-DDTHH:MM:SS.mmmZ` (milliseconds and `Z` required) |
| `documentId` | string | yes | uuid |
| `candidateId` | string | yes | uuid |
| `document` | ObjectRef | yes |  |
| `originalExtension` | enum | yes | values: `pdf`, `docx`, `doc` |
| `redactionHints` | object | yes |  |
| `redactionHints.fullName` | string, nullable | yes |  |
| `redactionHints.email` | string, nullable | yes |  |
| `redactionHints.phone` | string, nullable | yes |  |
| `outputSchemaVersion` | const `cv_profile.v1` | yes |  |

Example payload:

```json
{
  "jobId": "9c1f0001-0000-4000-8000-000000000001",
  "attempt": 1,
  "deadlineAt": "2099-01-01T00:00:00.000Z",
  "outputSchemaVersion": "cv_profile.v1",
  "documentId": "0b8c3f1e-6a41-4a8e-9a0e-2b4c0d6c1f10",
  "candidateId": "1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f",
  "document": {
    "bucket": "ihr-candidate-private",
    "key": "candidate/1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f/documents/0b8c3f1e-6a41-4a8e-9a0e-2b4c0d6c1f10/original",
    "sha256": "eba4483b62e2ebc8990afaae93dd061f6a2d35f490a0aa11d9229c721c00bf60",
    "sizeBytes": 19,
    "mimeType": "application/pdf"
  },
  "originalExtension": "pdf",
  "redactionHints": {
    "fullName": "Asha Verma",
    "email": "asha.verma@example.com",
    "phone": "+91 98765 43210"
  }
}
```

`.completed` payload:

| Path | Type | Required | Constraints |
|---|---|---|---|
| `jobId` | string | yes | uuid |
| `attempt` | integer | yes | min 1 |
| `output` | object | yes |  |
| `output.cvProfile` | ObjectRef | yes |  |
| `output.cvText` | ObjectRef | yes |  |
| `outputSchemaVersion` | const `cv_profile.v1` | yes |  |
| `run` | RunMeta (see H.1) | yes |  |
| `pageCount` | integer | yes | min 0 |
| `ocrUsed` | boolean | yes |  |
| `textChars` | integer | yes | min 0 |
| `identity` | object | yes |  |
| `identity.name` | object, nullable | yes |  |
| `identity.name.value` | string, nullable | yes |  |
| `identity.name.confidence` | number | yes | min 0; max 1 |
| `identity.name.source` | enum | yes | values: `LOCAL_REGEX`, `LOCAL_HEADER` |
| `identity.email` | object, nullable | yes |  |
| `identity.email.value` | string, nullable | yes |  |
| `identity.email.confidence` | number | yes | min 0; max 1 |
| `identity.email.source` | enum | yes | values: `LOCAL_REGEX`, `LOCAL_HEADER` |
| `identity.phone` | object, nullable | yes |  |
| `identity.phone.value` | string, nullable | yes |  |
| `identity.phone.confidence` | number | yes | min 0; max 1 |
| `identity.phone.source` | enum | yes | values: `LOCAL_REGEX`, `LOCAL_HEADER` |
| `warnings` | array of enum | yes | item values: `OCR_USED`, `OCR_PARTIAL`, `LOW_OCR_CONFIDENCE`, `TEXT_TRUNCATED`, `EXTENSION_MISMATCH`, `MULTI_COLUMN_LAYOUT`, `PROMPT_INJECTION_SUSPECTED`, `NON_ENGLISH_CONTENT`, `DATES_AMBIGUOUS`, `NO_EMPLOYMENT_HISTORY`, `NO_SKILLS_FOUND`, `ENCRYPTED_DOCUMENT`, `TEXT_GARBLED`, `MULTIPLE_PEOPLE_DESCRIBED`, `NOT_A_CV` |

Example (mock run):

```json
{
  "jobId": "9c1f0001-0000-4000-8000-000000000001",
  "attempt": 1,
  "output": {
    "cvProfile": {
      "bucket": "ihr-generated-private",
      "key": "cv/0b8c3f1e-6a41-4a8e-9a0e-2b4c0d6c1f10/profile/9c1f0001-0000-4000-8000-000000000001.json",
      "sha256": "92f5ae18e2a49e55fc630161335fe96c84277161cccec8ac11d2b5e2d2e7b109",
      "sizeBytes": 2361,
      "mimeType": "application/json"
    },
    "cvText": {
      "bucket": "ihr-generated-private",
      "key": "cv/0b8c3f1e-6a41-4a8e-9a0e-2b4c0d6c1f10/text/9c1f0001-0000-4000-8000-000000000001.txt",
      "sha256": "a8d70c5edaa425a72ca92460b9a9e977ec0908d4169513f436e9a6f3d06d104a",
      "sizeBytes": 645,
      "mimeType": "text/plain; charset=utf-8"
    }
  },
  "outputSchemaVersion": "cv_profile.v1",
  "run": {
    "provider": "mock",
    "model": "mock-standard",
    "promptId": "cv_parse",
    "promptVersion": null,
    "promptSha256": null,
    "providerRequestId": null,
    "latencyMs": 0,
    "inputTokens": null,
    "outputTokens": null,
    "providerAttempts": 1,
    "repairAttempted": false,
    "validationStatus": "VALID",
    "workerVersion": "1.0.0+dev"
  },
  "pageCount": 1,
  "ocrUsed": false,
  "textChars": 645,
  "identity": {
    "name": {
      "value": "Asha Verma",
      "confidence": 0.6,
      "source": "LOCAL_HEADER"
    },
    "email": {
      "value": "asha.verma@example.com",
      "confidence": 1.0,
      "source": "LOCAL_REGEX"
    },
    "phone": {
      "value": "+919876543210",
      "confidence": 1.0,
      "source": "LOCAL_REGEX"
    }
  },
  "warnings": []
}
```

Notes:

- `output.cvProfile` → `ihr-generated-private/cv/{documentId}/profile/{jobId}.json`, schema `cv_profile.v1` (Appendix K). `output.cvText` → `ihr-generated-private/cv/{documentId}/text/{jobId}.txt`: the redacted, normalised text, used later for evidence checks by CV_MATCH and INTERVIEW_PLAN. In the implementation this is exactly the text sent to the model (ASSUMPTIONS #20e).
- `identity` is extracted locally and never by the model. It is stored by core and is not sent to later model calls. Each member (`name`, `email`, `phone`) is `null` when nothing was found; `value` can also be `null`. Implementation detail not stated in the spec: phone numbers are normalised to E.164 (for example `+919876543210`), name confidence is `0.6`, email and phone confidence `1.0`.
- `warnings` is the union of the extraction warnings and the model warnings. Values (§6.2 and `cv_profile.v1`): `OCR_USED`, `OCR_PARTIAL`, `LOW_OCR_CONFIDENCE`, `TEXT_TRUNCATED`, `EXTENSION_MISMATCH`, `MULTI_COLUMN_LAYOUT`, `PROMPT_INJECTION_SUSPECTED`, `NON_ENGLISH_CONTENT`, `DATES_AMBIGUOUS`, `NO_EMPLOYMENT_HISTORY`, `NO_SKILLS_FOUND`, `ENCRYPTED_DOCUMENT`, `TEXT_GARBLED`, `MULTIPLE_PEOPLE_DESCRIBED`, `NOT_A_CV`. A `NOT_A_CV` warning alone does not fail the job (HR decides).
- Limits (A-16, §6.1): file ≤ 10 MB (enforced upstream by Platform), at most 30 pages (else `INPUT_TOO_LARGE`), OCR at most 10 pages (`OCR_PARTIAL` beyond that), text ≤ 60,000 characters, at most 40,000 characters sent to the model (tail truncated, warning `TEXT_TRUNCATED`), fewer than 200 extracted characters fails with `INPUT_NO_TEXT`.
- Failure codes that can occur: `INPUT_NOT_FOUND`, `INPUT_CHECKSUM_MISMATCH`, `INPUT_MALWARE_DETECTED`, `INPUT_UNSUPPORTED_FORMAT`, `INPUT_TOO_LARGE`, `INPUT_NO_TEXT`, `OUTPUT_SCHEMA_INVALID`, `OUTPUT_SEMANTIC_INVALID`, `PROVIDER_*`, `STORAGE_UNAVAILABLE`, `DEADLINE_EXCEEDED`, `INTERNAL_ERROR`.

> **P-21 — Encrypted CV documents**  
> **Status:** Pending confirmation  
> **Detail:** §6.1 step 3 says an encrypted PDF/DOCX fails with INPUT_UNSUPPORTED_FORMAT "with warning ENCRYPTED_DOCUMENT". A `.failed` payload has no warnings field. Implemented (ASSUMPTIONS #20i): the code is INPUT_UNSUPPORTED_FORMAT and the fixed message text names ENCRYPTED_DOCUMENT.  
> **Platform impact:** Platform cannot detect an encrypted document from a structured field; only the message text distinguishes it. Whether Platform needs a structured signal is open.


### B.4 `ihr.cv.match.requested` → `ihr.cv.match.completed` / `.failed`

Producer → consumer: `ihr-core-api` → `ihr-ai-worker`. Kafka key `applicationId`. **Implementation: pending (Phase 3)**; mock mode returns the fixture.

| Path | Type | Required | Constraints |
|---|---|---|---|
| `jobId` | string | yes | uuid |
| `attempt` | integer | yes | min 1 |
| `deadlineAt` | string | yes | UTC timestamp `YYYY-MM-DDTHH:MM:SS.mmmZ` (milliseconds and `Z` required) |
| `applicationId` | string | yes | uuid |
| `cvAnalysisId` | string | yes | uuid |
| `rubricVersionId` | string | yes | uuid |
| `cvProfile` | ObjectRef | yes |  |
| `cvText` | ObjectRef | yes |  |
| `job` | object | yes |  |
| `job.title` | string | yes | minLen 1 |
| `job.summary` | string | yes | maxLen 4000 |
| `job.experienceMinMonths` | integer, nullable | yes | min 0 |
| `job.experienceMaxMonths` | integer, nullable | yes | min 0 |
| `requirements` | array | yes | minItems 1; maxItems 30 |
| `requirements[]` | object | yes |  |
| `requirements[].code` | string | yes | minLen 1 |
| `requirements[].category` | enum | yes | values: `SKILL`, `EXPERIENCE`, `EDUCATION`, `CERTIFICATION`, `DOMAIN`, `OTHER` |
| `requirements[].text` | string | yes | minLen 1 |
| `requirements[].skillName` | string | no | minLen 1 |
| `requirements[].type` | enum | yes | values: `MUST_HAVE`, `PREFERRED` |
| `requirements[].weight` | integer | yes | min 1; max 5 |
| `requirements[].minMonths` | integer | no | min 0 |
| `outputSchemaVersion` | const `cv_match.v1` | yes |  |

Example payload:

```json
{
  "jobId": "9c1f0002-0000-4000-8000-000000000002",
  "attempt": 1,
  "deadlineAt": "2099-01-01T00:00:00.000Z",
  "outputSchemaVersion": "cv_match.v1",
  "applicationId": "2d3e4f5a-6b7c-4d8e-9f0a-1b2c3d4e5f60",
  "cvAnalysisId": "3e4f5a6b-7c8d-4e9f-8a1b-2c3d4e5f6071",
  "rubricVersionId": "4f5a6b7c-8d9e-4f0a-9b2c-3d4e5f607182",
  "cvProfile": {
    "bucket": "ihr-generated-private",
    "key": "cv/0b8c3f1e-6a41-4a8e-9a0e-2b4c0d6c1f10/profile/9c1f0001-0000-4000-8000-000000000001.json",
    "sha256": "d1e80f2e9600bcd1959dac27116b2bca0b8d8a07c9aaff17cc27c10df8ea2730",
    "sizeBytes": 2532,
    "mimeType": "application/json"
  },
  "cvText": {
    "bucket": "ihr-generated-private",
    "key": "cv/0b8c3f1e-6a41-4a8e-9a0e-2b4c0d6c1f10/text/9c1f0001-0000-4000-8000-000000000001.txt",
    "sha256": "a8d70c5edaa425a72ca92460b9a9e977ec0908d4169513f436e9a6f3d06d104a",
    "sizeBytes": 645,
    "mimeType": "text/plain"
  },
  "job": {
    "title": "Java Backend Engineer",
    "summary": "Build and operate Java services for payment settlement.",
    "experienceMinMonths": 36,
    "experienceMaxMonths": 96
  },
  "requirements": [
    {
      "code": "R1",
      "category": "SKILL",
      "text": "Strong Java backend development",
      "skillName": "Java",
      "type": "MUST_HAVE",
      "weight": 3
    },
    {
      "code": "R2",
      "category": "SKILL",
      "text": "Event streaming with Apache Kafka",
      "skillName": "Kafka",
      "type": "MUST_HAVE",
      "weight": 3
    },
    {
      "code": "R3",
      "category": "EXPERIENCE",
      "text": "At least 3 years of backend experience",
      "type": "MUST_HAVE",
      "weight": 3,
      "minMonths": 36
    },
    {
      "code": "R4",
      "category": "SKILL",
      "text": "Experience with AWS",
      "skillName": "AWS",
      "type": "PREFERRED",
      "weight": 1
    }
  ]
}
```

`.completed` payload:

| Path | Type | Required | Constraints |
|---|---|---|---|
| `jobId` | string | yes | uuid |
| `attempt` | integer | yes | min 1 |
| `output` | ObjectRef | yes |  |
| `outputSchemaVersion` | const `cv_match.v1` | yes |  |
| `run` | RunMeta (see H.1) | yes |  |

Example (mock run):

```json
{
  "jobId": "9c1f0002-0000-4000-8000-000000000002",
  "attempt": 1,
  "output": {
    "bucket": "ihr-generated-private",
    "key": "application/2d3e4f5a-6b7c-4d8e-9f0a-1b2c3d4e5f60/fit/9c1f0002-0000-4000-8000-000000000002.json",
    "sha256": "a83cfdc1a3526772b77a15056ae7facaeffbb6433e7e0770219e64c4d4b518c7",
    "sizeBytes": 1300,
    "mimeType": "application/json"
  },
  "outputSchemaVersion": "cv_match.v1",
  "run": {
    "provider": "mock",
    "model": "mock-standard",
    "promptId": "cv_match",
    "promptVersion": null,
    "promptSha256": null,
    "providerRequestId": null,
    "latencyMs": 0,
    "inputTokens": null,
    "outputTokens": null,
    "providerAttempts": 1,
    "repairAttempted": false,
    "validationStatus": "VALID",
    "workerVersion": "1.0.0+dev"
  }
}
```

Notes:

- Output object: `ihr-generated-private/application/{applicationId}/fit/{jobId}.json`, schema `cv_match.v1` (Appendix K). There are no inline extras.
- The AI returns a status per requirement: `MATCHED`, `PARTIAL`, `MISSING`, `UNKNOWN`. The set of requirement codes in the result must equal the request exactly. For `EXPERIENCE` requirements with `minMonths`, code overrides the model's status deterministically (A-20). Core computes the numeric score and band (S-05); the AI never returns them.

### B.5 `ihr.interview.plan.requested` → `ihr.interview.plan.completed` / `.failed`

Producer → consumer: `ihr-core-api` → `ihr-ai-worker`. Kafka key `interviewId`. **Implementation: pending (Phase 4)**; mock mode returns the fixture.

| Path | Type | Required | Constraints |
|---|---|---|---|
| `jobId` | string | yes | uuid |
| `attempt` | integer | yes | min 1 |
| `deadlineAt` | string | yes | UTC timestamp `YYYY-MM-DDTHH:MM:SS.mmmZ` (milliseconds and `Z` required) |
| `interviewId` | string | yes | uuid |
| `rubricVersionId` | string | yes | uuid |
| `templateVersionId` | string | yes | uuid |
| `planMode` | enum | yes | values: `BASELINE`, `REPLAN` |
| `mainQuestionCount` | integer | yes | min 4; max 10 |
| `maxAnswerSeconds` | integer | yes | min 1 |
| `competencies` | array | yes | minItems 3; maxItems 8 |
| `competencies[]` | object | yes |  |
| `competencies[].code` | string | yes | minLen 1 |
| `competencies[].name` | string | yes | minLen 1 |
| `competencies[].description` | string | yes |  |
| `competencies[].weight` | number | yes | min 0 |
| `competencies[].mandatory` | boolean | yes |  |
| `competencies[].anchors` | object | yes |  |
| `competencies[].anchors.1` | string | yes | minLen 1 |
| `competencies[].anchors.2` | string | yes | minLen 1 |
| `competencies[].anchors.3` | string | yes | minLen 1 |
| `competencies[].anchors.4` | string | yes | minLen 1 |
| `competencies[].anchors.5` | string | yes | minLen 1 |
| `competencies[].fallbackQuestions` | array of string | yes | minItems 2; maxItems 6; item minLen 1; maxLen 300 |
| `job` | object | yes |  |
| `job.title` | string | yes | minLen 1 |
| `job.summary` | string | yes |  |
| `job.mustHaveSkills` | array of string | yes | item minLen 1 |
| `job.preferredSkills` | array of string | yes | item minLen 1 |
| `cvProfile` | ObjectRef, nullable | yes |  |
| `outputSchemaVersion` | const `interview_plan.v1` | yes |  |

Example payload:

```json
{
  "jobId": "9c1f0003-0000-4000-8000-000000000003",
  "attempt": 1,
  "deadlineAt": "2099-01-01T00:00:00.000Z",
  "outputSchemaVersion": "interview_plan.v1",
  "interviewId": "6de6a7b8-c9d0-4e1f-9a2b-3c4d5e6f7a8b",
  "rubricVersionId": "4f5a6b7c-8d9e-4f0a-9b2c-3d4e5f607182",
  "templateVersionId": "5a6b7c8d-9e0f-4a1b-8c3d-4e5f60718293",
  "planMode": "BASELINE",
  "mainQuestionCount": 6,
  "maxAnswerSeconds": 180,
  "competencies": [
    {
      "code": "TECHNICAL_SKILL",
      "name": "Technical skill",
      "description": "Depth and accuracy of technical knowledge",
      "weight": 40,
      "mandatory": true,
      "anchors": {
        "1": "Incorrect or no relevant technical content",
        "2": "Recognises terms but explanation is vague or partly incorrect",
        "3": "Correct explanation of core concepts with a basic example",
        "4": "Correct, specific explanation with trade-offs or real usage",
        "5": "Deep, precise explanation covering edge cases, alternatives and when not to use it"
      },
      "fallbackQuestions": [
        "Choose one of the main technologies listed for this role that you have used. Explain how it works and when you would not use it.",
        "Describe a technical decision you made recently, the options you considered and why you chose one.",
        "Explain a concept from this role's core skills to a new team member, including a common mistake people make."
      ]
    },
    {
      "code": "PROBLEM_SOLVING",
      "name": "Problem solving",
      "description": "Approach to analysing and solving problems",
      "weight": 20,
      "mandatory": true,
      "anchors": {
        "1": "No approach offered",
        "2": "Jumps to a solution without reasoning",
        "3": "Logical steps to a workable solution",
        "4": "Clear steps, states assumptions, compares options",
        "5": "Structured approach with assumptions, trade-offs, validation and risk handling"
      },
      "fallbackQuestions": [
        "Describe how you would investigate a problem where a system that worked yesterday is failing today.",
        "Tell me about a difficult problem you solved. How did you break it down and check your solution?",
        "If you had two possible solutions and limited time, how would you decide between them?"
      ]
    },
    {
      "code": "PRACTICAL_EXPERIENCE",
      "name": "Practical experience",
      "description": "Concrete, owned work examples",
      "weight": 20,
      "mandatory": true,
      "anchors": {
        "1": "No concrete example",
        "2": "Generic example without own role or detail",
        "3": "Concrete example with own role described",
        "4": "Specific example with own decisions, actions and results",
        "5": "Detailed example with ownership, measurable outcome and lessons learned"
      },
      "fallbackQuestions": [
        "Describe a project you worked on that is relevant to this role. What was your own contribution and what was the result?",
        "Tell me about something you delivered that did not go as planned. What did you do?",
        "Describe a piece of work you are proud of and the steps you took to complete it."
      ]
    },
    {
      "code": "COMMUNICATION",
      "name": "Communication",
      "description": "Relevance, structure and clarity of content",
      "weight": 10,
      "mandatory": false,
      "anchors": {
        "1": "Answer does not address the question",
        "2": "Partly relevant, hard to follow",
        "3": "Relevant and understandable",
        "4": "Relevant, well structured, clear",
        "5": "Concise, well structured, tailored to the question, easy to follow"
      },
      "fallbackQuestions": [
        "How would you explain a recent piece of your work to someone without a technical background?",
        "Describe how you keep others informed when a task you own is delayed."
      ]
    },
    {
      "code": "ROLE_FIT",
      "name": "Role fit",
      "description": "Link between experience and the role's requirements",
      "weight": 10,
      "mandatory": false,
      "anchors": {
        "1": "No connection to the role's requirements",
        "2": "Weak link to the role",
        "3": "Links experience to some requirements",
        "4": "Clear link to most must-have requirements",
        "5": "Clear, specific link to must-have and preferred requirements with examples"
      },
      "fallbackQuestions": [
        "Which parts of this role's requirements match your experience most closely? Give an example.",
        "What would you need to learn or improve to be effective in this role in the first three months?"
      ]
    }
  ],
  "job": {
    "title": "Java Backend Engineer",
    "summary": "Build and operate Java services for payment settlement.",
    "mustHaveSkills": [
      "Java",
      "Kafka"
    ],
    "preferredSkills": [
      "AWS"
    ]
  },
  "cvProfile": {
    "bucket": "ihr-generated-private",
    "key": "cv/0b8c3f1e-6a41-4a8e-9a0e-2b4c0d6c1f10/profile/9c1f0001-0000-4000-8000-000000000001.json",
    "sha256": "d1e80f2e9600bcd1959dac27116b2bca0b8d8a07c9aaff17cc27c10df8ea2730",
    "sizeBytes": 2532,
    "mimeType": "application/json"
  }
}
```

`.completed` payload:

| Path | Type | Required | Constraints |
|---|---|---|---|
| `jobId` | string | yes | uuid |
| `attempt` | integer | yes | min 1 |
| `output` | ObjectRef | yes |  |
| `outputSchemaVersion` | const `interview_plan.v1` | yes |  |
| `run` | RunMeta (see H.1) | yes |  |
| `questionCount` | integer | yes | min 4; max 10 |
| `fallbackUsedCount` | integer | yes | min 0; max 10 |

Example (mock run):

```json
{
  "jobId": "9c1f0003-0000-4000-8000-000000000003",
  "attempt": 1,
  "output": {
    "bucket": "ihr-generated-private",
    "key": "interview/6de6a7b8-c9d0-4e1f-9a2b-3c4d5e6f7a8b/plans/9c1f0003-0000-4000-8000-000000000003.json",
    "sha256": "b10bb4c8617626edaa981c3297d9426a429b226cfdbed3e23fbb6de9011783b3",
    "sizeBytes": 3557,
    "mimeType": "application/json"
  },
  "outputSchemaVersion": "interview_plan.v1",
  "run": {
    "provider": "mock",
    "model": "mock-standard",
    "promptId": "interview_plan",
    "promptVersion": null,
    "promptSha256": null,
    "providerRequestId": null,
    "latencyMs": 0,
    "inputTokens": null,
    "outputTokens": null,
    "providerAttempts": 1,
    "repairAttempted": false,
    "validationStatus": "VALID",
    "workerVersion": "1.0.0+dev"
  },
  "questionCount": 6,
  "fallbackUsedCount": 0
}
```

Notes:

- Output object: `ihr-generated-private/interview/{interviewId}/plans/{jobId}.json`, schema `interview_plan.v1` (section C).
- `mainQuestionCount` comes from the template duration (S-07: 20, 30, 45, 60 minutes → 4, 6, 8, 10). `maxAnswerSeconds` is 180 by default and 360 with an accommodation. `planMode` is `BASELINE`, or `REPLAN` when the candidate uploads a different CV in the portal (P-41 in the Platform Specification).
- `questionCount` equals `mainQuestionCount`. `fallbackUsedCount` is the number of questions replaced from the fallback bank.
- §7.3: if more than half the questions are invalid, the worker repairs once; if that fails, the plan is built entirely from the fallback bank and `.completed` is sent with `fallbackUsedCount = questionCount` (the plan is never `.failed` while a fallback is possible).

> **P-23 — INTERVIEW_PLAN behaviour when the model provider is down**  
> **Status:** Pending confirmation  
> **Detail:** §7.3 says a plan is never `.failed` while a fallback question is possible, and describes building the whole plan from the fallback bank when the model output is unusable. A provider outage after the worker's own retries (`PROVIDER_UNAVAILABLE`, `PROVIDER_TIMEOUT`, `PROVIDER_RATE_LIMITED`) is not mentioned there. S-18 says that if no plan exists at interview start because of a Kimi outage, core builds a deterministic plan from the rubric's fallback bank itself. Whether the worker answers an outage with a fallback `.completed` or with a retryable `.failed` is not specified. Not implemented yet (Phase 4).  
> **Platform impact:** Platform must be prepared for a retryable `.failed` on `ihr.interview.plan` (re-dispatch per S-17) and, separately, for core's own fallback plan at start (S-18). Which one occurs in an outage is not yet defined.


### B.6 `ihr.interview.followup.requested` → `ihr.interview.followup.completed` / `.failed`

Producer → consumer: `ihr-core-api` → `ihr-ai-worker-realtime` (consumer group `ihr-ai-worker-realtime`). Kafka key `interviewId`. Deadline +12 s. **Implementation: pending (Phase 5)**; mock mode returns the fixture after 300 ms.

| Path | Type | Required | Constraints |
|---|---|---|---|
| `jobId` | string | yes | uuid |
| `attempt` | integer | yes | min 1 |
| `deadlineAt` | string | yes | UTC timestamp `YYYY-MM-DDTHH:MM:SS.mmmZ` (milliseconds and `Z` required) |
| `interviewId` | string | yes | uuid |
| `turnId` | string | yes | uuid |
| `mainTurnId` | string | yes | uuid |
| `redactionHints` | object | yes |  |
| `redactionHints.fullName` | string, nullable | yes |  |
| `redactionHints.email` | string, nullable | yes |  |
| `redactionHints.phone` | string, nullable | yes |  |
| `question` | object | yes |  |
| `question.text` | string | yes | minLen 1; maxLen 300 |
| `question.competencyCodes` | array of string | yes | minItems 1; item minLen 1 |
| `question.expectedAnswerRubric` | object | yes |  |
| `question.expectedAnswerRubric.keyPoints` | array of string | yes |  |
| `question.expectedAnswerRubric.strongSignals` | array of string | yes |  |
| `question.expectedAnswerRubric.weakSignals` | array of string | yes |  |
| `transcript` | ObjectRef | yes |  |
| `outputSchemaVersion` | const `followup_decision.v1` | yes |  |

Example payload:

```json
{
  "jobId": "9c1f0004-0000-4000-8000-000000000004",
  "attempt": 1,
  "deadlineAt": "2099-01-01T00:00:00.000Z",
  "outputSchemaVersion": "followup_decision.v1",
  "interviewId": "6de6a7b8-c9d0-4e1f-9a2b-3c4d5e6f7a8b",
  "turnId": "b7e1c2d3-e4f5-4a6b-8c7d-8e9f0a1b2c3d",
  "mainTurnId": "b7e1c2d3-e4f5-4a6b-8c7d-8e9f0a1b2c3d",
  "redactionHints": {
    "fullName": "Asha Verma",
    "email": "asha.verma@example.com",
    "phone": "+91 98765 43210"
  },
  "question": {
    "text": "You mention designing event pipelines on Apache Kafka for settlement batches. Walk me through your own role in that work and the result.",
    "competencyCodes": [
      "PRACTICAL_EXPERIENCE",
      "TECHNICAL_SKILL"
    ],
    "expectedAnswerRubric": {
      "keyPoints": [
        "Describes the pipeline's purpose",
        "States own decisions and actions",
        "Describes the outcome or impact"
      ],
      "strongSignals": [
        "Names concrete design choices and why"
      ],
      "weakSignals": [
        "Describes only team work without own role"
      ]
    }
  },
  "transcript": {
    "bucket": "ihr-generated-private",
    "key": "interview/6de6a7b8-c9d0-4e1f-9a2b-3c4d5e6f7a8b/transcripts/b7e1c2d3-e4f5-4a6b-8c7d-8e9f0a1b2c3d/9c1f0010-0000-4000-8000-000000000010.json",
    "sha256": "a7910c3799926f3bb3e89425836c6f837040aa323ab00c1372c24fae88c87c63",
    "sizeBytes": 1079,
    "mimeType": "application/json"
  }
}
```

`.completed` payload (the output is inline, at most 2 KB, no ObjectRef):

| Path | Type | Required | Constraints |
|---|---|---|---|
| `jobId` | string | yes | uuid |
| `attempt` | integer | yes | min 1 |
| `output` | object | yes |  |
| `output.decision` | enum | yes | values: `ASK_FOLLOW_UP`, `NEXT_QUESTION` |
| `output.reasonCode` | enum | yes | values: `CLARIFY_AMBIGUITY`, `PROBE_DEPTH`, `ANSWER_INCOMPLETE`, `ANSWER_SUFFICIENT`, `NOT_USEFUL` |
| `output.followUp` | object, nullable | yes |  |
| `output.followUp.text` | string | yes | minLen 1; maxLen 200 |
| `output.followUp.competencyCodes` | array of string | yes | minItems 1; item minLen 1 |
| `outputSchemaVersion` | const `followup_decision.v1` | yes |  |
| `run` | RunMeta (see H.1) | yes |  |

Example (mock run):

```json
{
  "jobId": "9c1f0004-0000-4000-8000-000000000004",
  "attempt": 1,
  "output": {
    "decision": "ASK_FOLLOW_UP",
    "reasonCode": "PROBE_DEPTH",
    "followUp": {
      "text": "You mentioned an idempotency check for replayed messages. How did you decide what made a message a duplicate?",
      "competencyCodes": [
        "TECHNICAL_SKILL"
      ]
    }
  },
  "outputSchemaVersion": "followup_decision.v1",
  "run": {
    "provider": "mock",
    "model": "mock-realtime",
    "promptId": "followup_decision",
    "promptVersion": null,
    "promptSha256": null,
    "providerRequestId": null,
    "latencyMs": 0,
    "inputTokens": null,
    "outputTokens": null,
    "providerAttempts": 1,
    "repairAttempted": false,
    "validationStatus": "VALID",
    "workerVersion": "1.0.0+dev"
  }
}
```

Notes (§7.4):

- `decision = ASK_FOLLOW_UP` if and only if `followUp` is present and `reasonCode` is one of `CLARIFY_AMBIGUITY`, `PROBE_DEPTH`, `ANSWER_INCOMPLETE`. Otherwise `decision = NEXT_QUESTION`, `followUp = null`, and `reasonCode` is `ANSWER_SUFFICIENT` or `NOT_USEFUL`.
- A follow-up question fails the question policy → the result is still `.completed` with `NEXT_QUESTION` / `NOT_USEFUL`. This is not a failure.
- Invalid JSON from the model → `.failed` `OUTPUT_SCHEMA_INVALID`; there is no repair call (time budget). Core moves on to the next planned question.
- Retry: at most 1 provider retry, and only if at least 4 s remain (A-08).
- `followUp.competencyCodes` is a subset of the original question's codes; `followUp.text` is at most 200 characters.

### B.7 `ihr.interview.answer-assessment.requested` → `.completed` / `.failed`

Producer → consumer: `ihr-core-api` → `ihr-ai-worker`. Kafka key `interviewId`. **Implementation: pending (Phase 5)**; mock mode returns the fixture.

| Path | Type | Required | Constraints |
|---|---|---|---|
| `jobId` | string | yes | uuid |
| `attempt` | integer | yes | min 1 |
| `deadlineAt` | string | yes | UTC timestamp `YYYY-MM-DDTHH:MM:SS.mmmZ` (milliseconds and `Z` required) |
| `interviewId` | string | yes | uuid |
| `turnId` | string | yes | uuid |
| `turnKind` | enum | yes | values: `WARMUP`, `MAIN`, `FOLLOW_UP`, `CLOSING` |
| `redactionHints` | object | yes |  |
| `redactionHints.fullName` | string, nullable | yes |  |
| `redactionHints.email` | string, nullable | yes |  |
| `redactionHints.phone` | string, nullable | yes |  |
| `question` | object | yes |  |
| `question.text` | string | yes | minLen 1; maxLen 300 |
| `question.competencyCodes` | array of string | yes | minItems 1; item minLen 1 |
| `question.expectedAnswerRubric` | object | yes |  |
| `question.expectedAnswerRubric.keyPoints` | array of string | yes |  |
| `question.expectedAnswerRubric.strongSignals` | array of string | yes |  |
| `question.expectedAnswerRubric.weakSignals` | array of string | yes |  |
| `competencies` | array | yes |  |
| `competencies[]` | object | yes |  |
| `competencies[].code` | string | yes | minLen 1 |
| `competencies[].name` | string | yes | minLen 1 |
| `competencies[].description` | string | yes |  |
| `competencies[].anchors` | object | yes |  |
| `competencies[].anchors.1` | string | yes | minLen 1 |
| `competencies[].anchors.2` | string | yes | minLen 1 |
| `competencies[].anchors.3` | string | yes | minLen 1 |
| `competencies[].anchors.4` | string | yes | minLen 1 |
| `competencies[].anchors.5` | string | yes | minLen 1 |
| `competencies[].weight` | number | no | min 0 |
| `competencies[].mandatory` | boolean | no |  |
| `transcript` | ObjectRef | yes |  |
| `transcriptQuality` | object | yes |  |
| `transcriptQuality.avgConfidence` | number | yes | min 0; max 1 |
| `transcriptQuality.speechDetected` | boolean | yes |  |
| `transcriptQuality.qualityFlags` | array of enum | yes | item values: `NO_SPEECH`, `LOW_CONFIDENCE`, `POSSIBLE_HALLUCINATION`, `CLIPPED_AUDIO`, `TRUNCATED_AT_LIMIT` |
| `priorContext` | array | yes | minItems 0; maxItems 2 |
| `priorContext[]` | object | yes |  |
| `priorContext[].questionText` | string | yes | minLen 1 |
| `priorContext[].transcriptExcerpt` | string | yes | maxLen 1200 |
| `outputSchemaVersion` | const `answer_assessment.v1` | yes |  |

Example payload:

```json
{
  "jobId": "9c1f0005-0000-4000-8000-000000000005",
  "attempt": 1,
  "deadlineAt": "2099-01-01T00:00:00.000Z",
  "outputSchemaVersion": "answer_assessment.v1",
  "interviewId": "6de6a7b8-c9d0-4e1f-9a2b-3c4d5e6f7a8b",
  "turnId": "b7e1c2d3-e4f5-4a6b-8c7d-8e9f0a1b2c3d",
  "turnKind": "MAIN",
  "redactionHints": {
    "fullName": "Asha Verma",
    "email": "asha.verma@example.com",
    "phone": "+91 98765 43210"
  },
  "question": {
    "text": "You mention designing event pipelines on Apache Kafka for settlement batches. Walk me through your own role in that work and the result.",
    "competencyCodes": [
      "PRACTICAL_EXPERIENCE",
      "TECHNICAL_SKILL"
    ],
    "expectedAnswerRubric": {
      "keyPoints": [
        "Describes the pipeline's purpose",
        "States own decisions and actions",
        "Describes the outcome or impact"
      ],
      "strongSignals": [
        "Names concrete design choices and why"
      ],
      "weakSignals": [
        "Describes only team work without own role"
      ]
    }
  },
  "competencies": [
    {
      "code": "TECHNICAL_SKILL",
      "name": "Technical skill",
      "description": "Depth and accuracy of technical knowledge",
      "anchors": {
        "1": "Incorrect or no relevant technical content",
        "2": "Recognises terms but explanation is vague or partly incorrect",
        "3": "Correct explanation of core concepts with a basic example",
        "4": "Correct, specific explanation with trade-offs or real usage",
        "5": "Deep, precise explanation covering edge cases, alternatives and when not to use it"
      }
    },
    {
      "code": "PRACTICAL_EXPERIENCE",
      "name": "Practical experience",
      "description": "Concrete, owned work examples",
      "anchors": {
        "1": "No concrete example",
        "2": "Generic example without own role or detail",
        "3": "Concrete example with own role described",
        "4": "Specific example with own decisions, actions and results",
        "5": "Detailed example with ownership, measurable outcome and lessons learned"
      }
    }
  ],
  "transcript": {
    "bucket": "ihr-generated-private",
    "key": "interview/6de6a7b8-c9d0-4e1f-9a2b-3c4d5e6f7a8b/transcripts/b7e1c2d3-e4f5-4a6b-8c7d-8e9f0a1b2c3d/9c1f0010-0000-4000-8000-000000000010.json",
    "sha256": "a7910c3799926f3bb3e89425836c6f837040aa323ab00c1372c24fae88c87c63",
    "sizeBytes": 1079,
    "mimeType": "application/json"
  },
  "transcriptQuality": {
    "avgConfidence": 0.86,
    "speechDetected": true,
    "qualityFlags": []
  },
  "priorContext": []
}
```

`.completed` payload:

| Path | Type | Required | Constraints |
|---|---|---|---|
| `jobId` | string | yes | uuid |
| `attempt` | integer | yes | min 1 |
| `output` | ObjectRef | yes |  |
| `outputSchemaVersion` | const `answer_assessment.v1` | yes |  |
| `run` | RunMeta (see H.1) | yes |  |

Example (mock run):

```json
{
  "jobId": "9c1f0005-0000-4000-8000-000000000005",
  "attempt": 1,
  "output": {
    "bucket": "ihr-generated-private",
    "key": "interview/6de6a7b8-c9d0-4e1f-9a2b-3c4d5e6f7a8b/assessments/b7e1c2d3-e4f5-4a6b-8c7d-8e9f0a1b2c3d/9c1f0005-0000-4000-8000-000000000005.json",
    "sha256": "3daff5a024a5a21ceb5f172cf3cd8244058c5d55f7847503bab7a1fafcd00491",
    "sizeBytes": 1175,
    "mimeType": "application/json"
  },
  "outputSchemaVersion": "answer_assessment.v1",
  "run": {
    "provider": "mock",
    "model": "mock-standard",
    "promptId": "answer_assessment",
    "promptVersion": null,
    "promptSha256": null,
    "providerRequestId": null,
    "latencyMs": 0,
    "inputTokens": null,
    "outputTokens": null,
    "providerAttempts": 1,
    "repairAttempted": false,
    "validationStatus": "VALID",
    "workerVersion": "1.0.0+dev"
  }
}
```

Notes:

- Output object: `ihr-generated-private/interview/{interviewId}/assessments/{turnId}/{jobId}.json`, schema `answer_assessment.v1` (Appendix K).
- `turnKind` `WARMUP` and `CLOSING` are assessed for evidence only and never scored (S-06). The request then has `competencies: []` and the assessment `scores` is empty (§7.5). See P-13 for the competency codes of these turns.
- When `transcriptQuality.speechDetected = false`, the worker calls no model: it builds the output in code with every score `null` and reason `NO_SPEECH` (§7.5).
- When `transcriptQuality.avgConfidence < 0.55`, or `qualityFlags` contains `LOW_CONFIDENCE` or `POSSIBLE_HALLUCINATION`, every `confidence` is set to `LOW` and the answer flag `LIKELY_TRANSCRIPTION_ISSUE` is added. Low confidence never lowers a score (S-19).
- The `priorContext` field is described in section F.

### B.8 `ihr.interview.final-report.requested` → `.completed` / `.failed`

Producer → consumer: `ihr-core-api` → `ihr-ai-worker`. Kafka key `interviewId`. **Implementation: pending (Phase 6)**; mock mode returns the fixture and attaches `computedScores` from the request unchanged.

| Path | Type | Required | Constraints |
|---|---|---|---|
| `jobId` | string | yes | uuid |
| `attempt` | integer | yes | min 1 |
| `deadlineAt` | string | yes | UTC timestamp `YYYY-MM-DDTHH:MM:SS.mmmZ` (milliseconds and `Z` required) |
| `interviewId` | string | yes | uuid |
| `rubricVersionId` | string | yes | uuid |
| `competencies` | array | yes | minItems 1 |
| `competencies[]` | object | yes |  |
| `competencies[].code` | string | yes | minLen 1 |
| `competencies[].name` | string | yes | minLen 1 |
| `competencies[].description` | string | yes |  |
| `competencies[].weight` | number | yes | min 0 |
| `competencies[].mandatory` | boolean | yes |  |
| `competencies[].anchors` | object | yes |  |
| `competencies[].anchors.1` | string | yes | minLen 1 |
| `competencies[].anchors.2` | string | yes | minLen 1 |
| `competencies[].anchors.3` | string | yes | minLen 1 |
| `competencies[].anchors.4` | string | yes | minLen 1 |
| `competencies[].anchors.5` | string | yes | minLen 1 |
| `competencies[].fallbackQuestions` | array of string | yes | minItems 2; maxItems 6; item minLen 1; maxLen 300 |
| `redactionHints` | object | yes |  |
| `redactionHints.fullName` | string, nullable | yes |  |
| `redactionHints.email` | string, nullable | yes |  |
| `redactionHints.phone` | string, nullable | yes |  |
| `computedScores` | object | yes |  |
| `computedScores.competencies` | array | yes |  |
| `computedScores.competencies[]` | object | yes |  |
| `computedScores.competencies[].code` | string | yes | minLen 1 |
| `computedScores.competencies[].score` | number, nullable | yes | min 1.0; max 5.0 |
| `computedScores.competencies[].assessedTurnCount` | integer | yes | min 0 |
| `computedScores.overallScore` | number, nullable | yes | min 0; max 100 |
| `computedScores.completionState` | enum | yes | values: `COMPLETE`, `INCOMPLETE` |
| `computedScores.evidenceLevel` | enum | yes | values: `STRONG_EVIDENCE`, `MODERATE_EVIDENCE`, `LIMITED_EVIDENCE`, `INSUFFICIENT_EVIDENCE` |
| `turns` | array | yes |  |
| `turns[]` | object | yes |  |
| `turns[].turnId` | string | yes | uuid |
| `turns[].turnNumber` | integer | yes | min 1 |
| `turns[].kind` | enum | yes | values: `WARMUP`, `MAIN`, `FOLLOW_UP`, `CLOSING` |
| `turns[].questionText` | string | yes | minLen 1 |
| `turns[].competencyCodes` | array of string | yes | item minLen 1 |
| `turns[].transcript` | ObjectRef, nullable | yes |  |
| `turns[].assessment` | ObjectRef, nullable | yes |  |
| `turns[].transcriptStatus` | enum | yes | values: `NOT_REQUESTED`, `QUEUED`, `SUCCEEDED`, `NO_SPEECH`, `FAILED`, `RETRYING` |
| `turns[].assessmentStatus` | enum | yes | values: `QUEUED`, `RUNNING`, `SUCCEEDED`, `RETRYING`, `FAILED`, `DEAD_LETTERED`, `CANCELLED` |
| `proctoringSummary` | object (map) | yes | keys: `CAMERA_TRACK_ENDED`, `MIC_TRACK_ENDED`, `TAB_HIDDEN`, `WINDOW_BLUR`, `NETWORK_OFFLINE`, `SESSION_PAUSED`, `SESSION_RESUMED`, `PAGE_RELOADED`, `SCREEN_SHARE_STOPPED`, `SECOND_DEVICE_ATTEMPT`, `RECORDING_UPLOAD_GAP`, `TECHNICAL_ISSUE_REPORTED`; values: min 0 |
| `outputSchemaVersion` | const `final_report.v1` | yes |  |

Example payload:

```json
{
  "jobId": "9c1f0006-0000-4000-8000-000000000006",
  "attempt": 1,
  "deadlineAt": "2099-01-01T00:00:00.000Z",
  "outputSchemaVersion": "final_report.v1",
  "interviewId": "6de6a7b8-c9d0-4e1f-9a2b-3c4d5e6f7a8b",
  "rubricVersionId": "4f5a6b7c-8d9e-4f0a-9b2c-3d4e5f607182",
  "competencies": [
    {
      "code": "TECHNICAL_SKILL",
      "name": "Technical skill",
      "description": "Depth and accuracy of technical knowledge",
      "weight": 40,
      "mandatory": true,
      "anchors": {
        "1": "Incorrect or no relevant technical content",
        "2": "Recognises terms but explanation is vague or partly incorrect",
        "3": "Correct explanation of core concepts with a basic example",
        "4": "Correct, specific explanation with trade-offs or real usage",
        "5": "Deep, precise explanation covering edge cases, alternatives and when not to use it"
      },
      "fallbackQuestions": [
        "Choose one of the main technologies listed for this role that you have used. Explain how it works and when you would not use it.",
        "Describe a technical decision you made recently, the options you considered and why you chose one.",
        "Explain a concept from this role's core skills to a new team member, including a common mistake people make."
      ]
    },
    {
      "code": "PROBLEM_SOLVING",
      "name": "Problem solving",
      "description": "Approach to analysing and solving problems",
      "weight": 20,
      "mandatory": true,
      "anchors": {
        "1": "No approach offered",
        "2": "Jumps to a solution without reasoning",
        "3": "Logical steps to a workable solution",
        "4": "Clear steps, states assumptions, compares options",
        "5": "Structured approach with assumptions, trade-offs, validation and risk handling"
      },
      "fallbackQuestions": [
        "Describe how you would investigate a problem where a system that worked yesterday is failing today.",
        "Tell me about a difficult problem you solved. How did you break it down and check your solution?",
        "If you had two possible solutions and limited time, how would you decide between them?"
      ]
    },
    {
      "code": "PRACTICAL_EXPERIENCE",
      "name": "Practical experience",
      "description": "Concrete, owned work examples",
      "weight": 20,
      "mandatory": true,
      "anchors": {
        "1": "No concrete example",
        "2": "Generic example without own role or detail",
        "3": "Concrete example with own role described",
        "4": "Specific example with own decisions, actions and results",
        "5": "Detailed example with ownership, measurable outcome and lessons learned"
      },
      "fallbackQuestions": [
        "Describe a project you worked on that is relevant to this role. What was your own contribution and what was the result?",
        "Tell me about something you delivered that did not go as planned. What did you do?",
        "Describe a piece of work you are proud of and the steps you took to complete it."
      ]
    },
    {
      "code": "COMMUNICATION",
      "name": "Communication",
      "description": "Relevance, structure and clarity of content",
      "weight": 10,
      "mandatory": false,
      "anchors": {
        "1": "Answer does not address the question",
        "2": "Partly relevant, hard to follow",
        "3": "Relevant and understandable",
        "4": "Relevant, well structured, clear",
        "5": "Concise, well structured, tailored to the question, easy to follow"
      },
      "fallbackQuestions": [
        "How would you explain a recent piece of your work to someone without a technical background?",
        "Describe how you keep others informed when a task you own is delayed."
      ]
    },
    {
      "code": "ROLE_FIT",
      "name": "Role fit",
      "description": "Link between experience and the role's requirements",
      "weight": 10,
      "mandatory": false,
      "anchors": {
        "1": "No connection to the role's requirements",
        "2": "Weak link to the role",
        "3": "Links experience to some requirements",
        "4": "Clear link to most must-have requirements",
        "5": "Clear, specific link to must-have and preferred requirements with examples"
      },
      "fallbackQuestions": [
        "Which parts of this role's requirements match your experience most closely? Give an example.",
        "What would you need to learn or improve to be effective in this role in the first three months?"
      ]
    }
  ],
  "redactionHints": {
    "fullName": "Asha Verma",
    "email": "asha.verma@example.com",
    "phone": "+91 98765 43210"
  },
  "computedScores": {
    "competencies": [
      {
        "code": "TECHNICAL_SKILL",
        "score": 3.7,
        "assessedTurnCount": 3
      },
      {
        "code": "PROBLEM_SOLVING",
        "score": 3.5,
        "assessedTurnCount": 2
      },
      {
        "code": "PRACTICAL_EXPERIENCE",
        "score": 4.0,
        "assessedTurnCount": 1
      },
      {
        "code": "COMMUNICATION",
        "score": 3.8,
        "assessedTurnCount": 4
      },
      {
        "code": "ROLE_FIT",
        "score": null,
        "assessedTurnCount": 0
      }
    ],
    "overallScore": 68.3,
    "completionState": "COMPLETE",
    "evidenceLevel": "MODERATE_EVIDENCE"
  },
  "turns": [
    {
      "turnId": "b7e1c2d3-e4f5-4a6b-8c7d-8e9f0a1b2c3d",
      "turnNumber": 1,
      "kind": "MAIN",
      "questionText": "You mention designing event pipelines on Apache Kafka for settlement batches. Walk me through your own role in that work and the result.",
      "competencyCodes": [
        "PRACTICAL_EXPERIENCE",
        "TECHNICAL_SKILL"
      ],
      "transcript": {
        "bucket": "ihr-generated-private",
        "key": "interview/6de6a7b8-c9d0-4e1f-9a2b-3c4d5e6f7a8b/transcripts/b7e1c2d3-e4f5-4a6b-8c7d-8e9f0a1b2c3d/9c1f0010-0000-4000-8000-000000000010.json",
        "sha256": "a7910c3799926f3bb3e89425836c6f837040aa323ab00c1372c24fae88c87c63",
        "sizeBytes": 1079,
        "mimeType": "application/json"
      },
      "assessment": {
        "bucket": "ihr-generated-private",
        "key": "interview/6de6a7b8-c9d0-4e1f-9a2b-3c4d5e6f7a8b/assessments/b7e1c2d3-e4f5-4a6b-8c7d-8e9f0a1b2c3d/9c1f0005-0000-4000-8000-000000000005.json",
        "sha256": "c65d9734cad06e8c6ff40781460a1b88d1ead5300d4d624c76dc08e17366226c",
        "sizeBytes": 1229,
        "mimeType": "application/json"
      },
      "transcriptStatus": "SUCCEEDED",
      "assessmentStatus": "SUCCEEDED"
    }
  ],
  "proctoringSummary": {
    "TAB_HIDDEN": 1
  }
}
```

`.completed` payload:

| Path | Type | Required | Constraints |
|---|---|---|---|
| `jobId` | string | yes | uuid |
| `attempt` | integer | yes | min 1 |
| `output` | ObjectRef | yes |  |
| `outputSchemaVersion` | const `final_report.v1` | yes |  |
| `run` | RunMeta (see H.1) | yes |  |

Example (mock run):

```json
{
  "jobId": "9c1f0006-0000-4000-8000-000000000006",
  "attempt": 1,
  "output": {
    "bucket": "ihr-generated-private",
    "key": "interview/6de6a7b8-c9d0-4e1f-9a2b-3c4d5e6f7a8b/reports/9c1f0006-0000-4000-8000-000000000006.json",
    "sha256": "056547012c1bf473d24efb9a38b1f68feff7c0156c8750658683e3dff43706f6",
    "sizeBytes": 2027,
    "mimeType": "application/json"
  },
  "outputSchemaVersion": "final_report.v1",
  "run": {
    "provider": "mock",
    "model": "mock-standard",
    "promptId": "final_report",
    "promptVersion": null,
    "promptSha256": null,
    "providerRequestId": null,
    "latencyMs": 0,
    "inputTokens": null,
    "outputTokens": null,
    "providerAttempts": 1,
    "repairAttempted": false,
    "validationStatus": "VALID",
    "workerVersion": "1.0.0+dev"
  }
}
```

Notes:

- Output object: `ihr-generated-private/interview/{interviewId}/reports/{jobId}.json`, schema `final_report.v1` (Appendix K). The report has no numbers of its own; code attaches `computedScores` from the request unchanged so that the stored artefact is self-contained.
- In the stored artefact, evidence references use a turn label `T#` and a segment index. Mapping `T#` back to `turnId` uses the request's `turns[]` (ASSUMPTIONS #15); the numbering rule is P-22 in section F.
- The fields `turns[].assessmentStatus`, `proctoringSummary` and the required content of `competencies[]` are discussed in section F.

### B.9 `ihr.speech.transcription.requested` → `.completed` / `.failed`

Producer → consumer: `ihr-core-api` → `ihr-speech-stt`. Kafka key `interviewId`. Deadline +25 s (live turn) or +10 min (re-run). **Implementation:** the mock (`STT_PROVIDER=mock`) is implemented; the real faster-whisper service is pending (Phase 3, with a benchmark).

| Path | Type | Required | Constraints |
|---|---|---|---|
| `jobId` | string | yes | uuid |
| `attempt` | integer | yes | min 1 |
| `deadlineAt` | string | yes | UTC timestamp `YYYY-MM-DDTHH:MM:SS.mmmZ` (milliseconds and `Z` required) |
| `interviewId` | string | yes | uuid |
| `turnId` | string | yes | uuid |
| `audio` | ObjectRef | yes |  |
| `language` | const `en` | yes |  |
| `vocabularyHints` | array of string | yes | minItems 0; maxItems 40; item minLen 1 |
| `maxDurationSeconds` | integer | yes | min 1 |
| `outputSchemaVersion` | const `transcript.v1` | yes |  |

Example payload:

```json
{
  "jobId": "9c1f0007-0000-4000-8000-000000000007",
  "attempt": 1,
  "deadlineAt": "2099-01-01T00:00:00.000Z",
  "outputSchemaVersion": "transcript.v1",
  "interviewId": "6de6a7b8-c9d0-4e1f-9a2b-3c4d5e6f7a8b",
  "turnId": "b7e1c2d3-e4f5-4a6b-8c7d-8e9f0a1b2c3d",
  "audio": {
    "bucket": "ihr-interview-raw",
    "key": "interview/6de6a7b8-c9d0-4e1f-9a2b-3c4d5e6f7a8b/answers/b7e1c2d3-e4f5-4a6b-8c7d-8e9f0a1b2c3d/audio",
    "sha256": "ef746a1a59afecba50d3ce36e3506ee52784236e91034569ccb54f73fbc3c632",
    "sizeBytes": 15,
    "mimeType": "audio/webm"
  },
  "language": "en",
  "vocabularyHints": [
    "Kafka",
    "Spring Boot",
    "PostgreSQL"
  ],
  "maxDurationSeconds": 400
}
```

`.completed` payload:

| Path | Type | Required | Constraints |
|---|---|---|---|
| `jobId` | string | yes | uuid |
| `attempt` | integer | yes | min 1 |
| `output` | ObjectRef | yes |  |
| `outputSchemaVersion` | const `transcript.v1` | yes |  |
| `run` | RunMeta (see H.1) | yes |  |
| `durationMs` | integer | yes | min 0 |
| `segmentCount` | integer | yes | min 0 |
| `avgConfidence` | number | yes | min 0; max 1 |
| `speechDetected` | boolean | yes |  |
| `qualityFlags` | array of enum | yes | item values: `NO_SPEECH`, `LOW_CONFIDENCE`, `POSSIBLE_HALLUCINATION`, `CLIPPED_AUDIO`, `TRUNCATED_AT_LIMIT` |

Example (mock run, 12 s of audio):

```json
{
  "jobId": "9c1f0007-0000-4000-8000-000000000007",
  "attempt": 1,
  "output": {
    "bucket": "ihr-generated-private",
    "key": "interview/6de6a7b8-c9d0-4e1f-9a2b-3c4d5e6f7a8b/transcripts/b7e1c2d3-e4f5-4a6b-8c7d-8e9f0a1b2c3d/9c1f0007-0000-4000-8000-000000000007.json",
    "sha256": "03e8dc6dab4dd5cb9a79ced338c48e44ff210bd2330c3693b6b7a10d6f5d5249",
    "sizeBytes": 549,
    "mimeType": "application/json"
  },
  "outputSchemaVersion": "transcript.v1",
  "run": {
    "provider": "mock",
    "model": "mock",
    "promptId": null,
    "promptVersion": null,
    "promptSha256": null,
    "providerRequestId": null,
    "latencyMs": 0,
    "inputTokens": null,
    "outputTokens": null,
    "providerAttempts": 1,
    "repairAttempted": false,
    "validationStatus": "VALID",
    "workerVersion": "1.0.0+dev"
  },
  "durationMs": 12000,
  "segmentCount": 3,
  "avgConfidence": 0.9,
  "speechDetected": true,
  "qualityFlags": []
}
```

Notes:

- Output object: `ihr-generated-private/interview/{interviewId}/transcripts/{turnId}/{jobId}.json`, schema `transcript.v1` (section D).
- `vocabularyHints` are skill names from the rubric and never candidate data. They are used to build the model's `initial_prompt` ("Interview answer. Terms: …", at most 200 characters, cut at a comma).
- `language` is fixed to `en` in V1. Audio over 10 MB fails with `INPUT_TOO_LARGE`; an audio file with no audio stream or that cannot be decoded fails with `INPUT_UNSUPPORTED_FORMAT`.
- **No speech is a `.completed` result, not a failure:** `speechDetected` is `false` and `qualityFlags` contains `NO_SPEECH`. Mapping to Platform's `TranscriptStatus.NO_SPEECH` is Platform's responsibility.

### B.10 `ihr.speech.synthesis.requested` → `.completed` / `.failed`

Producer → consumer: `ihr-core-api` → `ihr-speech-tts`. Kafka key `interviewId`, or `cacheKey` for a pre-render. Deadline +15 s (live) or +10 min (pre-render). **Implementation:** the mock (`TTS_PROVIDER_PRIMARY=mock`) is implemented, including the cache and the personal-data guard; Azure and Polly are pending (Phase 4).

| Path | Type | Required | Constraints |
|---|---|---|---|
| `jobId` | string | yes | uuid |
| `attempt` | integer | yes | min 1 |
| `deadlineAt` | string | yes | UTC timestamp `YYYY-MM-DDTHH:MM:SS.mmmZ` (milliseconds and `Z` required) |
| `interviewId` | string, nullable | yes | uuid |
| `turnId` | string, nullable | yes | uuid |
| `purpose` | enum | yes | values: `OPENING`, `WARMUP`, `QUESTION`, `FOLLOW_UP`, `CLOSING`, `SYSTEM_MESSAGE` |
| `text` | string | yes | minLen 1; maxLen 600 |
| `voiceProfileKey` | string | yes | minLen 1 |
| `cacheKey` | string | yes | hex, 64 chars |
| `outputSchemaVersion` | const `tts_audio.v1` | yes |  |

Example payload:

```json
{
  "jobId": "9c1f0008-0000-4000-8000-000000000008",
  "attempt": 1,
  "deadlineAt": "2099-01-01T00:00:00.000Z",
  "outputSchemaVersion": "tts_audio.v1",
  "interviewId": null,
  "turnId": null,
  "purpose": "QUESTION",
  "text": "Tell me about a project you worked on that is relevant to this role.",
  "voiceProfileKey": "en-default",
  "cacheKey": "79491119a8f5e41be5a4001803f3da8f567466eee821cf92a03e95295401e715"
}
```

`.completed` payload:

| Path | Type | Required | Constraints |
|---|---|---|---|
| `jobId` | string | yes | uuid |
| `attempt` | integer | yes | min 1 |
| `output` | ObjectRef | yes |  |
| `outputSchemaVersion` | const `tts_audio.v1` | yes |  |
| `run` | RunMeta (see H.1) | yes |  |
| `durationMs` | integer | yes | min 0 |
| `provider` | string | yes | minLen 1 |
| `voiceId` | string | yes | minLen 1 |
| `cacheHit` | boolean | yes |  |

Example (mock run, first request):

```json
{
  "jobId": "9c1f0008-0000-4000-8000-000000000008",
  "attempt": 1,
  "output": {
    "bucket": "ihr-generated-private",
    "key": "tts/en-default/79491119a8f5e41be5a4001803f3da8f567466eee821cf92a03e95295401e715.mp3",
    "sha256": "f541c0465be30217a63f63a28c1a8f78a35200af921caeaf8734d8cb6a08ad30",
    "sizeBytes": 8492,
    "mimeType": "audio/mpeg"
  },
  "outputSchemaVersion": "tts_audio.v1",
  "run": {
    "provider": "mock",
    "model": "mock-tone",
    "promptId": null,
    "promptVersion": null,
    "promptSha256": null,
    "providerRequestId": null,
    "latencyMs": 0,
    "inputTokens": null,
    "outputTokens": null,
    "providerAttempts": 1,
    "repairAttempted": false,
    "validationStatus": "VALID",
    "workerVersion": "1.0.0+dev"
  },
  "durationMs": 4900,
  "provider": "mock",
  "voiceId": "mock-tone",
  "cacheHit": false
}
```

Notes: see section E.

### B.11 `.failed` payload (all eight result topics)

All `.failed` topics have the same payload:

| Path | Type | Required | Constraints |
|---|---|---|---|
| `jobId` | string | yes | uuid |
| `attempt` | integer | yes | min 1 |
| `errorCode` | enum | yes | values: `DEADLINE_EXCEEDED`, `PROVIDER_TIMEOUT`, `PROVIDER_RATE_LIMITED`, `PROVIDER_UNAVAILABLE`, `STORAGE_UNAVAILABLE`, `INPUT_NOT_FOUND`, `INPUT_CHECKSUM_MISMATCH`, `INPUT_UNSUPPORTED_FORMAT`, `INPUT_TOO_LARGE`, `INPUT_MALWARE_DETECTED`, `INPUT_NO_TEXT`, `OUTPUT_SCHEMA_INVALID`, `OUTPUT_SEMANTIC_INVALID`, `OUTPUT_POLICY_VIOLATION`, `MEDIA_CHUNKS_MISSING`, `MEDIA_CORRUPT`, `UNSUPPORTED_SCHEMA_VERSION`, `INTERNAL_ERROR` |
| `errorClass` | enum | yes | values: `TRANSIENT`, `PERMANENT` |
| `retryable` | boolean | yes |  |
| `message` | string | yes | maxLen 300 |
| `run` | RunMeta (see H.1) | yes |  |

Examples produced by the real code:

`PROVIDER_UNAVAILABLE` (transient, retryable):

```json
{
  "jobId": "9c1f0002-0000-4000-8000-000000000002",
  "attempt": 1,
  "errorCode": "PROVIDER_UNAVAILABLE",
  "errorClass": "TRANSIENT",
  "retryable": true,
  "message": "Injected mock failure (MOCK_FAILURE_RATE).",
  "run": null
}
```

`DEADLINE_EXCEEDED` (request received after its deadline):

```json
{
  "jobId": "9c1f0002-0000-4000-8000-000000000002",
  "attempt": 1,
  "errorCode": "DEADLINE_EXCEEDED",
  "errorClass": "TRANSIENT",
  "retryable": true,
  "message": "Deadline passed before processing started.",
  "run": null
}
```

`UNSUPPORTED_SCHEMA_VERSION` (the request asked for `cv_match.v9`):

```json
{
  "jobId": "9c1f0002-0000-4000-8000-000000000002",
  "attempt": 1,
  "errorCode": "UNSUPPORTED_SCHEMA_VERSION",
  "errorClass": "PERMANENT",
  "retryable": false,
  "message": "Worker does not implement the requested output schema version.",
  "run": null
}
```

`OUTPUT_POLICY_VIOLATION` (TTS text containing a `[CANDIDATE]` placeholder):

```json
{
  "jobId": "9c1f0008-0000-4000-8000-000000000008",
  "attempt": 1,
  "errorCode": "OUTPUT_POLICY_VIOLATION",
  "errorClass": "PERMANENT",
  "retryable": false,
  "message": "Synthesis text contains personal data or placeholders.",
  "run": null
}
```

`message` is at most 300 characters and never contains personal data, CV or transcript text, tokens or URLs (A.3). `run` is `null` in these examples; it is filled in when a model or provider call has been made.

### B.12 Schema versioning rules (A.2, A.3, A.9)

- `eventVersion` is `1` for every topic at launch. A breaking change creates a new topic with the suffix `.v2`: the producer publishes to both topics for one release, the consumer migrates, and the old topic is removed in the following release.
- A backward-compatible change (a new optional field) keeps the same topic version. See P-19 for the deployment order.
- `outputSchemaVersion` (for example `cv_profile.v1`) names the artefact schema. A worker that does not implement the requested version answers `.failed` `UNSUPPORTED_SCHEMA_VERSION`. The artefact schemas follow the same rule.
- Every schema is JSON Schema draft 2020-12 with `additionalProperties: false` on every object. An extra field is a validation failure.
- Any change under `contracts/` needs approval from both team leads in the same pull request (S-12, CODEOWNERS); the `contracts-check` CI job validates every fixture against its schema.

> **P-19 — Roll-out order for additive schema changes**  
> **Status:** Pending confirmation  
> **Detail:** A.9 says a new optional field keeps the same topic version. Every object has `additionalProperties: false` and consumers validate on receipt, so a consumer that does not yet have the updated schema rejects a message containing the new field. The order in which producer and consumer deploy is not stated.  
> **Platform impact:** Both teams must agree that the consumer deploys the updated schema before the producer emits the new field. Otherwise messages are dead-lettered.


### B.13 Additional events during processing

None. The specification defines no progress, heartbeat or intermediate events. Each request produces exactly one `.completed` or one `.failed` (a duplicate `.completed` is possible, see H.4), or — if the message is invalid — one record on `<request topic>.dlq` and no result event (see H.3 and P-18).

## C. Interview plan generation: `interview_plan.v1`

**Confirmed design:** §7.3 (field table and validation), A-25 (allocation), A.5 and A.7. **Implementation: pending (Phase 4).** The schema file `contracts/ai-schemas/interview_plan.v1.json` and its fixtures are implemented; mock mode returns the fixture.

### C.1 Final structure

| Path | Type | Required | Constraints |
|---|---|---|---|
| `questions` | array | yes | minItems 4; maxItems 10 |
| `questions[]` | object | yes |  |
| `questions[].questionId` | string | yes | pattern `^q([1-9]\|10)$` |
| `questions[].slot` | integer | yes | min 1; max 10 |
| `questions[].text` | string | yes | minLen 20; maxLen 300 |
| `questions[].competencyCodes` | array of string | yes | minItems 1; maxItems 2; item minLen 1 |
| `questions[].questionType` | enum | yes | values: `TECHNICAL_CONCEPT`, `SCENARIO`, `PAST_EXPERIENCE`, `PROBLEM_SOLVING`, `ROLE_MOTIVATION` |
| `questions[].expectedAnswerRubric` | object | yes |  |
| `questions[].expectedAnswerRubric.keyPoints` | array of string | yes | minItems 3; maxItems 6; item maxLen 200 |
| `questions[].expectedAnswerRubric.strongSignals` | array of string | yes | minItems 1; maxItems 4; item maxLen 200 |
| `questions[].expectedAnswerRubric.weakSignals` | array of string | yes | minItems 1; maxItems 4; item maxLen 200 |
| `questions[].timeBudgetSeconds` | integer | yes | min 60; max 360 |
| `questions[].cvAnchor` | object, nullable | yes |  |
| `questions[].cvAnchor.claim` | string | yes | maxLen 200 |
| `questions[].cvAnchor.quote` | string | yes | minLen 8; maxLen 300 |
| `questions[].source` | enum | yes | values: `AI_GENERATED`, `FALLBACK_BANK` |
| `coverage` | object (map) | yes | map of key -> array array of ^q([1-9]\|10)$ |
| `warnings` | array of enum | yes | item values: `QUESTIONS_REPLACED_BY_FALLBACK`, `CV_CONTEXT_MISSING` |

Stored at `ihr-generated-private/interview/{interviewId}/plans/{jobId}.json` (mime type `application/json`). Complete example (the repository fixture, passed through the mock worker):

```json
{
  "questions": [
    {
      "questionId": "q1",
      "slot": 1,
      "competencyCodes": [
        "PRACTICAL_EXPERIENCE",
        "TECHNICAL_SKILL"
      ],
      "questionType": "PAST_EXPERIENCE",
      "text": "You mention designing event pipelines on Apache Kafka for settlement batches. Walk me through your own role in that work and the result.",
      "expectedAnswerRubric": {
        "keyPoints": [
          "Describes the pipeline's purpose",
          "States own decisions and actions",
          "Describes the outcome or impact"
        ],
        "strongSignals": [
          "Names concrete design choices and why"
        ],
        "weakSignals": [
          "Describes only team work without own role"
        ]
      },
      "timeBudgetSeconds": 180,
      "cvAnchor": {
        "claim": "Designed Kafka event pipelines for settlement batches",
        "quote": "Designed event pipelines on Apache Kafka for settlement batches"
      },
      "source": "AI_GENERATED"
    },
    {
      "questionId": "q2",
      "slot": 2,
      "competencyCodes": [
        "TECHNICAL_SKILL"
      ],
      "questionType": "TECHNICAL_CONCEPT",
      "text": "How does Kafka guarantee message ordering, and what limits that guarantee in a system with several consumers?",
      "expectedAnswerRubric": {
        "keyPoints": [
          "Ordering is per partition",
          "Keys map messages to partitions",
          "Consumer group assigns partitions to consumers"
        ],
        "strongSignals": [
          "Mentions rebalancing or retries affecting order"
        ],
        "weakSignals": [
          "Claims global ordering across topics"
        ]
      },
      "timeBudgetSeconds": 180,
      "cvAnchor": null,
      "source": "AI_GENERATED"
    },
    {
      "questionId": "q3",
      "slot": 3,
      "competencyCodes": [
        "PROBLEM_SOLVING"
      ],
      "questionType": "SCENARIO",
      "text": "A settlement job that ran fine yesterday now takes three times longer. How would you find the cause?",
      "expectedAnswerRubric": {
        "keyPoints": [
          "Checks what changed",
          "Uses metrics or logs to locate the slow step",
          "Forms and tests hypotheses"
        ],
        "strongSignals": [
          "Compares options and validates the fix"
        ],
        "weakSignals": [
          "Guesses a fix without investigation"
        ]
      },
      "timeBudgetSeconds": 180,
      "cvAnchor": null,
      "source": "AI_GENERATED"
    },
    {
      "questionId": "q4",
      "slot": 4,
      "competencyCodes": [
        "TECHNICAL_SKILL"
      ],
      "questionType": "TECHNICAL_CONCEPT",
      "text": "Explain when you would choose a database transaction over an eventually consistent design in a payments service.",
      "expectedAnswerRubric": {
        "keyPoints": [
          "Defines transactional guarantees",
          "Explains eventual consistency",
          "Relates the choice to payment correctness"
        ],
        "strongSignals": [
          "Discusses trade-offs such as latency and failure handling"
        ],
        "weakSignals": [
          "Gives a definition only"
        ]
      },
      "timeBudgetSeconds": 180,
      "cvAnchor": null,
      "source": "AI_GENERATED"
    },
    {
      "questionId": "q5",
      "slot": 5,
      "competencyCodes": [
        "COMMUNICATION"
      ],
      "questionType": "SCENARIO",
      "text": "How would you explain a production incident in your service to a business stakeholder who is not technical?",
      "expectedAnswerRubric": {
        "keyPoints": [
          "States impact in business terms",
          "Explains cause simply",
          "Describes next steps"
        ],
        "strongSignals": [
          "Structures the explanation clearly"
        ],
        "weakSignals": [
          "Uses unexplained jargon"
        ]
      },
      "timeBudgetSeconds": 120,
      "cvAnchor": null,
      "source": "AI_GENERATED"
    },
    {
      "questionId": "q6",
      "slot": 6,
      "competencyCodes": [
        "ROLE_FIT"
      ],
      "questionType": "ROLE_MOTIVATION",
      "text": "Which parts of this backend role match your experience most closely, and which would you need to build up?",
      "expectedAnswerRubric": {
        "keyPoints": [
          "Links experience to must-have requirements",
          "Gives an example",
          "Names a realistic area to develop"
        ],
        "strongSignals": [
          "Links to both must-have and preferred requirements"
        ],
        "weakSignals": [
          "Generic answer without examples"
        ]
      },
      "timeBudgetSeconds": 120,
      "cvAnchor": null,
      "source": "AI_GENERATED"
    }
  ],
  "coverage": {
    "PRACTICAL_EXPERIENCE": [
      "q1"
    ],
    "TECHNICAL_SKILL": [
      "q1",
      "q2",
      "q4"
    ],
    "PROBLEM_SOLVING": [
      "q3"
    ],
    "COMMUNICATION": [
      "q5"
    ],
    "ROLE_FIT": [
      "q6"
    ]
  },
  "warnings": []
}
```

### C.2 Which fields are in the final output, and who produces them

| Field | In the final output? | Produced by | Enforced or set by code |
|---|---|---|---|
| `questions[]` | yes, exactly `mainQuestionCount` items (schema: 4–10) | model, one per slot | code checks the count against the request |
| `questionId` | **yes**, `q1`…`qN` | **code** (after validation) | numbered in slot order; unique only inside one plan |
| `slot` | **yes**, integer 1..N | model returns it | code requires it to equal the question's position |
| `order` | **no** — not in `interview_plan.v1` (see P-01) | — | — |
| `kind` | **no** — not in `interview_plan.v1` (see P-01) | — | — |
| `text` | yes, 20–300 characters | model | policy check A-21 (≤ 300 characters, ≤ 2 question marks, no candidate name or placeholder, no protected topic, no accusation, no document request, competency codes allowed); similarity to every other question below 0.85 (rapidfuzz `token_set_ratio / 100`) |
| `competencyCodes` | yes, 1–2 codes | model | the first code is the slot's competency; all codes must exist in the request's `competencies` |
| `questionType` | **yes** | model | must be one of `TECHNICAL_CONCEPT`, `SCENARIO`, `PAST_EXPERIENCE`, `PROBLEM_SOLVING`, `ROLE_MOTIVATION` (see P-04); at least two different types across the plan |
| `expectedAnswerRubric` | yes: `keyPoints` 3–6, `strongSignals` 1–4, `weakSignals` 1–4, each ≤ 200 characters | model | schema |
| `timeBudgetSeconds` | yes, 60–360 | model | the prompt asks for `maxAnswerSeconds`, or 120 for a narrow question |
| `cvAnchor` | **yes**, object `{claim ≤ 200, quote 8–300}` or `null` | model | the quote is verified against the CV profile or CV text (A-23); at most 2 questions in a plan have an anchor |
| `source` | **yes**: `AI_GENERATED` or `FALLBACK_BANK` | **code** | set per question |
| `coverage` | **yes**: map from competency code to a list of `questionId` | **code** | map of competency code to the `questionId`s that cover it (in the fixture, a question that lists two codes appears under both) |
| `warnings` | **yes**, array, possibly empty: `QUESTIONS_REPLACED_BY_FALLBACK`, `CV_CONTEXT_MISSING` | **code** | added by the worker |

The model returns only the fields marked "model" (schema `$defs.ModelOutput` in the schema file). Code adds `questionId`, `source`, `coverage` and `warnings`.

### C.3 How questions are allocated and replaced (code, before and after the model)

- **Allocation (A-25, §7.3):** code assigns the competency of each slot before the model writes any question: one slot per mandatory competency first (ordered by weight, highest first), then the remaining slots by largest remainder on weights, at most 3 slots per competency. Slots are interleaved so that the same competency is not asked twice in a row where possible. The model receives the numbered slots and writes exactly one question per slot.
- **Anchors:** at most 2 questions are anchored to CV claims.
- **Replacement:** a question that fails validation is replaced by the next unused fallback question of the slot's competency (from `competencies[].fallbackQuestions`, in bank order) and the plan gets the warning `QUESTIONS_REPLACED_BY_FALLBACK`. If more than half the questions are invalid, the worker repairs once; if that fails, the whole plan comes from the fallback bank.
- `CV_CONTEXT_MISSING` is listed in §7.3 without a stated trigger. The request field `cvProfile` is `null` when no parsed CV exists (A.5).

### C.4 What the Platform Backend does with the plan

- The plan is a finished artefact. **The specification does not require the Platform Backend to transform, re-validate or enrich it before storing or returning it.** Core receives the ObjectRef in the `.completed` event and reads the object (verify `sha256` from the ObjectRef).
- Core pre-renders TTS for every planned question using each question's `text` (S-18, §11.2) once the plan has completed.
- HR can see the plan before the interview and may edit questions (the Platform enum `PlanSource` has the value `HR_EDITED`). An edited question must pass the same question policy (A-21); core repeats the cheap checks using the shared lexicon file, which is not delivered yet (P-17).
- `questionId` values (`q1`…`q10`) are unique only within one plan.

> **P-01 — interview_plan.v1: `order` and `kind` fields**  
> **Status:** Pending confirmation  
> **Detail:** Appendix A.7 lists `order` and `kind` among the `questions[]` fields; the detailed §7.3 table, and the schema generated from it, has `slot` and has neither `order` nor `kind`. The schema follows §7.3 (ASSUMPTIONS #3). Neither lead has signed this off yet (S-12).  
> **Platform impact:** Platform must not read or store `order` or `kind` from plan questions until this is confirmed; the position of a question is `slot` (1..N). If a lead decides to add either field, the schema changes and the Platform storage model has to be re-checked.


> **P-02 — Fallback-bank questions inside a plan**  
> **Status:** Pending confirmation  
> **Detail:** Fallback questions reach the AI worker as plain strings (`competencies[].fallbackQuestions[]`), but a plan question must have `questionType`, `timeBudgetSeconds` and an `expectedAnswerRubric` with at least one `strongSignals` and one `weakSignals` entry. §7.3 only says the rubric for a fallback question is derived from anchors 4 and 5 (keyPoints) and does not state the `questionType`, the `timeBudgetSeconds`, or the strong/weak signal values for `source: FALLBACK_BANK` questions. Not implemented yet (Phase 4).  
> **Platform impact:** Platform cannot assume a particular `questionType` or time budget for `FALLBACK_BANK` questions (for example in UI filters or answer timers). It must read both from the stored plan.


> **P-03 — Plan-level `PlanSource` vs per-question `source`**  
> **Status:** Pending confirmation  
> **Detail:** Appendix B defines the Platform enum `PlanSource` = AI, FALLBACK, HR_EDITED. `interview_plan.v1` has a per-question `source` = AI_GENERATED, FALLBACK_BANK and the `.completed` extra `fallbackUsedCount`. No rule maps one to the other (for example, when a plan counts as FALLBACK). The shape of a plan that core builds itself from the rubric's fallback bank when no AI plan exists (S-18) is also not described by an AI-owned schema.  
> **Platform impact:** Platform cannot finalise how it derives or stores the plan-level source from the AI output, nor the stored shape of a core-built fallback plan.


> **P-04 — Allowed question types for a plan**  
> **Status:** Pending confirmation  
> **Detail:** The INTERVIEW_PLAN prompt restricts question types to `{allowedTypes}`. The plan request payload has no field that supplies this list, and the output schema accepts all five `QuestionType` values.  
> **Platform impact:** Platform cannot restrict question types per template or rubric through the current request contract. If a restriction is needed, the request schema has to change (contract change, S-12).


## D. Transcript generation: `transcript.v1`

**Confirmed design:** §11.1 (output schema and processing steps), A-26, A-27, A.5 and A.7. **Implementation:** the mock service is implemented and produces valid `transcript.v1`; the real faster-whisper service is pending (Phase 3). Schema file: `contracts/ai-schemas/transcript.v1.json`.

Reconciliation: A.7 summarises the schema as `language, durationMs, segments[], qualityFlags[]`. §11.1 gives the full object, which also has `schemaVersion`, `model`, `speechDetected` and `avgConfidence`. The schema follows §11.1 and all eight top-level fields are required (ASSUMPTIONS #2). The §11.1 example is valid against the schema.

### D.1 Structure

| Path | Type | Required | Constraints |
|---|---|---|---|
| `schemaVersion` | const `transcript.v1` | yes |  |
| `language` | const `en` | yes |  |
| `model` | string | yes | minLen 1 |
| `durationMs` | integer | yes | min 0 |
| `speechDetected` | boolean | yes |  |
| `avgConfidence` | number | yes | min 0; max 1 |
| `qualityFlags` | array of enum | yes | item values: `NO_SPEECH`, `LOW_CONFIDENCE`, `POSSIBLE_HALLUCINATION`, `CLIPPED_AUDIO`, `TRUNCATED_AT_LIMIT` |
| `segments` | array | yes |  |
| `segments[]` | object | yes |  |
| `segments[].index` | integer | yes | min 0 |
| `segments[].startMs` | integer | yes | min 0 |
| `segments[].endMs` | integer | yes | min 0 |
| `segments[].text` | string | yes |  |
| `segments[].confidence` | number | yes | min 0; max 1 |
| `segments[].noSpeechProb` | number | yes | min 0; max 1 |
| `segments[].words` | array | yes |  |
| `segments[].words[]` | object | yes |  |
| `segments[].words[].w` | string | yes |  |
| `segments[].words[].startMs` | integer | yes | min 0 |
| `segments[].words[].endMs` | integer | yes | min 0 |
| `segments[].words[].p` | number | yes | min 0; max 1 |

Stored at `ihr-generated-private/interview/{interviewId}/transcripts/{turnId}/{jobId}.json` (`application/json`). Complete example from §11.1 (checked against the schema):

```json
{
  "schemaVersion": "transcript.v1",
  "language": "en",
  "model": "large-v3-turbo",
  "durationMs": 83400,
  "speechDetected": true,
  "avgConfidence": 0.86,
  "qualityFlags": [],
  "segments": [
    {
      "index": 0,
      "startMs": 420,
      "endMs": 6180,
      "text": "In my last project we moved the settlement batch to Kafka.",
      "confidence": 0.91,
      "noSpeechProb": 0.02,
      "words": [
        {
          "w": "In",
          "startMs": 420,
          "endMs": 560,
          "p": 0.98
        }
      ]
    }
  ]
}
```

### D.2 Field meanings

| Field | Meaning |
|---|---|
| `schemaVersion` | constant `transcript.v1` |
| `language` | constant `en` (fixed in V1) |
| `model` | the model name. `large-v3-turbo` in QA/PROD (S-14); `small.en` on CPU is used in LOCAL/DEV; the mock emits `mock` |
| `durationMs` | audio duration in milliseconds (mock: the decoded duration, cut at `maxDurationSeconds`) |
| `speechDetected` | boolean. In the mock it is `false` exactly when `NO_SPEECH` is raised |
| `avgConfidence` | duration-weighted mean of segment `confidence`, range 0 to 1 |
| `qualityFlags` | zero or more of the values below |
| `segments[].index` | position, re-indexed from 0 after post-processing |
| `segments[].startMs`, `endMs` | milliseconds **relative to the start of the answer clip** (Platform §9.6 maps them to playback time) |
| `segments[].text` | segment text with whitespace trimmed |
| `segments[].confidence` | `exp(avg_logprob)` clamped to 0–1 |
| `segments[].noSpeechProb` | 0–1 |
| `segments[].words[]` | `w` (word), `startMs`, `endMs` (same clip-relative milliseconds), `p` (word probability, 0–1). The array is required and is empty in the mock |

Quality flags (A-27, Appendix B `TranscriptQualityFlag`):

| Flag | Raised when |
|---|---|
| `NO_SPEECH` | no segments after voice-activity detection, or total speech under 1.5 s |
| `LOW_CONFIDENCE` | duration-weighted mean of `exp(avg_logprob)` is below 0.55 |
| `POSSIBLE_HALLUCINATION` | any segment has compression ratio above 2.4, or a repeated n-gram (at least 4 words repeated at least 3 times) |
| `CLIPPED_AUDIO` | more than 1 % of samples at full scale |
| `TRUNCATED_AT_LIMIT` | audio longer than `maxDurationSeconds` (400); the first 400 s are transcribed |

Processing rules (§11.1): segments with `no_speech_prob > 0.6` and `avg_logprob < -1.0` are dropped; exact repeated adjacent segments are collapsed. Transcripts are stored **unredacted** because they are the HR record; redaction happens only inside the AI worker before text goes to the model. Treat the object as personal data.

### D.3 Mock behaviour (`STT_PROVIDER=mock`, §11.3, A.8 — implemented)

- One segment per 5 s of audio, text `This is a mock answer for testing.`, `confidence` 0.9, `noSpeechProb` 0, `words: []`, `model: "mock"`, `avgConfidence` 0.9.
- The duration is measured by decoding the audio (PyAV). A clip longer than `maxDurationSeconds` is cut to that length and flagged `TRUNCATED_AT_LIMIT`.
- An audio file smaller than 1 KB gives the no-speech result: `speechDetected: false`, `qualityFlags: ["NO_SPEECH"]`, `segments: []`, `avgConfidence: 0.0`, `durationMs: 0`.

Mock no-speech transcript (artefact):

```json
{
  "schemaVersion": "transcript.v1",
  "language": "en",
  "model": "mock",
  "durationMs": 0,
  "speechDetected": false,
  "avgConfidence": 0.0,
  "qualityFlags": [
    "NO_SPEECH"
  ],
  "segments": []
}
```

Corresponding `.completed` payload:

```json
{
  "jobId": "9c1f0007-0000-4000-8000-000000000007",
  "attempt": 1,
  "output": {
    "bucket": "ihr-generated-private",
    "key": "interview/6de6a7b8-c9d0-4e1f-9a2b-3c4d5e6f7a8b/transcripts/b7e1c2d3-e4f5-4a6b-8c7d-8e9f0a1b2c3d/9c1f0007-0000-4000-8000-000000000007.json",
    "sha256": "1d1637fc19f399995167952405e096f7d350fc1314131d86be8e5ca546be84a0",
    "sizeBytes": 165,
    "mimeType": "application/json"
  },
  "outputSchemaVersion": "transcript.v1",
  "run": {
    "provider": "mock",
    "model": "mock",
    "promptId": null,
    "promptVersion": null,
    "promptSha256": null,
    "providerRequestId": null,
    "latencyMs": 0,
    "inputTokens": null,
    "outputTokens": null,
    "providerAttempts": 1,
    "repairAttempted": false,
    "validationStatus": "VALID",
    "workerVersion": "1.0.0+dev"
  },
  "durationMs": 0,
  "segmentCount": 0,
  "avgConfidence": 0.0,
  "speechDetected": false,
  "qualityFlags": [
    "NO_SPEECH"
  ]
}
```

Mock transcript of a 12 s clip (3 segments):

```json
{
  "schemaVersion": "transcript.v1",
  "language": "en",
  "model": "mock",
  "durationMs": 12000,
  "speechDetected": true,
  "avgConfidence": 0.9,
  "qualityFlags": [],
  "segments": [
    {
      "index": 0,
      "startMs": 0,
      "endMs": 5000,
      "text": "This is a mock answer for testing.",
      "confidence": 0.9,
      "noSpeechProb": 0.0,
      "words": []
    },
    {
      "index": 1,
      "startMs": 5000,
      "endMs": 10000,
      "text": "This is a mock answer for testing.",
      "confidence": 0.9,
      "noSpeechProb": 0.0,
      "words": []
    },
    {
      "index": 2,
      "startMs": 10000,
      "endMs": 12000,
      "text": "This is a mock answer for testing.",
      "confidence": 0.9,
      "noSpeechProb": 0.0,
      "words": []
    }
  ]
}
```

### D.4 Real service targets and failure handling (confirmed design, not implemented yet)

- Targets on one NVIDIA L4 with `large-v3-turbo` float16: p95 ≤ 4 s for 180 s of audio; p95 ≤ 6 s from event in to event out at design load (§11.1). Core's own cut-off is 25 s (S-09).
- The model is loaded at start-up and the pod reports ready only after a warm-up transcription of a bundled 3-second clip.
- CUDA out of memory: the model is reloaded once and the job retried; a second failure gives `INTERNAL_ERROR` (retryable) and the pod's liveness fails so that Kubernetes restarts it. Decode errors give `INPUT_UNSUPPORTED_FORMAT`.

## E. Text-to-speech: `tts_audio.v1`

**Confirmed design:** §11.2, A-28, S-13, A.5, A.6. **Implementation:** the mock provider, the cache and the personal-data guard are implemented; Azure and Polly are pending (Phase 4).

### E.1 What the service returns

There is **no JSON artefact** for TTS. The audio is the output object; the metadata travels in the `.completed` event:

- `output`: ObjectRef of the MP3 at `ihr-generated-private/tts/{voiceProfileKey}/{cacheKey}.mp3`.
- `durationMs`, `provider`, `voiceId`, `cacheHit`: four inline fields at the **top level of the event payload**, next to `output` (not inside the ObjectRef and not inside `run`). `tts_audio.v1.json` is the schema of exactly these four fields:

| Path | Type | Required | Constraints |
|---|---|---|---|
| `durationMs` | integer | yes | min 0 |
| `provider` | string | yes | minLen 1 |
| `voiceId` | string | yes | minLen 1 |
| `cacheHit` | boolean | yes |  |

| Field | Meaning |
|---|---|
| `durationMs` | duration of the audio. Real providers: measured with PyAV on the decoded MP3 (§11.2 step 5). Mock: see E.3. |
| `provider` | the provider that produced the audio. RunMeta values in A.3: `azure-speech`, `aws-polly`. The mock emits `mock`. |
| `voiceId` | the voice. Azure `en-IN-NeerjaNeural`, Polly `Kajal` (S-13). The mock emits `mock-tone`. |
| `cacheHit` | `true` when the audio already existed under the key and no provider was called |

ObjectRef (A.3) for the MP3: `{bucket, key, sha256, sizeBytes, mimeType}` — all five required (table in section G). Complete `.completed` payload, first request (mock):

```json
{
  "jobId": "9c1f0008-0000-4000-8000-000000000008",
  "attempt": 1,
  "output": {
    "bucket": "ihr-generated-private",
    "key": "tts/en-default/79491119a8f5e41be5a4001803f3da8f567466eee821cf92a03e95295401e715.mp3",
    "sha256": "f541c0465be30217a63f63a28c1a8f78a35200af921caeaf8734d8cb6a08ad30",
    "sizeBytes": 8492,
    "mimeType": "audio/mpeg"
  },
  "outputSchemaVersion": "tts_audio.v1",
  "run": {
    "provider": "mock",
    "model": "mock-tone",
    "promptId": null,
    "promptVersion": null,
    "promptSha256": null,
    "providerRequestId": null,
    "latencyMs": 0,
    "inputTokens": null,
    "outputTokens": null,
    "providerAttempts": 1,
    "repairAttempted": false,
    "validationStatus": "VALID",
    "workerVersion": "1.0.0+dev"
  },
  "durationMs": 4900,
  "provider": "mock",
  "voiceId": "mock-tone",
  "cacheHit": false
}
```

Second request with the same `cacheKey` (the object and its sidecar exist, so no synthesis happens):

```json
{
  "jobId": "9c1f0008-0000-4000-8000-000000000008",
  "attempt": 1,
  "output": {
    "bucket": "ihr-generated-private",
    "key": "tts/en-default/79491119a8f5e41be5a4001803f3da8f567466eee821cf92a03e95295401e715.mp3",
    "sha256": "f541c0465be30217a63f63a28c1a8f78a35200af921caeaf8734d8cb6a08ad30",
    "sizeBytes": 8492,
    "mimeType": "audio/mpeg"
  },
  "outputSchemaVersion": "tts_audio.v1",
  "run": {
    "provider": "mock",
    "model": "mock-tone",
    "promptId": null,
    "promptVersion": null,
    "promptSha256": null,
    "providerRequestId": null,
    "latencyMs": 0,
    "inputTokens": null,
    "outputTokens": null,
    "providerAttempts": 1,
    "repairAttempted": false,
    "validationStatus": "VALID",
    "workerVersion": "1.0.0+dev"
  },
  "durationMs": 4900,
  "provider": "mock",
  "voiceId": "mock-tone",
  "cacheHit": true
}
```

> **P-05 — TTS result details not fixed by the spec**  
> **Status:** Pending confirmation  
> **Detail:** `tts_audio.v1` is not defined as a stored JSON artefact in §7 or A.7. A.5 only lists the inline extras `durationMs`, `provider`, `voiceId`, `cacheHit`. Implemented: those four fields are top-level fields of the `.completed` payload, next to `output` (ASSUMPTIONS #1, #6), and `tts_audio.v1.json` validates exactly that set. The spec does not state (a) the `mimeType` of the MP3 ObjectRef (the mock emits `audio/mpeg`) or (b) the set of values of the inline `provider` field (A.3 lists `azure-speech` and `aws-polly` for RunMeta; the mock emits `mock`).  
> **Platform impact:** Platform should not enum-validate `provider` or `voiceId`, and should not hard-code the MIME type of the TTS ObjectRef beyond "audio".


### E.2 Behaviour (confirmed design)

- **Cache:** the output key is content-addressed (`cacheKey`). If the object and its sidecar `.meta.json` exist, the service returns `.completed` with `cacheHit=true` and calls no provider. Fixed texts (opening, warm-up, closing, system messages) are shared across interviews through the cache key and rendered once per template version and voice. Pre-render requests have `interviewId: null` and `turnId: null`.
- **Providers (§11.2):** Azure AI Speech is primary (SSML, voice `en-IN-NeerjaNeural`, `audio-24khz-48kbitrate-mono-mp3`, 6 s timeout). Amazon Polly (neural, voice `Kajal`, `en-IN`, MP3 24 kHz, 6 s timeout) is the automatic failover after a timeout, HTTP 429, 5xx, or an open circuit. Each provider has its own circuit breaker: 5 consecutive failures open it for 60 s.
- **Audio validation:** non-empty, decodable, duration of at least 300 ms.
- **Text guard:** a text containing `[CANDIDATE]`, `[EMAIL]`, `[PHONE]` or an email or phone pattern is rejected with `OUTPUT_POLICY_VIOLATION` (permanent). Maximum 600 characters per request (schema: 1–600). Only template or AI-written question text may be sent (S-10).
- **Failure:** both providers failed → `.failed` `PROVIDER_UNAVAILABLE` (retryable). Core then falls back to showing the text; a follow-up whose audio is not ready within 10 s is shown as text with an "audio unavailable" note (S-09).
- **What the Platform Backend does additionally:** compute the `cacheKey` (P-06), send pre-render requests, create a presigned download URL for the MP3 when the browser needs it (A.6: reader is core, by presign), and apply the text fallback above. No post-processing of the audio is required.

> **P-06 — Definition of `normalisedText` in `cacheKey`**  
> **Status:** Pending confirmation  
> **Detail:** `cacheKey = sha256(normalisedText + "|" + voiceProfileKey + "|" + ttsProfileVersion)` is computed by core (A.5). `normalisedText` is not defined. §11.2 step 2 describes the speech service's own normalisation (collapse whitespace, speech-only lexicon substitutions, XML escaping). The service uses the supplied `cacheKey` as given and never recomputes or verifies it. If an object already exists under a key, it returns that audio with `cacheHit=true` without comparing text.  
> **Platform impact:** Core must define its own normalisation. If core ever reuses one `cacheKey` for different texts, the service returns the earlier audio. Different keys for the same text only cause extra renders.


### E.3 Mock TTS (`TTS_PROVIDER_PRIMARY=mock` — implemented)

| Aspect | Behaviour |
|---|---|
| Audio | the bundled file `services/ihr-speech-service/speech/assets/mock_tone.mp3`: **a 2,000 ms tone**, MP3 at 24 kHz, 8,492 bytes. The same bytes are returned for every text. |
| `durationMs` | **350 × the number of whitespace-separated words in `text`**. The 14-word fixture text gives `4900`, while the audio itself plays for 2,000 ms. |
| `provider`, `voiceId`, `run.model` | `mock`, `mock-tone`, `mock-tone` |
| Cache | the same cache and `.meta.json` sidecar as the real service; a repeated `cacheKey` returns `cacheHit: true` |
| Text guard | active (a placeholder, email or phone number fails with `OUTPUT_POLICY_VIOLATION`) |
| ObjectRef `mimeType` | `audio/mpeg` |

Difference between the two specification sentences: **A.8** says "a bundled 2-second MP3 tone; duration = 350 ms × word count"; **§11.3** says "a bundled MP3 tone with duration 350 ms × word count". The implementation uses a fixed 2-second file and reports the §11.3 formula as `durationMs`.

> **P-07 — Mock TTS: Appendix A.8 vs §11.3**  
> **Status:** Pending confirmation  
> **Detail:** A.8 says: "Returns a bundled 2-second MP3 tone; duration = 350 ms × word count". §11.3 says: "returns a bundled MP3 tone with duration 350 ms × word count". Implemented (ASSUMPTIONS #4): the bundled file is a fixed 2,000 ms tone (8,492 bytes, 24 kHz). The reported `durationMs` is 350 × the number of whitespace-separated words in `text`, so it does not match the audio's real length (a 14-word text reports 4,900 ms and plays for 2,000 ms). The two sentences have not been reconciled by the leads. Real providers will report the PyAV-measured duration of the decoded audio (§11.2 step 5).  
> **Platform impact:** In mock and LOCAL runs, `durationMs` must not drive UI timers or progress bars; use the audio element's own events. Production values will be the real audio duration.


## F. Additional payload fields: `priorContext`, `assessmentStatus`, `proctoringSummary`

All three are **inputs populated by the Platform Backend** (core). The AI services never generate, change or return them. Status of each: the field exists in the approved A.5; its exact structure is an AI-team schema decision that both leads have not yet approved, as marked.

| Field | Request | Generated by | Owned and updated by | Mandatory | Allowed values |
|---|---|---|---|---|---|
| `priorContext` | `ihr.interview.answer-assessment.requested` | no (input) | Platform core | **yes** (the key must be present; `[]` when there is no prior context) | 0–2 entries of `{questionText, transcriptExcerpt}` — structure pending (P-11) |
| `turns[].assessmentStatus` | `ihr.interview.final-report.requested` | no (input) | Platform core (set by core; the AI services do not produce it) | **yes**, for every turn | `QUEUED`, `RUNNING`, `SUCCEEDED`, `RETRYING`, `FAILED`, `DEAD_LETTERED`, `CANCELLED` — value set pending (P-08) |
| `proctoringSummary` | `ihr.interview.final-report.requested` | no (input) | Platform core | **yes** (the key must be present; `{}` when there are no events) | keys: the 12 `ProctoringEventType` values; values: integers ≥ 0 — structure pending (P-12) |

### F.1 `priorContext`

Meaning (A.5): for a `FOLLOW_UP` turn, the parent main question text and its transcript excerpt (at most 1,200 characters). For other turns, an empty array.

```json
[]
```

The repository fixture is a MAIN turn and so has `[]`. A follow-up example (checked against the schema, not a fixture):

```json
{
  "turnKind": "FOLLOW_UP",
  "priorContext": [
    {
      "questionText": "How does Kafka guarantee message ordering, and what limits that guarantee?",
      "transcriptExcerpt": "Ordering is guaranteed per partition. Messages with the same key go to the same partition, so …"
    }
  ]
}
```

| Path | Type | Required | Constraints |
|---|---|---|---|
| `priorContext` | array | yes | 0–2 items |
| `priorContext[].questionText` | string | yes | minimum length 1 |
| `priorContext[].transcriptExcerpt` | string | yes | at most 1,200 characters |

> **P-11 — `priorContext` field names and the meaning of "0–2"**  
> **Status:** Pending confirmation  
> **Detail:** A.5 describes `priorContext` as an array of 0–2 entries holding, for a follow-up, "the parent main question text and its transcript excerpt (≤ 1,200 chars)"; empty otherwise. It does not give field names. Implemented: each entry is `{questionText, transcriptExcerpt}` (ASSUMPTIONS #11). The spec defines the content of one such entry and does not say what a second entry would hold.  
> **Platform impact:** Platform cannot finalise the field names or whether it may ever send two entries. Only the shape in section F is accepted by the schema.


### F.2 `assessmentStatus` and the turns array of the final report

`turns[]` in `ihr.interview.final-report.requested` (full table in B.8). Real fixture entry (first turn):

```json
{
  "turnId": "b7e1c2d3-e4f5-4a6b-8c7d-8e9f0a1b2c3d",
  "turnNumber": 1,
  "kind": "MAIN",
  "questionText": "You mention designing event pipelines on Apache Kafka for settlement batches. Walk me through your own role in that work and the result.",
  "competencyCodes": [
    "PRACTICAL_EXPERIENCE",
    "TECHNICAL_SKILL"
  ],
  "transcript": {
    "bucket": "ihr-generated-private",
    "key": "interview/6de6a7b8-c9d0-4e1f-9a2b-3c4d5e6f7a8b/transcripts/b7e1c2d3-e4f5-4a6b-8c7d-8e9f0a1b2c3d/9c1f0010-0000-4000-8000-000000000010.json",
    "sha256": "a7910c3799926f3bb3e89425836c6f837040aa323ab00c1372c24fae88c87c63",
    "sizeBytes": 1079,
    "mimeType": "application/json"
  },
  "assessment": {
    "bucket": "ihr-generated-private",
    "key": "interview/6de6a7b8-c9d0-4e1f-9a2b-3c4d5e6f7a8b/assessments/b7e1c2d3-e4f5-4a6b-8c7d-8e9f0a1b2c3d/9c1f0005-0000-4000-8000-000000000005.json",
    "sha256": "c65d9734cad06e8c6ff40781460a1b88d1ead5300d4d624c76dc08e17366226c",
    "sizeBytes": 1229,
    "mimeType": "application/json"
  },
  "transcriptStatus": "SUCCEEDED",
  "assessmentStatus": "SUCCEEDED"
}
```

`transcriptStatus` uses the Appendix B enum `TranscriptStatus`: `NOT_REQUESTED`, `QUEUED`, `SUCCEEDED`, `NO_SPEECH`, `FAILED`, `RETRYING`. `kind` uses `TurnKind`: `WARMUP`, `MAIN`, `FOLLOW_UP`, `CLOSING`. `transcript` and `assessment` are each an ObjectRef or `null`.

> **P-08 — `assessmentStatus` values in the final-report request**  
> **Status:** Pending confirmation  
> **Detail:** A.5 lists `turns[].assessmentStatus` but Appendix B defines no enum for it (it defines TranscriptStatus, FollowupStatus, AnswerStatus and ProcessingJobStatus). The schema uses the `ProcessingJobStatus` values QUEUED, RUNNING, SUCCEEDED, RETRYING, FAILED, DEAD_LETTERED, CANCELLED. This is an AI-team assumption (ASSUMPTIONS #12), not an approved decision.  
> **Platform impact:** Platform decides what it sends. A value outside these seven makes the whole request invalid: it is dead-lettered and no report is produced. Confirm the value set before coding the mapping.


> **P-09 — How skipped or unanswered turns are conveyed to FINAL_REPORT**  
> **Status:** Pending confirmation  
> **Detail:** §7.6 builds the report's `<interview_facts>` from "skipped questions", "answers with low transcript confidence", "interruptions/resumes" and "technical events". The final-report request has no per-turn `AnswerStatus` (SKIPPED, NO_ANSWER) and no interruption count. The AI worker can only see each turn's nullable `transcript` and `assessment` ObjectRefs, `transcriptStatus`, `assessmentStatus`, and the `proctoringSummary` counts (including SESSION_PAUSED and SESSION_RESUMED). How "skipped" and "interruptions" are derived from them is not specified. FINAL_REPORT is not implemented yet (Phase 6).  
> **Platform impact:** Platform cannot finalise how a skipped or unanswered turn is represented in `turns[]` (for example `transcript: null` with a particular `transcriptStatus`) until this is confirmed.


> **P-10 — Required fields of `competencies[]` in the final-report request**  
> **Status:** Pending confirmation  
> **Detail:** A.5 says `competencies` is "Full rubric competencies". The generated schema therefore requires every item to carry `code`, `name`, `description`, `weight`, `mandatory`, `anchors` ("1".."5") and `fallbackQuestions` (2–6 strings). The report prompt itself only uses code, name, weight, mandatory and the computed score.  
> **Platform impact:** A final-report request whose competencies omit `anchors` or `fallbackQuestions` is currently rejected (dead-lettered). Send the full rubric competency object, as in the fixture, until the leads decide whether to relax this (contract change, S-12).


> **P-22 — Numbering of the `T#` turn labels in `final_report.v1`**  
> **Status:** Pending confirmation  
> **Detail:** `final_report.v1` evidence references are `{turn: "T#", segmentIndex}` (pattern `^T[1-9]\d*$`). §5.1 says turns are referenced as T1…Tn and mapped back in code, and the stored artefact keeps the label (ASSUMPTIONS #15). The specification does not say how `n` is assigned: by position in `turns[]` or by `turnNumber`. The only fixture has one turn, so it does not settle it. FINAL_REPORT is not implemented yet (Phase 6).  
> **Platform impact:** Core or the UI that turns an evidence reference into a link to a turn and transcript segment needs the numbering rule. The stored artefact contains no `turnId`.


### F.3 `proctoringSummary`

Meaning (A.5): counts by event type only, no interpretation; used solely for the wording of the report's `limitations` section. Proctoring events are never used in any score (§8.4).

Fixture value:

```json
{
  "TAB_HIDDEN": 1
}
```

Allowed keys (Appendix B `ProctoringEventType`): `CAMERA_TRACK_ENDED`, `MIC_TRACK_ENDED`, `TAB_HIDDEN`, `WINDOW_BLUR`, `NETWORK_OFFLINE`, `SESSION_PAUSED`, `SESSION_RESUMED`, `PAGE_RELOADED`, `SCREEN_SHARE_STOPPED`, `SECOND_DEVICE_ATTEMPT`, `RECORDING_UPLOAD_GAP`, `TECHNICAL_ISSUE_REPORTED`. Values are integers of 0 or more. Any other key is a validation failure.

> **P-12 — `proctoringSummary` shape and counting rules**  
> **Status:** Pending confirmation  
> **Detail:** A.5 says "counts by event type only (no interpretation)". Implemented: a map from `ProctoringEventType` (12 values, Appendix B) to a non-negative integer, with unknown keys rejected (ASSUMPTIONS #13). Not specified: whether every event is counted or only those of severity REVIEW, whether zero counts are listed or omitted (the schema accepts both), and what happens to durations (S-23 mentions durations for TAB_HIDDEN and WINDOW_BLUR; the summary carries counts only).  
> **Platform impact:** Platform cannot finalise how it computes the summary. Any key outside the 12 enum values causes the request to be rejected.


### F.4 One more request-shape point on competency codes (WARMUP and CLOSING turns)

> **P-13 — Empty `question.competencyCodes` for WARMUP and CLOSING turns**  
> **Status:** Pending confirmation  
> **Detail:** §7.5 says that for WARMUP and CLOSING turns `<competencies>` is empty. The answer-assessment request schema allows `competencies: []` but requires `question.competencyCodes` to contain at least one item; the follow-up request schema has the same rule. Checked with the real validator: an empty `question.competencyCodes` is rejected (`[] should be non-empty`). The spec does not say which competency codes a WARMUP or CLOSING question carries.  
> **Platform impact:** Until this is confirmed, an answer-assessment request for a WARMUP or CLOSING turn with `competencyCodes: []` is dead-lettered and no result event is produced.


## G. Object storage and media exchange

**Confirmed design:** A.3 (ObjectRef), A.6 (storage layout), S-02, A-12. The AI services do not handle video or the media topics; the only media they read is the answer audio clip.

### G.1 ObjectRef

Used whenever a payload points at storage (A.3). All five fields are required and no other field is allowed.

| Field | Type | Meaning |
|---|---|---|
| `bucket` | string | bucket name |
| `key` | string | object key (opaque UUIDs only) |
| `sha256` | string | hex, 64 characters; of the stored bytes |
| `sizeBytes` | integer ≥ 0 | size of the stored bytes |
| `mimeType` | string | media type of the object |

Real example (the CV profile ObjectRef from a mock `cv.parse.completed`):

```json
{
  "bucket": "ihr-generated-private",
  "key": "cv/0b8c3f1e-6a41-4a8e-9a0e-2b4c0d6c1f10/profile/9c1f0001-0000-4000-8000-000000000001.json",
  "sha256": "92f5ae18e2a49e55fc630161335fe96c84277161cccec8ac11d2b5e2d2e7b109",
  "sizeBytes": 2361,
  "mimeType": "application/json"
}
```

### G.2 Which operation reads and writes what

| AI operation | Reads | Writes | Who uploads the input | Who consumes the output |
|---|---|---|---|---|
| CV_PARSE | `ihr-candidate-private` `candidate/{candidateId}/documents/{documentId}/original` | `ihr-generated-private` `cv/{documentId}/profile/{jobId}.json` and `cv/{documentId}/text/{jobId}.txt` | browser, by presigned URL | core; `ihr-ai-worker` (for later tasks) |
| CV_MATCH | profile and text objects named in the request (`ihr-generated-private`) | `application/{applicationId}/fit/{jobId}.json` | `ihr-ai-worker` (parse) | core |
| INTERVIEW_PLAN | the profile object, if `cvProfile` is not `null` | `interview/{interviewId}/plans/{jobId}.json` | `ihr-ai-worker` (parse) | core |
| FOLLOWUP | the transcript object | nothing; the output is inline in the event | `ihr-speech-stt` | core |
| ANSWER_ASSESSMENT | the transcript object | `interview/{interviewId}/assessments/{turnId}/{jobId}.json` | `ihr-speech-stt` | core; `ihr-ai-worker` (final report) |
| FINAL_REPORT | the transcript and assessment objects of each turn | `interview/{interviewId}/reports/{jobId}.json` | `ihr-speech-stt`, `ihr-ai-worker` | core |
| TRANSCRIPTION | `ihr-interview-raw` `interview/{interviewId}/answers/{turnId}/audio` (WebM/Opus or OGG/Opus, at most 10 MB) | `ihr-generated-private` `interview/{interviewId}/transcripts/{turnId}/{jobId}.json` | browser | core; `ihr-ai-worker` |
| SYNTHESIS | nothing (only the existence of the output key, for the cache) | `ihr-generated-private` `tts/{voiceProfileKey}/{cacheKey}.mp3` | — | core (presigned download) |
| Debug capture | — | `ihr-generated-private` `ai-runs/{yyyy-mm-dd}/{jobId}/request.json` and `response.json` | `ihr-ai-worker` | AI team only (break-glass), not Platform |

The Platform Backend receives every ObjectRef in the `.completed` event (`output`; for parse `output.cvProfile` and `output.cvText`), except for the follow-up decision which is inline.

### G.3 Content type and metadata

| Object | `mimeType` | Source |
|---|---|---|
| JSON artefacts (profile, fit, plan, assessment, report, transcript) | `application/json` | the ObjectRef example in A.3; the worker library writes JSON this way |
| CV text (`…/text/{jobId}.txt`) | `text/plain; charset=utf-8` | implementation; not stated in the spec |
| TTS audio | `audio/mpeg` in the mock; real value pending | see P-05 |

- Every key uses opaque UUIDs only. **No names, emails or phone numbers appear in any key, tag or object metadata** (A.6).
- Output objects are written with a `Content-MD5` header (§3.2 step 7).
- Next to each output object the worker writes a sidecar `{output key}.meta.json` holding the RunMeta and the inline extras. It exists so that a repeated request can be answered with the same `.completed` (see H.4). It is internal to the AI services; Platform does not read it. For TTS the sidecar also carries `durationMs`, `provider`, `voiceId`.

### G.4 Lifecycle and cleanup the Platform must account for

| Item | Rule |
|---|---|
| `ai-runs/…` debug captures | expire after 30 days by a bucket lifecycle rule (A-12); readable only by the AI lead (break-glass). The captured requests are already redacted. Debug capture can be switched off with `AI_DEBUG_CAPTURE=false`. |
| Kafka topics | retention 7 days; `<topic>.dlq` 30 days (A.4) |
| Re-dispatch | output keys do not contain `attempt`; a re-dispatch of the same `jobId` overwrites the same key |
| Deleting objects | the AI services never delete objects. Retention values and deletion of candidate data are Platform responsibilities (S-15, Appendix B `DeletionRequestStatus`; defaults are in the Platform document). A deletion job that removes an output object should also consider its `.meta.json` sidecar. |
| TTS objects | keyed by content (`cacheKey`), shared across interviews, and by design contain only template or question text (S-10); not per-candidate data |
| Logs | no payload bodies, `redactionHints`, CV text, transcript text, prompts, model responses or presigned URLs are ever logged (§3.2) |

## H. Error handling and processing lifecycle

### H.1 RunMeta

Present in every `.completed`, and in `.failed` when a model or provider call was made (otherwise `null`). Core stores it in `ai_runs`. Verbatim example from A.3 (placeholders as in the spec):

```json
{
  "provider": "moonshot",
  "model": "kimi-k2-turbo-preview",
  "promptId": "answer_assessment",
  "promptVersion": "1.0.0",
  "promptSha256": "hex-64",
  "providerRequestId": "req-…",
  "latencyMs": 3120,
  "inputTokens": 2210,
  "outputTokens": 410,
  "providerAttempts": 1,
  "repairAttempted": false,
  "validationStatus": "VALID",
  "workerVersion": "1.0.0+gitsha"
}
```

Schema:

| Field | Type | Required | Constraints |
|---|---|---|---|
| `provider` | string | yes | minLen 1 |
| `model` | string | yes | minLen 1 |
| `promptId` | string, nullable | yes |  |
| `promptVersion` | string, nullable | yes |  |
| `promptSha256` | string, nullable | yes | hex, 64 chars |
| `providerRequestId` | string, nullable | yes |  |
| `latencyMs` | integer | yes | min 0 |
| `inputTokens` | integer, nullable | yes | min 0 |
| `outputTokens` | integer, nullable | yes | min 0 |
| `providerAttempts` | integer | yes | min 0 |
| `repairAttempted` | boolean | yes |  |
| `validationStatus` | string | yes | minLen 1 |
| `workerVersion` | string | yes | minLen 1 |

For the speech service: `provider` is `faster-whisper`, `azure-speech` or `aws-polly`; `model` is the Whisper model name or the TTS voice ID; token fields are `null`. The media worker's values are in the Platform document. The AI worker's `workerVersion` follows `1.0.0+gitsha`.

> **P-16 — `RunMeta` nullability and `validationStatus` values**  
> **Status:** Pending confirmation  
> **Detail:** A.3 shows one RunMeta example. Implemented (ASSUMPTIONS #8): `promptId`, `promptVersion`, `promptSha256`, `providerRequestId`, `inputTokens` and `outputTokens` are nullable (speech and mock runs); `validationStatus` is a free string. The values emitted today are `VALID` and, for CV_PARSE, `REPAIRED`. No closed value set has been approved.  
> **Platform impact:** Platform should store `validationStatus` as an opaque string and not enum-validate it.


### H.2 Error codes (`ProcessingErrorCode`, A.3)

| Code | Class | Retryable | Raised when |
|---|---|---|---|
| `DEADLINE_EXCEEDED` | TRANSIENT | yes | Work could not finish before `deadlineAt` (or the request arrived after it). |
| `PROVIDER_TIMEOUT` | TRANSIENT | yes | A model or TTS call timed out after the worker's own retries. |
| `PROVIDER_RATE_LIMITED` | TRANSIENT | yes | HTTP 429 after the worker's own retries. |
| `PROVIDER_UNAVAILABLE` | TRANSIENT | yes | 5xx or connection failure after retries; circuit open; both TTS providers failed. |
| `STORAGE_UNAVAILABLE` | TRANSIENT | yes | An S3 read or write failed after 3 tries. |
| `INPUT_NOT_FOUND` | PERMANENT | no | A referenced object is missing. |
| `INPUT_CHECKSUM_MISMATCH` | PERMANENT | no | The object's sha256 differs from the payload. |
| `INPUT_UNSUPPORTED_FORMAT` | PERMANENT | no | Content sniffing rejects the file (also encrypted documents, see P-21). |
| `INPUT_TOO_LARGE` | PERMANENT | no | Over the size, page or duration limit. |
| `INPUT_MALWARE_DETECTED` | PERMANENT | no | ClamAV positive. |
| `INPUT_NO_TEXT` | PERMANENT | no | The CV has no extractable text even after OCR. |
| `OUTPUT_SCHEMA_INVALID` | PERMANENT | no | The model output is still invalid after one repair attempt (follow-up: no repair). |
| `OUTPUT_SEMANTIC_INVALID` | PERMANENT | no | Valid JSON that fails semantic checks (unknown competency, evidence not found, range). |
| `OUTPUT_POLICY_VIOLATION` | PERMANENT | no | A generated question or report breaks the question or language policy and no safe alternative was produced; TTS text containing personal data or placeholders. |
| `MEDIA_CHUNKS_MISSING`, `MEDIA_CORRUPT` | PERMANENT | no | Media worker only. |
| `UNSUPPORTED_SCHEMA_VERSION` | PERMANENT | no | The worker does not implement `outputSchemaVersion`. |
| `INTERNAL_ERROR` | TRANSIENT | yes | Unexpected exception (a bug), or a model-provider configuration error (HTTP 400, 401, 403, 404; alert). Retried; an alert fires on any occurrence. |

Implementation detail not stated in the spec: if the malware scanner cannot be reached, CV_PARSE fails closed with `INTERNAL_ERROR` (retryable) and the file is not parsed.

### H.3 Worker processing loop and dead-lettering (§3.2)

Per message, in this order:

1. Parse and validate against `envelope.v1.json` and the topic schema. An invalid message is **copied byte for byte to `<request topic>.dlq`** with header `ihr-dlq-reason`, the offset is committed, and **no result event is produced**. The reason text lists schema paths only (for example `schema: /question/competencyCodes`), never values.
2. An `eventId` already seen (in-memory LRU of 50,000) is skipped silently.
3. `deadlineAt` in the past → `.failed` `DEADLINE_EXCEEDED` (retryable).
4. Idempotency check (see H.4).
5. Wait for a free task slot; while all slots are busy the partition is paused (backpressure).
6. Run the handler with a timeout of `deadlineAt − now` → on timeout `.failed` `DEADLINE_EXCEEDED`.
7. Write the output object(s) and sidecar; produce the result (idempotent producer, `acks=all`); flush; then commit the offset. A crash between produce and commit can give a duplicate result event.
8. Any exception is mapped to an error code and a `.failed` is produced, then the offset is committed.

`outputSchemaVersion` is checked before the payload schema: an unknown version is a `.failed` `UNSUPPORTED_SCHEMA_VERSION`, not a dead-letter.

On `SIGTERM` the worker stops polling, waits up to 30 s for in-flight work, commits finished work and exits; unfinished messages are redelivered (`terminationGracePeriodSeconds: 45`).

Example of a dead-lettered message (a `cv.match` request with an unknown extra field), as produced by the real code:

```json
{
  "topic": "ihr.cv.match.requested.dlq",
  "key": "<the request's Kafka key, unchanged>",
  "headers": {
    "content-type": "application/json",
    "ihr-dlq-reason": "schema: /"
  },
  "value": "<the original message bytes, unchanged>"
}
```

> **P-18 — Detecting a request that never produces a result**  
> **Status:** Pending confirmation  
> **Detail:** The AI services emit exactly one `.completed` or `.failed` per processed request and no progress or heartbeat events. A request that is dead-lettered (schema invalid on receipt), or that is lost because a worker is down, produces no result event. The AI Specification does not define how core detects this case; S-17 only covers results with `retryable`.  
> **Platform impact:** Platform needs its own rule (for example based on `deadlineAt`) to move such a job out of RUNNING. That rule is a Platform decision and is not defined in either document.


### H.4 Idempotency and duplicate handling

- **Idempotency key:** `jobId`. The same `jobId` always maps to the same output object key.
- **Replay:** if the output object **and** its sidecar exist, the worker does not process again; it sends the stored `.completed` again (new `eventId`, the stored `run` and extras, the `attempt` of the new request).
- **After a `.failed`:** no output object exists, so a re-dispatch (same `jobId`, `attempt + 1`) is processed again.
- **Follow-up decisions** have an inline output and no stored object, so a repeated request is detected only by the in-memory `eventId` LRU of that process.
- **Platform side:** core must ignore a duplicate result by `jobId` (and by `eventId` in its inbox table, A.2). Duplicates are expected after a crash or a restart.

### H.5 Retries, timeouts, and what the Platform does when the AI fails

Retry layers (S-17: one owner per layer):

| Layer | Rule |
|---|---|
| Model provider calls (inside the worker, A-08) | retry on timeout, connection error, HTTP 429, 500, 502, 503, 504: up to 2 retries, backoff 1 s and 4 s (±20 % jitter), `Retry-After` honoured up to 20 s, never beyond `deadlineAt − 2 s`. Follow-up: at most 1 retry, only if at least 4 s remain. HTTP 400, 401, 403, 404: no retry, `INTERNAL_ERROR` and an alert. |
| Circuit breaker (A-08) | per model slot: opens after 5 consecutive failures, stays open 30 s, then one trial call; while open → immediate `PROVIDER_UNAVAILABLE` (retryable) |
| Invalid model output (A-07) | one repair call (follow-up: none); then `OUTPUT_SCHEMA_INVALID` or `OUTPUT_SEMANTIC_INVALID` |
| Storage | 3 tries, then `STORAGE_UNAVAILABLE` |
| TTS | Azure first; Polly after one failed attempt or an open circuit; each provider has a 6 s timeout and its own breaker (5 failures → open 60 s) |
| STT | CUDA out-of-memory: reload the model once and retry; second failure → `INTERNAL_ERROR` |
| **Core re-dispatch (S-17)** | a `.failed` with `retryable: true` is re-dispatched with `attempt + 1`, **up to 3 attempts in total**, backoff 30 s then 2 min; then the job is marked FAILED and HR_ADMIN / HR_MANAGER get a retry button. A `.failed` with `retryable: false` is not re-dispatched. |

Per-call timeouts and job deadlines (§4.3, A.4):

| Task | Per-call timeout | Job deadline set by core |
|---|---|---|
| CV_PARSE | 60 s | +10 min |
| CV_MATCH | 60 s | +10 min |
| INTERVIEW_PLAN | 60 s | +5 min (BASELINE), +90 s (REPLAN) |
| FOLLOWUP | 8 s | +12 s |
| ANSWER_ASSESSMENT | 45 s | +10 min |
| FINAL_REPORT | 90 s | +20 min |
| TRANSCRIPTION | — | +25 s (live), +10 min (re-run) |
| SYNTHESIS | 6 s per provider | +15 s (live), +10 min (pre-render) |

What the Platform does when an AI service is unavailable or processing fails (these Platform behaviours are already decided in S-09, S-17 and S-18):

| Job | Behaviour |
|---|---|
| CV_PARSE, CV_MATCH, ANSWER_ASSESSMENT, FINAL_REPORT | not on the interview's critical path; retried as above, then FAILED with a retry button |
| INTERVIEW_PLAN | the BASELINE plan is requested when the interview is created. If a REPLAN is not ready when the candidate presses Start, the baseline plan is used. If no plan exists at Start (for example a model outage), core builds a deterministic plan from the rubric's fallback question bank (S-18). See P-23. |
| TRANSCRIPTION | not done within 25 s → core moves on to the next planned question (its audio is pre-rendered) and the transcription continues in the background |
| FOLLOWUP | no decision 12 s after the transcription completed → next planned question. A `.failed` is treated the same way. |
| SYNTHESIS | follow-up audio not ready within 10 s → show the follow-up text with an "audio unavailable" note and continue. `PROVIDER_UNAVAILABLE` → core shows the text. Planned questions are pre-rendered, so they are normally available. |

A candidate never waits more than about 30 s (S-09).

> **P-17 — Shared policy lexicon file**  
> **Status:** Pending confirmation  
> **Detail:** A-21 says every question, including an HR edit of a plan question, must pass the question policy, and §9.1 says the lexicon is "exposed to core through a shared lexicon file (core repeats cheap checks)". The file `docs/prompts/policy_lexicon.yaml` and `policy/questions.py` do not exist yet; they are in Phase 3 of the AI build plan. The file format is not specified beyond "category → terms" plus a whitelist.  
> **Platform impact:** Platform cannot implement HR-edit validation against the lexicon until the file is delivered and its format is agreed.


### H.6 Processing states

The AI services have **no state model of their own** and emit no state events. They produce exactly one `.completed` or `.failed` per processed request. The state of a job is kept by core in `processing_jobs` using the shared enum `ProcessingJobStatus` (Appendix B): `QUEUED`, `RUNNING`, `SUCCEEDED`, `RETRYING`, `FAILED`, `DEAD_LETTERED`, `CANCELLED`. The job types are `ProcessingJobType`: the AI-related values are `CV_PARSE`, `CV_MATCH`, `INTERVIEW_PLAN`, `FOLLOWUP`, `ANSWER_ASSESSMENT`, `FINAL_REPORT`, `TRANSCRIPTION`, `SYNTHESIS`. How core maps events to these states is a Platform decision.

## I. Configuration and integration dependencies

### I.1 Kafka

| Item | Value |
|---|---|
| Topics and keys | section B (A.4) |
| Consumer groups | `ihr-core-api`, `ihr-ai-worker`, `ihr-ai-worker-realtime`, `ihr-speech-stt`, `ihr-speech-tts`, `ihr-media-worker` |
| AI consumer settings (§3.2) | one consumer per process, `enable.auto.commit=false`, `max.poll.interval.ms=900000`, `isolation.level=read_committed`; the offset is committed only after the result has been produced and flushed |
| AI producer settings | idempotent producer, `acks=all` |
| Authentication | SCRAM user per deployment (`KAFKA_USERNAME`, `KAFKA_PASSWORD`), provided by Platform |
| Topic properties | 6 partitions, retention 7 days, `cleanup.policy=delete`; replication factor 3 and `min.insync.replicas=2` in QA/PROD (1/1 in LOCAL/DEV); `<topic>.dlq` twin with retention 30 days |
| Access each AI deployment needs (follows from its behaviour) | read its request topics; write the matching `.completed`, `.failed` and `<request topic>.dlq` topics |

### I.2 S3

| Item | Value |
|---|---|
| Variables | `S3_INTERNAL_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_REGION`; buckets `S3_BUCKET_CANDIDATE`, `S3_BUCKET_RAW`, `S3_BUCKET_GENERATED` (defaults used in the fixtures: `ihr-candidate-private`, `ihr-interview-raw`, `ihr-generated-private`) |
| Credentials | one storage user per service (Platform §13.1) |
| Access needed | `ihr-ai-worker`: read `ihr-candidate-private`, read and write `ihr-generated-private`. `ihr-speech-stt`: read `ihr-interview-raw`, write `ihr-generated-private`. `ihr-speech-tts`: read and write `ihr-generated-private`. (A.6) |
| Compatibility | QA/UAT/PROD: Ceph RGW; LOCAL/DEV: SeaweedFS (S-03) |

### I.3 Environment variables (§3.3)

"Platform relevance" says whether the Platform team has to set or match the value.

| Variable | Service | Default / example | Platform relevance |
|---|---|---|---|
| `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_USERNAME`, `KAFKA_PASSWORD` | all | SCRAM user per deployment | provided by Platform |
| `WORKER_ROLE` | ai-worker, speech | `standard` / `realtime`; `stt` / `tts` | selects the subscriptions (A.1 table) |
| `S3_INTERNAL_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_REGION` | all | storage user per service | provided by Platform |
| `S3_BUCKET_CANDIDATE`, `S3_BUCKET_RAW`, `S3_BUCKET_GENERATED` | as needed | — | bucket names must match Platform's |
| `TTS_PROFILE_VERSION` | speech | `1` | **must equal the value core uses in `cacheKey`** (A.5, §3.3) |
| `STT_MAX_AUDIO_SECONDS` | speech | `400` | matches `maxDurationSeconds` sent by core |
| `AI_PROVIDER` | ai-worker | `kimi` or `mock` | `mock` for integration without a model |
| `STT_PROVIDER` | speech | `faster-whisper` or `mock` | `mock` for integration |
| `TTS_PROVIDER_PRIMARY`, `TTS_PROVIDER_SECONDARY` | speech | `azure` / `polly`, or `mock` | `mock` for integration (name: see P-24) |
| `MOCK_FAILURE_RATE` | ai-worker | `0`–`1` | injects transient failures to test retries |
| `AI_DEBUG_CAPTURE` | ai-worker | `true` | none; debug objects live under `ai-runs/` |
| `HEALTH_PORT` | all | `8080` | probes and metrics only |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | all | `http://otel-collector:4317` | traces continue from the `traceparent` header |
| `KIMI_*`, `AZURE_SPEECH_*`, `AWS_*`, `POLLY_VOICE`, `WHISPER_*`, `CLAMAV_*`, `OCR_MAX_PAGES`, `LIBREOFFICE_BIN`, `PROMPTS_ACTIVE_FILE` | AI-internal | see §3.3 | none; secrets are held by the AI team and are never part of any message |

> **P-24 — Name of the TTS mock switch**  
> **Status:** Pending confirmation  
> **Detail:** A.8 and §11.3 name the switch `TTS_PROVIDER=mock`. §3.3 lists `TTS_PROVIDER_PRIMARY` and `TTS_PROVIDER_SECONDARY` ("or mock"). The code and the AI-mocks compose file read `TTS_PROVIDER_PRIMARY` (`TTS_PROVIDER_SECONDARY` is set to `mock` in the compose file); a variable named `TTS_PROVIDER` is not read. Until real providers exist (Phase 4) the default is `mock`. `STT_PROVIDER=mock` is named the same way in all three places.  
> **Platform impact:** Platform environment files and compose overrides should set `TTS_PROVIDER_PRIMARY`. Setting `TTS_PROVIDER=mock` has no effect today. The leads need to settle which name the contract uses.


### I.4 Endpoints, authentication and headers

- **Service endpoints for integration: none.** There is no HTTP API between core and the workers (S-02). The only HTTP endpoints are `/health/live`, `/health/ready`, `/metrics`, `/version` for operations.
- **Service-to-service security:** Kafka SCRAM per deployment, and an S3 storage user per service. There is no other authentication because there is no other channel.
- **Required headers and identifiers (A.2):** Kafka headers `traceparent`, `content-type: application/json`, `ihr-schema: <topic>.v1`; `correlationId` copied unchanged through the whole chain and equal to the W3C trace-id when present.
- **No secrets** (API keys, passwords) appear in this document or in any message.

### I.5 Integration assumptions the Platform team must follow

1. Send only messages that validate against the schema files in `contracts/` (every object closed, `additionalProperties: false`); timestamps in `YYYY-MM-DDTHH:MM:SS.mmmZ` (P-14).
2. Upload and hash objects before dispatch, and send the real `sha256` and `sizeBytes` (A.4, item 1).
3. Treat `jobId` as the idempotency key and de-duplicate results by `jobId`; expect duplicates (H.4).
4. Keep follow-up budgeting, scores, bands, the TTS `cacheKey`, `deadlineAt` and `attempt` in core (A.4).
5. Never send candidate data in TTS text; never log `redactionHints`.
6. Use the Kafka key rules in B.1 so that all messages of one interview stay in one partition and in order.
7. Both team leads approve any change under `contracts/` in the same pull request (S-12, A.9).

### I.6 Mock environments for parallel development (A.8, S-16)

| Service | Setting | Behaviour (implemented) |
|---|---|---|
| ihr-ai-worker | `AI_PROVIDER=mock` | Returns the fixture for the task, whatever the request content is; still validates the request and the output against the schemas and writes real objects and real events. Simulated latency: 300 ms for follow-up, 1 s for the others. `MOCK_FAILURE_RATE` injects transient `PROVIDER_UNAVAILABLE` failures. |
| ihr-speech-service (STT) | `STT_PROVIDER=mock` | section D.3 |
| ihr-speech-service (TTS) | `TTS_PROVIDER_PRIMARY=mock` (see P-24) | section E.3 |
| ihr-core-api | `IHR_FAKE_WORKERS=true` (LOCAL only) | Platform-owned fake that answers every `*.requested` topic with fixture results; refuses to start when the Spring profile contains `prod` or `qa` (A.8) |

Compose file for the AI mocks: `deploy/compose/ai-mocks.compose.yml`. The fixtures are in `contracts/fixtures/`.

> **P-20 — Platform-provided infrastructure the AI services depend on**  
> **Status:** Pending confirmation  
> **Detail:** Needed from the Platform side and not yet delivered: Kafka SCRAM users with ACLs for the AI services, topic provisioning, and the S3 storage users per service (Platform §13.1). The bucket lifecycle rule that expires `ai-runs/` objects after 30 days (A-12) must also be configured; the specification does not say by whom. Review of `envelope.v1.json` is tracked in P-14. Until these exist, AI services run only in mock mode with fixtures.  
> **Platform impact:** Integration tests and the joint checkpoints (end of S2, S4, S5, S6; S-20) need these users and topics.


## J. Contract reconciliation

| Item to reconcile | Answer | Where |
|---|---|---|
| `interview_plan.v1`: `slot`, `cvAnchor` and other question fields | Final fields per question: `questionId`, `slot`, `text`, `competencyCodes`, `questionType`, `expectedAnswerRubric`, `timeBudgetSeconds`, `cvAnchor` (required, nullable), `source`. Top level: `questions`, `coverage`, `warnings`. `order` and `kind` are **not** in the schema (A.7 differs from §7.3). | C, P-01 to P-04 |
| `transcript.v1`: `schemaVersion`, `model`, `speechDetected`, `avgConfidence`, `qualityFlags`, `segments` | All eight top-level fields are required, as in §11.1. Times are in milliseconds relative to the start of the clip. | D |
| `tts_audio.v1`: event and ObjectRef structure | `output` = ObjectRef of the MP3; `durationMs`, `provider`, `voiceId`, `cacheHit` at the top level of the `.completed` payload. No JSON artefact. | E, P-05 |
| Mock TTS | bundled tone of 2,000 ms; reported `durationMs` = 350 ms × word count (§11.3 formula). A.8 and §11.3 are worded differently and not reconciled. | E.3, P-07 |
| `priorContext`, `assessmentStatus`, `proctoringSummary` | All three are Platform-populated inputs. Structures as implemented are in section F; approval of each is pending. | F, P-08, P-11, P-12 |
| Event envelope | Fields and ownership in B.1; `envelope.v1.json` is Platform-owned and awaits Platform review. | B.1, P-14 |

## K. Appendix: other artefact schemas that the Platform reads

These are stored objects that core reads. They are listed with the same status: the schema files are implemented; the real model behaviour is implemented only for CV_PARSE.

### K.1 `cv_profile.v1` (CV_PARSE, implemented)

| Path | Type | Required | Constraints |
|---|---|---|---|
| `headline` | string, nullable | yes | maxLen 200 |
| `currentTitle` | string, nullable | yes | maxLen 120 |
| `currentEmployer` | string, nullable | yes | maxLen 120 |
| `location` | string, nullable | yes | maxLen 80 |
| `employmentHistory` | array | yes | maxItems 30 |
| `employmentHistory[]` | object | yes |  |
| `employmentHistory[].employer` | string, nullable | yes | maxLen 150 |
| `employmentHistory[].title` | string | yes | maxLen 150 |
| `employmentHistory[].startDate` | string, nullable | yes | pattern `^\d{4}-(0[1-9]\|1[0-2])$` |
| `employmentHistory[].endDate` | , nullable | yes |  |
| `employmentHistory[].datePrecision` | enum | yes | values: `MONTH`, `YEAR`, `UNKNOWN` |
| `employmentHistory[].employmentType` | enum | yes | values: `FULL_TIME`, `PART_TIME`, `CONTRACT`, `INTERNSHIP`, `FREELANCE`, `UNKNOWN` |
| `employmentHistory[].highlights` | array of string | yes | maxItems 5; item maxLen 200 |
| `employmentHistory[].skillsMentioned` | array of string | yes | maxItems 20; item maxLen 60 |
| `education` | array | yes | maxItems 10 |
| `education[]` | object | yes |  |
| `education[].institution` | string | yes | maxLen 150 |
| `education[].qualification` | string | yes | maxLen 120 |
| `education[].fieldOfStudy` | string, nullable | yes | maxLen 120 |
| `education[].startYear` | integer, nullable | yes |  |
| `education[].endYear` | integer, nullable | yes |  |
| `skills` | array | yes | maxItems 80 |
| `skills[]` | object | yes |  |
| `skills[].name` | string | yes | maxLen 60 |
| `skills[].quote` | string | yes | minLen 8; maxLen 300 |
| `skills[].sourceSection` | enum | yes | values: `SKILLS_SECTION`, `EXPERIENCE`, `PROJECT`, `CERTIFICATION`, `EDUCATION`, `SUMMARY` |
| `certifications` | array | yes | maxItems 20 |
| `certifications[]` | object | yes |  |
| `certifications[].name` | string | yes | maxLen 150 |
| `certifications[].issuer` | string, nullable | yes | maxLen 100 |
| `certifications[].year` | integer, nullable | yes |  |
| `projects` | array | yes | maxItems 15 |
| `projects[]` | object | yes |  |
| `projects[].name` | string | yes | maxLen 150 |
| `projects[].summary` | string | yes | maxLen 300 |
| `projects[].skills` | array of string | yes | maxItems 15; item maxLen 60 |
| `missingOrAmbiguousFields` | array of enum | yes | item values: `EMPLOYMENT_DATES`, `CURRENT_ROLE`, `SKILLS`, `EDUCATION`, `EMPLOYER_NAMES`, `JOB_TITLES` |
| `extractionWarnings` | array of enum | yes | item values: `PROMPT_INJECTION_SUSPECTED`, `TEXT_GARBLED`, `MULTIPLE_PEOPLE_DESCRIBED`, `NOT_A_CV` |
| `identity` | object | yes |  |
| `identity.name` | object, nullable | yes |  |
| `identity.name.value` | string, nullable | yes |  |
| `identity.name.confidence` | number | yes | min 0; max 1 |
| `identity.name.source` | enum | yes | values: `LOCAL_REGEX`, `LOCAL_HEADER` |
| `identity.email` | object, nullable | yes |  |
| `identity.email.value` | string, nullable | yes |  |
| `identity.email.confidence` | number | yes | min 0; max 1 |
| `identity.email.source` | enum | yes | values: `LOCAL_REGEX`, `LOCAL_HEADER` |
| `identity.phone` | object, nullable | yes |  |
| `identity.phone.value` | string, nullable | yes |  |
| `identity.phone.confidence` | number | yes | min 0; max 1 |
| `identity.phone.source` | enum | yes | values: `LOCAL_REGEX`, `LOCAL_HEADER` |
| `derived` | object | yes |  |
| `derived.totalExperienceMonths` | integer, nullable | yes | min 0 |
| `derived.skillsCanonical` | array | yes |  |
| `derived.skillsCanonical[]` | object | yes |  |
| `derived.skillsCanonical[].name` | string | yes | minLen 1 |
| `derived.skillsCanonical[].originalNames` | array of string | yes | minItems 1; item minLen 1 |
| `derived.mostRecentRoleMonths` | integer, nullable | yes | min 0 |
| `extraction` | object | yes |  |
| `extraction.pageCount` | integer | yes | min 0 |
| `extraction.ocrUsed` | boolean | yes |  |
| `extraction.ocrPages` | integer | yes | min 0 |
| `extraction.textChars` | integer | yes | min 0 |
| `extraction.truncated` | boolean | yes |  |
| `extraction.redactionCounts` | object | yes |  |
| `extraction.redactionCounts.EMAIL` | integer | yes | min 0 |
| `extraction.redactionCounts.PHONE` | integer | yes | min 0 |
| `extraction.redactionCounts.URL` | integer | yes | min 0 |
| `extraction.redactionCounts.GOV_ID` | integer | yes | min 0 |
| `extraction.redactionCounts.CANDIDATE` | integer | yes | min 0 |
| `extraction.redactionCounts.DOB` | integer | yes | min 0 |
| `extraction.redactionCounts.LINES_REMOVED` | integer | yes | min 0 |
| `extraction.warnings` | array of enum | yes | item values: `OCR_USED`, `OCR_PARTIAL`, `LOW_OCR_CONFIDENCE`, `TEXT_TRUNCATED`, `EXTENSION_MISMATCH`, `MULTI_COLUMN_LAYOUT`, `PROMPT_INJECTION_SUSPECTED`, `NON_ENGLISH_CONTENT`, `DATES_AMBIGUOUS`, `NO_EMPLOYMENT_HISTORY`, `NO_SKILLS_FOUND`, `ENCRYPTED_DOCUMENT`, `TEXT_GARBLED`, `MULTIPLE_PEOPLE_DESCRIBED`, `NOT_A_CV` |

Notes: `identity`, `derived` and `extraction` are added by code; the model produces the rest. `derived.totalExperienceMonths` is computed in code by merging employment intervals at month precision, with "PRESENT" taken as the month of the parse, overlapping jobs counted once, and entries with unknown dates excluded and flagged in `missingOrAmbiguousFields` (A-18). `skills[]` holds one entry per distinct skill name (the first quote is kept; ASSUMPTIONS #22). `derived.skillsCanonical` groups spelling variants through `skill_aliases.json` (A-19). Example from the mock run:

```json
{
  "headline": "Backend engineer with Java and Spring Boot experience in payments systems.",
  "currentTitle": "Senior Software Engineer",
  "currentEmployer": "Acme Payments",
  "location": null,
  "employmentHistory": [
    {
      "employer": "Acme Payments",
      "title": "Senior Software Engineer",
      "startDate": "2022-04",
      "endDate": "PRESENT",
      "datePrecision": "MONTH",
      "employmentType": "FULL_TIME",
      "highlights": [
        "Built Spring Boot services for card settlement",
        "Designed Kafka event pipelines for settlement batches"
      ],
      "skillsMentioned": [
        "Java",
        "Spring Boot",
        "Kafka"
      ]
    },
    {
      "employer": "Globex Fintech",
      "title": "Software Engineer",
      "startDate": "2018-07",
      "endDate": "2022-03",
      "datePrecision": "MONTH",
      "employmentType": "FULL_TIME",
      "highlights": [
        "Developed Java REST APIs for merchant onboarding",
        "Tuned PostgreSQL queries and indexes for reporting"
      ],
      "skillsMentioned": [
        "Java",
        "PostgreSQL"
      ]
    }
  ],
  "education": [
    {
      "institution": "XYZ Institute of Technology",
      "qualification": "B.E.",
      "fieldOfStudy": "Computer Engineering",
      "startYear": 2014,
      "endYear": 2018
    }
  ],
  "skills": [
    {
      "name": "Java",
      "quote": "Developed REST APIs in Java 11",
      "sourceSection": "EXPERIENCE"
    },
    {
      "name": "Spring Boot",
      "quote": "Built Spring Boot services for card settlement",
      "sourceSection": "EXPERIENCE"
    },
    {
      "name": "Kafka",
      "quote": "event pipelines on Apache Kafka",
      "sourceSection": "EXPERIENCE"
    },
    {
      "name": "PostgreSQL",
      "quote": "Wrote PostgreSQL queries and tuned indexes",
      "sourceSection": "EXPERIENCE"
    },
    {
      "name": "Docker",
      "quote": "Java | Spring Boot | Kafka | PostgreSQL | Docker",
      "sourceSection": "SKILLS_SECTION"
    }
  ],
  "certifications": [],
  "projects": [],
  "missingOrAmbiguousFields": [],
  "extractionWarnings": [],
  "identity": {
    "name": {
      "value": "Asha Verma",
      "confidence": 0.6,
      "source": "LOCAL_HEADER"
    },
    "email": {
      "value": "asha.verma@example.com",
      "confidence": 1.0,
      "source": "LOCAL_REGEX"
    },
    "phone": {
      "value": "+919876543210",
      "confidence": 1.0,
      "source": "LOCAL_REGEX"
    }
  },
  "derived": {
    "totalExperienceMonths": 102,
    "skillsCanonical": [
      {
        "name": "Java",
        "originalNames": [
          "Java"
        ]
      },
      {
        "name": "Spring Boot",
        "originalNames": [
          "Spring Boot"
        ]
      },
      {
        "name": "Kafka",
        "originalNames": [
          "Kafka"
        ]
      },
      {
        "name": "PostgreSQL",
        "originalNames": [
          "PostgreSQL"
        ]
      },
      {
        "name": "Docker",
        "originalNames": [
          "Docker"
        ]
      }
    ],
    "mostRecentRoleMonths": 57
  },
  "extraction": {
    "pageCount": 1,
    "ocrUsed": false,
    "ocrPages": 0,
    "textChars": 645,
    "truncated": false,
    "redactionCounts": {
      "EMAIL": 1,
      "PHONE": 1,
      "URL": 1,
      "GOV_ID": 0,
      "CANDIDATE": 1,
      "DOB": 0,
      "LINES_REMOVED": 2
    },
    "warnings": []
  }
}
```

### K.2 `cv_match.v1` (CV_MATCH, pending Phase 3)

| Path | Type | Required | Constraints |
|---|---|---|---|
| `criteria` | array | yes | minItems 1 |
| `criteria[]` | object | yes |  |
| `criteria[].requirementCode` | string | yes | minLen 1 |
| `criteria[].status` | enum | yes | values: `MATCHED`, `PARTIAL`, `MISSING`, `UNKNOWN` |
| `criteria[].evidence` | array | yes | minItems 0; maxItems 3 |
| `criteria[].evidence[]` | object | yes |  |
| `criteria[].evidence[].quote` | string | yes | minLen 8; maxLen 300 |
| `criteria[].rationale` | string | yes | maxLen 400 |
| `criteria[].confidence` | enum | yes | values: `LOW`, `MEDIUM`, `HIGH` |
| `criteria[].statusSource` | enum | yes | values: `MODEL`, `DETERMINISTIC_EXPERIENCE` |
| `summary` | string | yes | maxLen 800 |
| `strengths` | array of string | yes | maxItems 5; item maxLen 200 |
| `gaps` | array of string | yes | maxItems 5; item maxLen 200 |
| `clarificationQuestions` | array of string | yes | maxItems 5; item maxLen 300 |
| `warnings` | array of enum | yes | item values: `CV_SPARSE`, `CV_TEXT_TRUNCATED`, `PROMPT_INJECTION_SUSPECTED`, `NON_ENGLISH_CONTENT` |

```json
{
  "criteria": [
    {
      "requirementCode": "R1",
      "status": "MATCHED",
      "evidence": [
        {
          "quote": "Developed REST APIs in Java 11"
        },
        {
          "quote": "Built Spring Boot services for card settlement"
        }
      ],
      "rationale": "Two roles describe Java backend services and REST APIs.",
      "confidence": "HIGH",
      "statusSource": "MODEL"
    },
    {
      "requirementCode": "R2",
      "status": "MATCHED",
      "evidence": [
        {
          "quote": "event pipelines on Apache Kafka"
        }
      ],
      "rationale": "The current role describes designing Kafka event pipelines.",
      "confidence": "HIGH",
      "statusSource": "MODEL"
    },
    {
      "requirementCode": "R3",
      "status": "MATCHED",
      "evidence": [
        {
          "quote": "Software Engineer | Jul 2018 - Mar 2022"
        }
      ],
      "rationale": "Calculated from stated employment dates: 102 months against 36 required.",
      "confidence": "MEDIUM",
      "statusSource": "DETERMINISTIC_EXPERIENCE"
    },
    {
      "requirementCode": "R4",
      "status": "MISSING",
      "evidence": [],
      "rationale": "The CV lists skills and roles in detail and does not mention AWS.",
      "confidence": "MEDIUM",
      "statusSource": "MODEL"
    }
  ],
  "summary": "The CV shows Java backend and Kafka event-streaming work in payments over two roles. No AWS experience is stated.",
  "strengths": [
    "Java backend services in two roles",
    "Kafka event pipelines in the current role"
  ],
  "gaps": [
    "No stated AWS experience"
  ],
  "clarificationQuestions": [
    "Which cloud platforms have you deployed services to?"
  ],
  "warnings": []
}
```

### K.3 `followup_decision.v1` (FOLLOWUP, pending Phase 5)

Inline in `ihr.interview.followup.completed` (`output`); table in B.6.

### K.4 `answer_assessment.v1` (ANSWER_ASSESSMENT, pending Phase 5)

| Path | Type | Required | Constraints |
|---|---|---|---|
| `scores` | array | yes |  |
| `scores[]` | object | yes |  |
| `scores[].competencyCode` | string | yes | minLen 1 |
| `scores[].score` | integer, nullable | yes | min 1; max 5 |
| `scores[].notAssessedReason` | enum, nullable | yes | values: `NO_RELEVANT_CONTENT`, `ANSWER_TOO_SHORT`, `NO_SPEECH`, `OFF_TOPIC` |
| `scores[].confidence` | enum | yes | values: `LOW`, `MEDIUM`, `HIGH` |
| `scores[].rationale` | string | yes | minLen 20; maxLen 600 |
| `scores[].evidence` | array | yes | minItems 0; maxItems 3 |
| `scores[].evidence[]` | object | yes |  |
| `scores[].evidence[].segmentIndex` | integer | yes | min 0 |
| `scores[].evidence[].quote` | string | yes | minLen 8; maxLen 300 |
| `notableEvidence` | array | yes | maxItems 3 |
| `notableEvidence[]` | object | yes |  |
| `notableEvidence[].segmentIndex` | integer | yes | min 0 |
| `notableEvidence[].quote` | string | yes | minLen 8; maxLen 300 |
| `notableEvidence[].relatedCompetencyCode` | string, nullable | yes | minLen 1 |
| `candidateReportedClaims` | array | yes | maxItems 5 |
| `candidateReportedClaims[]` | object | yes |  |
| `candidateReportedClaims[].claim` | string | yes | maxLen 200 |
| `candidateReportedClaims[].segmentIndex` | integer | yes | min 0 |
| `answerFlags` | array of enum | yes | item values: `ANSWER_TOO_SHORT`, `OFF_TOPIC`, `NO_SUBSTANTIVE_ANSWER`, `LIKELY_TRANSCRIPTION_ISSUE`, `CANDIDATE_REPORTED_TECHNICAL_ISSUE`, `CANDIDATE_DECLINED_TO_ANSWER`, `PROMPT_INJECTION_SUSPECTED` |
| `uncertainty` | array of string | yes | maxItems 3; item maxLen 200 |

```json
{
  "scores": [
    {
      "competencyCode": "PRACTICAL_EXPERIENCE",
      "score": 4,
      "notAssessedReason": null,
      "confidence": "HIGH",
      "rationale": "Specific example with own design decisions (keying by merchant, idempotency check) and a stated result; lessons learned are not covered.",
      "evidence": [
        {
          "segmentIndex": 1,
          "quote": "I designed the topic layout so each merchant's events used the merchant ID as the key"
        },
        {
          "segmentIndex": 3,
          "quote": "The batch went from about forty minutes to under ten"
        }
      ]
    },
    {
      "competencyCode": "TECHNICAL_SKILL",
      "score": 4,
      "notAssessedReason": null,
      "confidence": "MEDIUM",
      "rationale": "Correctly links message keys to per-partition ordering and explains idempotent consumption; alternatives are not discussed.",
      "evidence": [
        {
          "segmentIndex": 1,
          "quote": "which kept them in order within a partition"
        },
        {
          "segmentIndex": 2,
          "quote": "I added an idempotency check so a replayed message did not settle twice"
        }
      ]
    }
  ],
  "notableEvidence": [
    {
      "segmentIndex": 2,
      "quote": "We had retries on the consumer side",
      "relatedCompetencyCode": "TECHNICAL_SKILL"
    }
  ],
  "candidateReportedClaims": [
    {
      "claim": "Reduced settlement batch time from about forty minutes to under ten",
      "segmentIndex": 3
    }
  ],
  "answerFlags": [],
  "uncertainty": []
}
```

### K.5 `final_report.v1` (FINAL_REPORT, pending Phase 6)

| Path | Type | Required | Constraints |
|---|---|---|---|
| `summary` | string | yes | minLen 100; maxLen 1200 |
| `competencyNarratives` | array | yes | minItems 1 |
| `competencyNarratives[]` | object | yes |  |
| `competencyNarratives[].competencyCode` | string | yes | minLen 1 |
| `competencyNarratives[].narrative` | string | yes | minLen 20; maxLen 600 |
| `competencyNarratives[].evidenceRefs` | array | yes | minItems 0; maxItems 4 |
| `competencyNarratives[].evidenceRefs[]` | object | yes |  |
| `competencyNarratives[].evidenceRefs[].turn` | string | yes | pattern `^T[1-9]\d*$` |
| `competencyNarratives[].evidenceRefs[].segmentIndex` | integer | yes | min 0 |
| `strengths` | array | yes | minItems 0; maxItems 5 |
| `strengths[]` | object | yes |  |
| `strengths[].text` | string | yes | maxLen 250 |
| `strengths[].evidenceRefs` | array | yes | minItems 0; maxItems 3 |
| `strengths[].evidenceRefs[]` | object | yes |  |
| `strengths[].evidenceRefs[].turn` | string | yes | pattern `^T[1-9]\d*$` |
| `strengths[].evidenceRefs[].segmentIndex` | integer | yes | min 0 |
| `gaps` | array | yes | minItems 0; maxItems 5 |
| `gaps[]` | object | yes |  |
| `gaps[].text` | string | yes | maxLen 250 |
| `gaps[].evidenceRefs` | array | yes | minItems 0; maxItems 3 |
| `gaps[].evidenceRefs[]` | object | yes |  |
| `gaps[].evidenceRefs[].turn` | string | yes | pattern `^T[1-9]\d*$` |
| `gaps[].evidenceRefs[].segmentIndex` | integer | yes | min 0 |
| `reviewerFocusAreas` | array of string | yes | minItems 1; maxItems 5; item maxLen 250 |
| `limitations` | array of string | yes | minItems 0; maxItems 6; item maxLen 250 |
| `computedScores` | object | yes |  |
| `computedScores.competencies` | array | yes |  |
| `computedScores.competencies[]` | object | yes |  |
| `computedScores.competencies[].code` | string | yes | minLen 1 |
| `computedScores.competencies[].score` | number, nullable | yes | min 1.0; max 5.0 |
| `computedScores.competencies[].assessedTurnCount` | integer | yes | min 0 |
| `computedScores.overallScore` | number, nullable | yes | min 0; max 100 |
| `computedScores.completionState` | enum | yes | values: `COMPLETE`, `INCOMPLETE` |
| `computedScores.evidenceLevel` | enum | yes | values: `STRONG_EVIDENCE`, `MODERATE_EVIDENCE`, `LIMITED_EVIDENCE`, `INSUFFICIENT_EVIDENCE` |

```json
{
  "summary": "The interview evidence shows practical experience designing Kafka-based settlement pipelines with clear ownership and a stated outcome, and correct explanations of partition ordering and idempotent consumption. Problem-solving answers followed logical steps. Role fit was not assessed.",
  "competencyNarratives": [
    {
      "competencyCode": "TECHNICAL_SKILL",
      "narrative": "Explained that message keys keep related events ordered within a partition and described an idempotency check for replays (T1 [1], T1 [2]).",
      "evidenceRefs": [
        {
          "turn": "T1",
          "segmentIndex": 1
        },
        {
          "turn": "T1",
          "segmentIndex": 2
        }
      ]
    },
    {
      "competencyCode": "PROBLEM_SOLVING",
      "narrative": "Answers described logical investigation steps; option comparison was limited.",
      "evidenceRefs": []
    },
    {
      "competencyCode": "PRACTICAL_EXPERIENCE",
      "narrative": "Gave a specific example with own design decisions and a stated reduction in batch time (T1 [3]).",
      "evidenceRefs": [
        {
          "turn": "T1",
          "segmentIndex": 3
        }
      ]
    },
    {
      "competencyCode": "COMMUNICATION",
      "narrative": "Answers were relevant to the questions and followed a clear structure.",
      "evidenceRefs": []
    },
    {
      "competencyCode": "ROLE_FIT",
      "narrative": "No answer in this interview provided evidence for this competency.",
      "evidenceRefs": []
    }
  ],
  "strengths": [
    {
      "text": "Specific example of designing Kafka event pipelines with own decisions",
      "evidenceRefs": [
        {
          "turn": "T1",
          "segmentIndex": 1
        }
      ]
    }
  ],
  "gaps": [
    {
      "text": "Limited evidence of comparing alternative designs",
      "evidenceRefs": []
    }
  ],
  "reviewerFocusAreas": [
    "Explore how alternative designs were evaluated for the settlement pipeline"
  ],
  "limitations": [
    "Role fit was not assessed in this interview."
  ],
  "computedScores": {
    "competencies": [
      {
        "code": "TECHNICAL_SKILL",
        "score": 3.7,
        "assessedTurnCount": 3
      },
      {
        "code": "PROBLEM_SOLVING",
        "score": 3.5,
        "assessedTurnCount": 2
      },
      {
        "code": "PRACTICAL_EXPERIENCE",
        "score": 4.0,
        "assessedTurnCount": 1
      },
      {
        "code": "COMMUNICATION",
        "score": 3.8,
        "assessedTurnCount": 4
      },
      {
        "code": "ROLE_FIT",
        "score": null,
        "assessedTurnCount": 0
      }
    ],
    "overallScore": 68.3,
    "completionState": "COMPLETE",
    "evidenceLevel": "MODERATE_EVIDENCE"
  }
}
```

## L. Source files

| What | Path |
|---|---|
| Envelope schema | `contracts/events/envelope.v1.json` |
| Topic payload schemas (24) | `contracts/events/ihr.<topic>.v1.json` |
| Error code table | `contracts/events/processing_error_codes.v1.json` |
| Artefact schemas | `contracts/ai-schemas/*.v1.json` (the `$defs.ModelOutput` entry of a schema is the part the model returns) |
| Fixtures (requests, expected outputs, negative cases) | `contracts/fixtures/ai/<task>/`, `contracts/fixtures/speech/` |
| Assumptions made where the specification is silent | `contracts/ASSUMPTIONS.md` (22 items) |
| Regenerating the schemas and fixtures | `tools/gen_contracts.py`, `tools/gen_fixtures.py` (CI fails on any difference) |
