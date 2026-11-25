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
