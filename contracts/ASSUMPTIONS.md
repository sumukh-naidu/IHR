# Contract assumptions (pending confirmation by both leads, S-12)

The schemas follow the AI Specification v1.1. Where the spec is silent or
contradicts itself, the choices below were made. Each one is open for an ADR (§1.4).

| # | Topic | Choice |
|---|-------|--------|
| 1 | `tts_audio.v1` (not defined in §7 / A.7) | Built only from the A.5 inline extras: `durationMs`, `provider`, `voiceId`, `cacheHit`. |
| 2 | `transcript.v1` (example only, §11.1) | Every field in the example is required. `qualityFlags` uses the Appendix B `TranscriptQualityFlag` enum. |
| 3 | `interview_plan.v1` (A.7 vs §7.3) | Follows §7.3: `slot` (not `order`), no `kind`, includes `cvAnchor`. |
| 4 | TTS mock duration (A.8 vs §11.3) | Follows §11.3: durationMs = 350 ms × word count. |
| 5 | `envelope.v1.json` (Platform-owned, A.1) | Drafted from A.2 by the AI team for Platform review. |
| 6 | `.completed` inline extras | Placed at the top level of the payload, next to `output`. |
| 7 | `cv.parse.completed` output | `output` = `{cvProfile: ObjectRef, cvText: ObjectRef}`. |
| 8 | `RunMeta` | `promptId`, `promptVersion`, `promptSha256`, `providerRequestId` and the token fields are nullable (speech and mock runs). `validationStatus` is a free string; the example shows `"VALID"`. |
| 9 | `identity` members | Each of name/email/phone may be null when nothing was found locally. |
| 10 | `derived.skillsCanonical[]` | `{name, originalNames[]}` ("canonical names with original names", §7.1). |
| 11 | `priorContext[]` item | `{questionText, transcriptExcerpt ≤ 1200}` (A.5 describes the content, not the field names). |
| 12 | `final-report.requested.turns[].assessmentStatus` | Uses `ProcessingJobStatus` (Appendix B). |
| 13 | `proctoringSummary` | Map of `ProctoringEventType` → integer count. |
| 14 | `cv.match.requested.job.experienceMin/MaxMonths` | Nullable (a job may have no range). |
| 15 | `final_report.v1` evidence refs | The stored artefact keeps the turn label (`T#`) and segment index; mapping to turnId uses the request's `turns[]`. |
| 16 | Media topics (`ihr.media.*`) | Not generated here: the Platform team (producer/consumer) owns those schemas. |
| 17 | Timestamps | `occurredAt` / `deadlineAt` must use the format `YYYY-MM-DDTHH:MM:SS.mmmZ` (A.2). |
| 18 | `promptSha256` (A-11) | Hash of the assembled system template (base_rules + task text + schema + example) before per-request variables are filled. The example output is the task's `kimi_response.json` fixture. |
| 19 | `AI_PROVIDER=local` | Development/integration-test stand-in only: the Kimi adapter pointed at a local OpenAI-compatible `llama-server` running Qwen3.6-35B-A3B (GGUF) for both model slots. Not a production provider; no §13.3 eval gate result counts unless produced on Kimi. Removed from use once the Kimi key is available. |
| 20 | CV_PARSE details the spec leaves open | (a) Aadhaar-shaped 12-digit numbers are left by step 4 (phone) so step 5 labels them `[GOV_ID]`. (b) DOCX page count comes from `docProps/app.xml` `<Pages>`, else ⌈chars / 3000⌉. (c) The NON_ENGLISH_CONTENT detector counts words not in Latin script. (d) Hidden text means white fill or font size ≤ 1.5 pt; any hidden word of ≥ 3 letters raises PROMPT_INJECTION_SUSPECTED. (e) The stored `cv/{documentId}/text/{jobId}.txt` is exactly the text sent to Kimi (redacted, capped, tags escaped), so later quote checks run on the same text. (f) Name confidence is 0.6 for both header rules (known and unknown name). (g) An inconsistent currentTitle/currentEmployer is set to null together. (h) `validationStatus` is `VALID` or `REPAIRED`. (i) Encrypted documents fail with INPUT_UNSUPPORTED_FORMAT; ENCRYPTED_DOCUMENT is named in the error message because `.failed` carries no warnings. |
| 21 | `skill_aliases.json` | Seeded with ~65 entries in Sprint 2 for CV_PARSE canonicalisation; extended to the ~300 entries of A-19 with CV_MATCH (Sprint 3). |
| 22 | Duplicate skills and name matching | Code keeps one `skills[]` entry per distinct name (case/space-insensitive), the first quote wins; spelling variants are grouped only in `derived.skillsCanonical`. Matching the known candidate name (A-17) ignores case, punctuation and spacing. |
| 23 | Policy lexicon and language regex | (a) The §9.2 hiring regex also matches inflected forms (hired, rejected, shortlisted, selected, advanced, progressed); otherwise A-22's own example "should be hired" passes. (b) `policy_lexicon.yaml` whitelist adds common CV vocabulary that contains lexicon words (third-party, developer community, medical devices, leader election, model weights …); lexicon terms do not match after a hyphen ("third-party"). (c) CV_MATCH carries `PROMPT_INJECTION_SUSPECTED`, `NON_ENGLISH_CONTENT` and `TEXT_TRUNCATED` (as `CV_TEXT_TRUNCATED`) from the profile; `CV_SPARSE` comes from the model only. (d) The profile sent to CV_MATCH omits `identity` and `extraction`. (e) A-20 skill-specific months use dated roles whose `skillsMentioned` contain the requirement's `skillName` (canonicalised); else total experience. MISSING/UNKNOWN after the override have no evidence. |
| 24 | STT details the spec leaves open | (a) `durationMs` is the transcribed duration (capped at min(maxDurationSeconds, STT_MAX_AUDIO_SECONDS)), as in mock mode. (b) CLIPPED_AUDIO is measured on the original decoded samples, before resampling. (c) Segments that are empty after trimming are dropped. (d) LOW_CONFIDENCE is not added when NO_SPEECH is set. (e) The bundled warm-up clip is a synthetic 3 s tone (`speech/assets/warmup_3s.wav`); it exercises model load and decode. (f) The GPU image expects the artefact-store model at build-context `models/whisper/large-v3-turbo` with an optional `SHA256SUMS`. |
| 25 | CV_MATCH evidence | Per §7.2 ("≥ 1 verified quote"): unverified quotes are dropped; a MATCHED/PARTIAL criterion fails validation only when none of its quotes verify. Prompt `cv_match` is v1.0.1 (experience durations must not be stated). |
