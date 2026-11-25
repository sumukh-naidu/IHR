"""Generate contracts/events/*.v1.json and contracts/ai-schemas/*.v1.json.

Source of truth: IHR V1 AI Specification v1.1 (Appendix A, Appendix B, chapter 7, §11.1).
Every object has "additionalProperties": false (A.1). Run:  python tools/gen_contracts.py
Assumptions where the spec is silent are listed in ASSUMPTIONS.md (contracts/) and
/Users/impacto/IHR-handover.md.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVENTS = ROOT / "contracts" / "events"
AI = ROOT / "contracts" / "ai-schemas"
DRAFT = "https://json-schema.org/draft/2020-12/schema"
BASE_ID = "https://ihr.internal/contracts"

# ---------------------------------------------------------------- helpers


def obj(props: dict, required: list[str] | None = None, **extra) -> dict:
    """Closed object; all properties required unless `required` is given."""
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": props,
        "required": list(props) if required is None else required,
        **extra,
    }


def s(max_len: int | None = None, min_len: int | None = None, **extra) -> dict:
    d: dict = {"type": "string", **extra}
    if max_len is not None:
        d["maxLength"] = max_len
    if min_len is not None:
        d["minLength"] = min_len
    return d


def nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


def arr(items: dict, min_items: int | None = None, max_items: int | None = None) -> dict:
    d: dict = {"type": "array", "items": items}
    if min_items is not None:
        d["minItems"] = min_items
    if max_items is not None:
        d["maxItems"] = max_items
    return d


def enum(*values: str) -> dict:
    return {"type": "string", "enum": list(values)}


def integer(minimum: int | None = None, maximum: int | None = None) -> dict:
    d: dict = {"type": "integer"}
    if minimum is not None:
        d["minimum"] = minimum
    if maximum is not None:
        d["maximum"] = maximum
    return d


def number(minimum: float | None = None, maximum: float | None = None) -> dict:
    d: dict = {"type": "number"}
    if minimum is not None:
        d["minimum"] = minimum
    if maximum is not None:
        d["maximum"] = maximum
    return d


UUID = s(format="uuid", pattern="^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
TIMESTAMP = s(format="date-time", pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
HEX64 = s(pattern="^[0-9a-f]{64}$")
QUOTE = s(300, 8)

# ---------------------------------------------------------------- Appendix B enums

TURN_KIND = enum("WARMUP", "MAIN", "FOLLOW_UP", "CLOSING")
QUESTION_TYPE = enum("TECHNICAL_CONCEPT", "SCENARIO", "PAST_EXPERIENCE", "PROBLEM_SOLVING", "ROLE_MOTIVATION")
CONFIDENCE = enum("LOW", "MEDIUM", "HIGH")
FIT_STATUS = enum("MATCHED", "PARTIAL", "MISSING", "UNKNOWN")
FOLLOWUP_DECISION = enum("ASK_FOLLOW_UP", "NEXT_QUESTION")
FOLLOWUP_REASON = enum("CLARIFY_AMBIGUITY", "PROBE_DEPTH", "ANSWER_INCOMPLETE", "ANSWER_SUFFICIENT", "NOT_USEFUL")
COMPLETION_STATE = enum("COMPLETE", "INCOMPLETE")
EVIDENCE_LEVEL = enum("STRONG_EVIDENCE", "MODERATE_EVIDENCE", "LIMITED_EVIDENCE", "INSUFFICIENT_EVIDENCE")
ANSWER_FLAG = enum(
    "ANSWER_TOO_SHORT", "OFF_TOPIC", "NO_SUBSTANTIVE_ANSWER", "LIKELY_TRANSCRIPTION_ISSUE",
    "CANDIDATE_REPORTED_TECHNICAL_ISSUE", "CANDIDATE_DECLINED_TO_ANSWER", "PROMPT_INJECTION_SUSPECTED",
)
TRANSCRIPT_QUALITY_FLAG = enum("NO_SPEECH", "LOW_CONFIDENCE", "POSSIBLE_HALLUCINATION", "CLIPPED_AUDIO", "TRUNCATED_AT_LIMIT")
TRANSCRIPT_STATUS = enum("NOT_REQUESTED", "QUEUED", "SUCCEEDED", "NO_SPEECH", "FAILED", "RETRYING")
PROCESSING_JOB_STATUS = enum("QUEUED", "RUNNING", "SUCCEEDED", "RETRYING", "FAILED", "DEAD_LETTERED", "CANCELLED")
REQUIREMENT_CATEGORY = enum("SKILL", "EXPERIENCE", "EDUCATION", "CERTIFICATION", "DOMAIN", "OTHER")
REQUIREMENT_TYPE = enum("MUST_HAVE", "PREFERRED")
PLAN_MODE = enum("BASELINE", "REPLAN")
PROCTORING_EVENT_TYPES = [
    "CAMERA_TRACK_ENDED", "MIC_TRACK_ENDED", "TAB_HIDDEN", "WINDOW_BLUR", "NETWORK_OFFLINE",
    "SESSION_PAUSED", "SESSION_RESUMED", "PAGE_RELOADED", "SCREEN_SHARE_STOPPED",
    "SECOND_DEVICE_ATTEMPT", "RECORDING_UPLOAD_GAP", "TECHNICAL_ISSUE_REPORTED",
]

# A.3 ProcessingErrorCode: code -> (class, retryable)
ERROR_CODES = {
    "DEADLINE_EXCEEDED": ("TRANSIENT", True),
    "PROVIDER_TIMEOUT": ("TRANSIENT", True),
    "PROVIDER_RATE_LIMITED": ("TRANSIENT", True),
    "PROVIDER_UNAVAILABLE": ("TRANSIENT", True),
    "STORAGE_UNAVAILABLE": ("TRANSIENT", True),
    "INPUT_NOT_FOUND": ("PERMANENT", False),
    "INPUT_CHECKSUM_MISMATCH": ("PERMANENT", False),
    "INPUT_UNSUPPORTED_FORMAT": ("PERMANENT", False),
    "INPUT_TOO_LARGE": ("PERMANENT", False),
    "INPUT_MALWARE_DETECTED": ("PERMANENT", False),
    "INPUT_NO_TEXT": ("PERMANENT", False),
    "OUTPUT_SCHEMA_INVALID": ("PERMANENT", False),
    "OUTPUT_SEMANTIC_INVALID": ("PERMANENT", False),
    "OUTPUT_POLICY_VIOLATION": ("PERMANENT", False),
    "MEDIA_CHUNKS_MISSING": ("PERMANENT", False),
    "MEDIA_CORRUPT": ("PERMANENT", False),
    "UNSUPPORTED_SCHEMA_VERSION": ("PERMANENT", False),
    "INTERNAL_ERROR": ("TRANSIENT", True),
}

# ---------------------------------------------------------------- A.3 fragments

OBJECT_REF = obj({
    "bucket": s(min_len=1),
    "key": s(min_len=1),
    "sha256": HEX64,
    "sizeBytes": integer(0),
    "mimeType": s(min_len=1),
})

RUN_META = obj({
    "provider": s(min_len=1),
    "model": s(min_len=1),
    "promptId": nullable(s()),
    "promptVersion": nullable(s()),
    "promptSha256": nullable(HEX64),
    "providerRequestId": nullable(s()),
    "latencyMs": integer(0),
    "inputTokens": nullable(integer(0)),
    "outputTokens": nullable(integer(0)),
    "providerAttempts": integer(0),
    "repairAttempted": {"type": "boolean"},
    "validationStatus": s(min_len=1),
    "workerVersion": s(min_len=1),
})

REDACTION_HINTS = obj({
    "fullName": nullable(s()),
    "email": nullable(s()),
    "phone": nullable(s()),
})

ANCHORS = obj({k: s(min_len=1) for k in ("1", "2", "3", "4", "5")})

EXPECTED_ANSWER_RUBRIC = obj({
    "keyPoints": arr(s(200), 3, 6),
    "strongSignals": arr(s(200), 1, 4),
    "weakSignals": arr(s(200), 1, 4),
})

QUESTION_IN_REQUEST = obj({
    "text": s(300, 1),
    "competencyCodes": arr(s(min_len=1), 1),
    "expectedAnswerRubric": obj({
        "keyPoints": arr(s()),
        "strongSignals": arr(s()),
        "weakSignals": arr(s()),
    }),
})

RUBRIC_COMPETENCY = obj({
    "code": s(min_len=1),
    "name": s(min_len=1),
    "description": s(),
    "weight": number(0),
    "mandatory": {"type": "boolean"},
    "anchors": ANCHORS,
    "fallbackQuestions": arr(s(300, 1), 2, 6),
})

TAGGED_COMPETENCY = obj(
    {
        "code": s(min_len=1),
        "name": s(min_len=1),
        "description": s(),
        "anchors": ANCHORS,
        "weight": number(0),
        "mandatory": {"type": "boolean"},
    },
    required=["code", "name", "description", "anchors"],
)

COMPUTED_SCORES = obj({
    "competencies": arr(obj({
        "code": s(min_len=1),
        "score": nullable(number(1.0, 5.0)),
        "assessedTurnCount": integer(0),
    })),
    "overallScore": nullable(number(0, 100)),
    "completionState": COMPLETION_STATE,
    "evidenceLevel": EVIDENCE_LEVEL,
})


def request_payload(output_version: str, props: dict, required: list[str] | None = None) -> dict:
    base = {
        "jobId": UUID,
        "attempt": integer(1),
        "deadlineAt": TIMESTAMP,
    }
    all_props = {**base, **props, "outputSchemaVersion": {"const": output_version}}
    req = list(base) + (list(props) if required is None else required) + ["outputSchemaVersion"]
    return obj(all_props, required=req)


def completed_payload(output_version: str, output: dict, extras: dict | None = None) -> dict:
    props = {
        "jobId": UUID,
        "attempt": integer(1),
        "output": output,
        "outputSchemaVersion": {"const": output_version},
        "run": RUN_META,
        **(extras or {}),
    }
    return obj(props)


def failed_payload() -> dict:
    return obj({
        "jobId": UUID,
        "attempt": integer(1),
        "errorCode": enum(*ERROR_CODES),
        "errorClass": enum("TRANSIENT", "PERMANENT"),
        "retryable": {"type": "boolean"},
        "message": s(300),
        "run": nullable(RUN_META),
    })


# ---------------------------------------------------------------- A.2 envelope

ENVELOPE = obj({
    "eventId": UUID,
    "eventType": s(pattern=r"^ihr\.[a-z0-9.\-]+$"),
    "eventVersion": {"const": 1},
    "occurredAt": TIMESTAMP,
    "correlationId": s(min_len=1),
    "causationId": nullable(s(min_len=1)),
    "producer": enum("ihr-core-api", "ihr-ai-worker", "ihr-speech-service", "ihr-media-worker"),
    "entityType": enum("CANDIDATE_DOCUMENT", "APPLICATION", "INTERVIEW", "INTERVIEW_TURN", "MEDIA_ASSET", "TTS_TEXT"),
    "entityId": s(min_len=1),
    "payload": {"type": "object"},
})

# ---------------------------------------------------------------- chapter 7 artefacts

YYYY_MM = s(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
EXTRACTION_WARNINGS_MODEL = enum("PROMPT_INJECTION_SUSPECTED", "TEXT_GARBLED", "MULTIPLE_PEOPLE_DESCRIBED", "NOT_A_CV")
EXTRACTION_WARNINGS_ALL = enum(
    # §6.2
    "OCR_USED", "OCR_PARTIAL", "LOW_OCR_CONFIDENCE", "TEXT_TRUNCATED", "EXTENSION_MISMATCH",
    "MULTI_COLUMN_LAYOUT", "PROMPT_INJECTION_SUSPECTED", "NON_ENGLISH_CONTENT", "DATES_AMBIGUOUS",
    "NO_EMPLOYMENT_HISTORY", "NO_SKILLS_FOUND",
    # §6.1 step 3
    "ENCRYPTED_DOCUMENT",
    # model warnings (§7.1)
    "TEXT_GARBLED", "MULTIPLE_PEOPLE_DESCRIBED", "NOT_A_CV",
)

CV_PROFILE_MODEL = obj({
    "headline": nullable(s(200)),
    "currentTitle": nullable(s(120)),
    "currentEmployer": nullable(s(120)),
    "location": nullable(s(80)),
    "employmentHistory": arr(obj({
        "employer": nullable(s(150)),
        "title": s(150),
        "startDate": nullable(YYYY_MM),
        "endDate": nullable({"anyOf": [YYYY_MM, {"const": "PRESENT"}]}),
        "datePrecision": enum("MONTH", "YEAR", "UNKNOWN"),
        "employmentType": enum("FULL_TIME", "PART_TIME", "CONTRACT", "INTERNSHIP", "FREELANCE", "UNKNOWN"),
        "highlights": arr(s(200), max_items=5),
        "skillsMentioned": arr(s(60), max_items=20),
    }), max_items=30),
    "education": arr(obj({
        "institution": s(150),
        "qualification": s(120),
        "fieldOfStudy": nullable(s(120)),
        "startYear": nullable(integer()),
        "endYear": nullable(integer()),
    }), max_items=10),
    "skills": arr(obj({
        "name": s(60),
        "quote": QUOTE,
        "sourceSection": enum("SKILLS_SECTION", "EXPERIENCE", "PROJECT", "CERTIFICATION", "EDUCATION", "SUMMARY"),
    }), max_items=80),
    "certifications": arr(obj({
        "name": s(150),
        "issuer": nullable(s(100)),
        "year": nullable(integer()),
    }), max_items=20),
    "projects": arr(obj({
        "name": s(150),
        "summary": s(300),
        "skills": arr(s(60), max_items=15),
    }), max_items=15),
    "missingOrAmbiguousFields": arr(enum(
        "EMPLOYMENT_DATES", "CURRENT_ROLE", "SKILLS", "EDUCATION", "EMPLOYER_NAMES", "JOB_TITLES")),
    "extractionWarnings": arr(EXTRACTION_WARNINGS_MODEL),
})

IDENTITY_FIELD = nullable(obj({
    "value": nullable(s()),
    "confidence": number(0, 1),
    "source": enum("LOCAL_REGEX", "LOCAL_HEADER"),
}))
IDENTITY = obj({"name": IDENTITY_FIELD, "email": IDENTITY_FIELD, "phone": IDENTITY_FIELD})

CV_PROFILE = copy.deepcopy(CV_PROFILE_MODEL)
CV_PROFILE["properties"].update({
    "identity": IDENTITY,
    "derived": obj({
        "totalExperienceMonths": nullable(integer(0)),
        "skillsCanonical": arr(obj({"name": s(min_len=1), "originalNames": arr(s(min_len=1), 1)})),
        "mostRecentRoleMonths": nullable(integer(0)),
    }),
    "extraction": obj({
        "pageCount": integer(0),
        "ocrUsed": {"type": "boolean"},
        "ocrPages": integer(0),
        "textChars": integer(0),
        "truncated": {"type": "boolean"},
        "redactionCounts": obj({k: integer(0) for k in
                                ("EMAIL", "PHONE", "URL", "GOV_ID", "CANDIDATE", "DOB", "LINES_REMOVED")}),
        "warnings": arr(EXTRACTION_WARNINGS_ALL),
    }),
})
CV_PROFILE["required"] = list(CV_PROFILE["properties"])

CV_MATCH_CRITERION_MODEL = obj({
    "requirementCode": s(min_len=1),
    "status": FIT_STATUS,
    "evidence": arr(obj({"quote": QUOTE}), 0, 3),
    "rationale": s(400),
    "confidence": CONFIDENCE,
})
CV_MATCH_WARNINGS = enum("CV_SPARSE", "CV_TEXT_TRUNCATED", "PROMPT_INJECTION_SUSPECTED", "NON_ENGLISH_CONTENT")
CV_MATCH_MODEL = obj({
    "criteria": arr(CV_MATCH_CRITERION_MODEL, 1),
    "summary": s(800),
    "strengths": arr(s(200), max_items=5),
    "gaps": arr(s(200), max_items=5),
    "clarificationQuestions": arr(s(300), max_items=5),
    "warnings": arr(CV_MATCH_WARNINGS),
})
CV_MATCH = copy.deepcopy(CV_MATCH_MODEL)
crit = CV_MATCH["properties"]["criteria"]["items"]
crit["properties"]["statusSource"] = enum("MODEL", "DETERMINISTIC_EXPERIENCE")
crit["required"].append("statusSource")

CV_ANCHOR = nullable(obj({"claim": s(200), "quote": QUOTE}))
PLAN_QUESTION_MODEL = obj({
    "slot": integer(1, 10),
    "text": s(300, 20),
    "competencyCodes": arr(s(min_len=1), 1, 2),
    "questionType": QUESTION_TYPE,
    "expectedAnswerRubric": EXPECTED_ANSWER_RUBRIC,
    "timeBudgetSeconds": integer(60, 360),
    "cvAnchor": CV_ANCHOR,
})
INTERVIEW_PLAN_MODEL = obj({"questions": arr(PLAN_QUESTION_MODEL, 4, 10)})
PLAN_QUESTION = copy.deepcopy(PLAN_QUESTION_MODEL)
PLAN_QUESTION["properties"] = {"questionId": s(pattern=r"^q([1-9]|10)$"), **PLAN_QUESTION["properties"],
                               "source": enum("AI_GENERATED", "FALLBACK_BANK")}
PLAN_QUESTION["required"] = list(PLAN_QUESTION["properties"])
INTERVIEW_PLAN = obj({
    "questions": arr(PLAN_QUESTION, 4, 10),
    "coverage": {"type": "object", "additionalProperties": arr(s(pattern=r"^q([1-9]|10)$"))},
    "warnings": arr(enum("QUESTIONS_REPLACED_BY_FALLBACK", "CV_CONTEXT_MISSING")),
})

FOLLOWUP_DECISION_SCHEMA = obj({
    "decision": FOLLOWUP_DECISION,
    "reasonCode": FOLLOWUP_REASON,
    "followUp": nullable(obj({"text": s(200, 1), "competencyCodes": arr(s(min_len=1), 1)})),
})

SEGMENT_INDEX = integer(0)
ANSWER_ASSESSMENT = obj({
    "scores": arr(obj({
        "competencyCode": s(min_len=1),
        "score": nullable(integer(1, 5)),
        "notAssessedReason": nullable(enum("NO_RELEVANT_CONTENT", "ANSWER_TOO_SHORT", "NO_SPEECH", "OFF_TOPIC")),
        "confidence": CONFIDENCE,
        "rationale": s(600, 20),
        "evidence": arr(obj({"segmentIndex": SEGMENT_INDEX, "quote": QUOTE}), 0, 3),
    })),
    "notableEvidence": arr(obj({
        "segmentIndex": SEGMENT_INDEX,
        "quote": QUOTE,
        "relatedCompetencyCode": nullable(s(min_len=1)),
    }), max_items=3),
    "candidateReportedClaims": arr(obj({"claim": s(200), "segmentIndex": SEGMENT_INDEX}), max_items=5),
    "answerFlags": arr(ANSWER_FLAG),
    "uncertainty": arr(s(200), max_items=3),
})

EVIDENCE_REF = obj({"turn": s(pattern=r"^T[1-9]\d*$"), "segmentIndex": SEGMENT_INDEX})
FINAL_REPORT_MODEL = obj({
    "summary": s(1200, 100),
    "competencyNarratives": arr(obj({
        "competencyCode": s(min_len=1),
        "narrative": s(600, 20),
        "evidenceRefs": arr(EVIDENCE_REF, 0, 4),
    }), 1),
    "strengths": arr(obj({"text": s(250), "evidenceRefs": arr(EVIDENCE_REF, 0, 3)}), 0, 5),
    "gaps": arr(obj({"text": s(250), "evidenceRefs": arr(EVIDENCE_REF, 0, 3)}), 0, 5),
    "reviewerFocusAreas": arr(s(250), 1, 5),
    "limitations": arr(s(250), 0, 6),
})
FINAL_REPORT = copy.deepcopy(FINAL_REPORT_MODEL)
FINAL_REPORT["properties"]["computedScores"] = COMPUTED_SCORES
FINAL_REPORT["required"].append("computedScores")

TRANSCRIPT = obj({
    "schemaVersion": {"const": "transcript.v1"},
    "language": {"const": "en"},
    "model": s(min_len=1),
    "durationMs": integer(0),
    "speechDetected": {"type": "boolean"},
    "avgConfidence": number(0, 1),
    "qualityFlags": arr(TRANSCRIPT_QUALITY_FLAG),
    "segments": arr(obj({
        "index": SEGMENT_INDEX,
        "startMs": integer(0),
        "endMs": integer(0),
        "text": s(),
        "confidence": number(0, 1),
        "noSpeechProb": number(0, 1),
        "words": arr(obj({"w": s(), "startMs": integer(0), "endMs": integer(0), "p": number(0, 1)})),
    })),
})

TTS_AUDIO = obj({
    "durationMs": integer(0),
    "provider": s(min_len=1),
    "voiceId": s(min_len=1),
    "cacheHit": {"type": "boolean"},
})

# ---------------------------------------------------------------- A.5 topics (AI + speech)

TOPICS: dict[str, dict] = {}


def add_job_topics(base: str, version: str, req_props: dict, output: dict, extras: dict | None = None,
                   req_required: list[str] | None = None) -> None:
    TOPICS[f"{base}.requested"] = request_payload(version, req_props, req_required)
    TOPICS[f"{base}.completed"] = completed_payload(version, output, extras)
    TOPICS[f"{base}.failed"] = failed_payload()


add_job_topics(
    "ihr.cv.parse", "cv_profile.v1",
    {
        "documentId": UUID,
        "candidateId": UUID,
        "document": OBJECT_REF,
        "originalExtension": enum("pdf", "docx", "doc"),
        "redactionHints": REDACTION_HINTS,
    },
    obj({"cvProfile": OBJECT_REF, "cvText": OBJECT_REF}),
    {
        "pageCount": integer(0),
        "ocrUsed": {"type": "boolean"},
        "textChars": integer(0),
        "identity": IDENTITY,
        "warnings": arr(EXTRACTION_WARNINGS_ALL),
    },
)

add_job_topics(
    "ihr.cv.match", "cv_match.v1",
    {
        "applicationId": UUID,
        "cvAnalysisId": UUID,
        "rubricVersionId": UUID,
        "cvProfile": OBJECT_REF,
        "cvText": OBJECT_REF,
        "job": obj({
            "title": s(min_len=1),
            "summary": s(4000),
            "experienceMinMonths": nullable(integer(0)),
            "experienceMaxMonths": nullable(integer(0)),
        }),
        "requirements": arr(obj(
            {
                "code": s(min_len=1),
                "category": REQUIREMENT_CATEGORY,
                "text": s(min_len=1),
                "skillName": s(min_len=1),
                "type": REQUIREMENT_TYPE,
                "weight": integer(1, 5),
                "minMonths": integer(0),
            },
            required=["code", "category", "text", "type", "weight"],
        ), 1, 30),
    },
    OBJECT_REF,
)

add_job_topics(
    "ihr.interview.plan", "interview_plan.v1",
    {
        "interviewId": UUID,
        "rubricVersionId": UUID,
        "templateVersionId": UUID,
        "planMode": PLAN_MODE,
        "mainQuestionCount": integer(4, 10),
        "maxAnswerSeconds": integer(1),
        "competencies": arr(RUBRIC_COMPETENCY, 3, 8),
        "job": obj({
            "title": s(min_len=1),
            "summary": s(),
            "mustHaveSkills": arr(s(min_len=1)),
            "preferredSkills": arr(s(min_len=1)),
        }),
        "cvProfile": nullable(OBJECT_REF),
    },
    OBJECT_REF,
    {"questionCount": integer(4, 10), "fallbackUsedCount": integer(0, 10)},
)

add_job_topics(
    "ihr.interview.followup", "followup_decision.v1",
    {
        "interviewId": UUID,
        "turnId": UUID,
        "mainTurnId": UUID,
        "redactionHints": REDACTION_HINTS,
        "question": QUESTION_IN_REQUEST,
        "transcript": OBJECT_REF,
    },
    FOLLOWUP_DECISION_SCHEMA,
)

add_job_topics(
    "ihr.interview.answer-assessment", "answer_assessment.v1",
    {
        "interviewId": UUID,
        "turnId": UUID,
        "turnKind": TURN_KIND,
        "redactionHints": REDACTION_HINTS,
        "question": QUESTION_IN_REQUEST,
        "competencies": arr(TAGGED_COMPETENCY),
        "transcript": OBJECT_REF,
        "transcriptQuality": obj({
            "avgConfidence": number(0, 1),
            "speechDetected": {"type": "boolean"},
            "qualityFlags": arr(TRANSCRIPT_QUALITY_FLAG),
        }),
        "priorContext": arr(obj({"questionText": s(min_len=1), "transcriptExcerpt": s(1200)}), 0, 2),
    },
    OBJECT_REF,
)

add_job_topics(
    "ihr.interview.final-report", "final_report.v1",
    {
        "interviewId": UUID,
        "rubricVersionId": UUID,
        "competencies": arr(RUBRIC_COMPETENCY, 1),
        "redactionHints": REDACTION_HINTS,
        "computedScores": COMPUTED_SCORES,
        "turns": arr(obj({
            "turnId": UUID,
            "turnNumber": integer(1),
            "kind": TURN_KIND,
            "questionText": s(min_len=1),
            "competencyCodes": arr(s(min_len=1)),
            "transcript": nullable(OBJECT_REF),
            "assessment": nullable(OBJECT_REF),
            "transcriptStatus": TRANSCRIPT_STATUS,
            "assessmentStatus": PROCESSING_JOB_STATUS,
        })),
        "proctoringSummary": {
            "type": "object",
            "propertyNames": {"enum": PROCTORING_EVENT_TYPES},
            "additionalProperties": integer(0),
        },
    },
    OBJECT_REF,
)

add_job_topics(
    "ihr.speech.transcription", "transcript.v1",
    {
        "interviewId": UUID,
        "turnId": UUID,
        "audio": OBJECT_REF,
        "language": {"const": "en"},
        "vocabularyHints": arr(s(min_len=1), 0, 40),
        "maxDurationSeconds": integer(1),
    },
    OBJECT_REF,
    {
        "durationMs": integer(0),
        "segmentCount": integer(0),
        "avgConfidence": number(0, 1),
        "speechDetected": {"type": "boolean"},
        "qualityFlags": arr(TRANSCRIPT_QUALITY_FLAG),
    },
)

add_job_topics(
    "ihr.speech.synthesis", "tts_audio.v1",
    {
        "interviewId": nullable(UUID),
        "turnId": nullable(UUID),
        "purpose": enum("OPENING", "WARMUP", "QUESTION", "FOLLOW_UP", "CLOSING", "SYSTEM_MESSAGE"),
        "text": s(600, 1),
        "voiceProfileKey": s(min_len=1),
        "cacheKey": HEX64,
    },
    OBJECT_REF,
    {
        "durationMs": integer(0),
        "provider": s(min_len=1),
        "voiceId": s(min_len=1),
        "cacheHit": {"type": "boolean"},
    },
)

AI_SCHEMAS = {
    "cv_profile.v1": (CV_PROFILE, CV_PROFILE_MODEL),
    "cv_match.v1": (CV_MATCH, CV_MATCH_MODEL),
    "interview_plan.v1": (INTERVIEW_PLAN, INTERVIEW_PLAN_MODEL),
    "followup_decision.v1": (FOLLOWUP_DECISION_SCHEMA, FOLLOWUP_DECISION_SCHEMA),
    "answer_assessment.v1": (ANSWER_ASSESSMENT, ANSWER_ASSESSMENT),
    "final_report.v1": (FINAL_REPORT, FINAL_REPORT_MODEL),
    "transcript.v1": (TRANSCRIPT, None),
    "tts_audio.v1": (TTS_AUDIO, None),
}


def write(path: Path, schema: dict, schema_id: str, title: str, defs: dict | None = None) -> None:
    doc = {"$schema": DRAFT, "$id": schema_id, "title": title, **schema}
    if defs:
        doc["$defs"] = defs
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    write(EVENTS / "envelope.v1.json", ENVELOPE, f"{BASE_ID}/events/envelope.v1.json", "Event envelope (A.2)")
    for topic, payload in TOPICS.items():
        write(EVENTS / f"{topic}.v1.json", payload, f"{BASE_ID}/events/{topic}.v1.json",
              f"Payload of {topic} (A.5)")
    for name, (full, model) in AI_SCHEMAS.items():
        defs = {"ModelOutput": model} if model is not None and model is not full else None
        write(AI / f"{name}.json", full, f"{BASE_ID}/ai-schemas/{name}.json", f"{name} (chapter 7)", defs)
    errors = {code: {"errorClass": c, "retryable": r} for code, (c, r) in ERROR_CODES.items()}
    (EVENTS / "processing_error_codes.v1.json").write_text(json.dumps(errors, indent=2) + "\n", encoding="utf-8")
    print(f"wrote envelope + {len(TOPICS)} topic schemas + {len(AI_SCHEMAS)} artefact schemas")


if __name__ == "__main__":
    main()
