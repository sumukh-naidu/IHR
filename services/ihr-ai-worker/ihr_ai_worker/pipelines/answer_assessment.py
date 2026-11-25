"""ANSWER_ASSESSMENT (AI Specification v1.1 §7.5).

Validation: schema; exact competency set; evidence quotes verified within the cited segment ±1 (A-23);
null/reason consistency; language policy (A-22). Deterministic adjustments in code:
- low transcript quality -> every confidence LOW + LIKELY_TRANSCRIPTION_ISSUE;
- speechDetected=false -> no model call, all scores null (NO_SPEECH);
- prompt-injection detector on the transcript -> PROMPT_INJECTION_SUSPECTED (scores still produced).
WARMUP / CLOSING turns are not scored.
"""
from __future__ import annotations

import copy
import time

from ihr_worker_common import contracts
from ihr_worker_common.errors import ProcessingError
from ihr_worker_common.events import worker_version
from ihr_worker_common.log import get_logger
from ihr_worker_common.metrics import counter
from ihr_worker_common.processor import HandlerResult, JobContext
from ihr_worker_common.storage import put_json

from .. import debug_capture
from ..evidence import quote_in_segments
from ..llm.base import TASK_PARAMS, LlmClient
from ..llm.structured import Validation, call_structured
from ..policy import language
from ..privacy import injection
from ..privacy.redaction import redact_transcript
from ..prompts import PromptRegistry
from ..tasks import TASKS, generated_bucket
from . import metrics
from .common import capture_calls, run_meta, capture_failure
from .transcripts import load_segments, segment_lines

log = get_logger("ihr.answer_assessment")

SCHEMA = "answer_assessment.v1"
LOW_TRANSCRIPT_CONFIDENCE = 0.55
LOW_QUALITY_FLAGS = {"LOW_CONFIDENCE", "POSSIBLE_HALLUCINATION"}
UNSCORED_TURNS = {"WARMUP", "CLOSING"}
NO_SPEECH_RATIONALE = "No speech was detected in the answer recording, so this competency could not be assessed."


def competency_lines(comps: list[dict]) -> str:
    return "\n".join(f"- code: {c['code']} | {c['name']} | {c['description']} | anchors: "
                     + "; ".join(f"{k}={c['anchors'][k]}" for k in ("1", "2", "3", "4", "5")) for c in comps)


def no_speech_output(codes: list[str]) -> dict:
    return {"scores": [{"competencyCode": c, "score": None, "notAssessedReason": "NO_SPEECH", "confidence": "LOW",
                        "rationale": NO_SPEECH_RATIONALE, "evidence": []} for c in codes],
            "notableEvidence": [], "candidateReportedClaims": [], "answerFlags": ["NO_SUBSTANTIVE_ANSWER"],
            "uncertainty": []}


class AnswerAssessmentHandler:
    task = "ANSWER_ASSESSMENT"

    def __init__(self, llm: LlmClient, prompts: PromptRegistry, *, debug_capture_enabled: bool = True) -> None:
        self.spec = TASKS["ANSWER_ASSESSMENT"]
        self.request_topic = self.spec.request_topic
        self.output_schema_version = self.spec.output_schema_version
        self.llm = llm
        self.prompt = prompts.get(self.spec.prompt_id)
        self.capture = debug_capture_enabled
        self._adjust = counter("ihr_ai_assessment_adjustments_total", "Deterministic assessment adjustments", ("kind",))

    def output_location(self, payload: dict) -> tuple[str, str]:
        return generated_bucket(), self.spec.output_key(payload)

    def handle(self, ctx: JobContext) -> HandlerResult:
        try:
            return self._handle(ctx)
        except ProcessingError as e:
            capture_failure(self, ctx, e)
            raise

    def _handle(self, ctx: JobContext) -> HandlerResult:
        t0 = time.monotonic()
        p = ctx.payload
        unscored = p["turnKind"] in UNSCORED_TURNS
        comps = [] if unscored else p["competencies"]
        codes = [c["code"] for c in comps]
        quality = p["transcriptQuality"]
        transcript, texts = load_segments(ctx.storage, p["transcript"], p.get("redactionHints"))

        if not quality["speechDetected"] or transcript.get("speechDetected") is False or not texts:
            out = no_speech_output(codes)
            self._adjust.labels("no_speech").inc()
            return self._write(ctx, out, _no_call_run(self.llm, self.prompt, t0))

        result = call_structured(self.llm, task=self.task, params=TASK_PARAMS[self.task],
                                 messages=self._messages(p, comps, texts, unscored), deadline=ctx.deadline,
                                 validate=lambda v: validate_model_output(v, codes, texts))
        out = copy.deepcopy(result.value)
        # unverified notable evidence is dropped (score evidence was already verified in validation)
        out["notableEvidence"] = [e for e in out["notableEvidence"]
                                  if quote_in_segments(e["quote"], texts, e["segmentIndex"])]
        flags = list(dict.fromkeys(out["answerFlags"]))
        if quality["avgConfidence"] < LOW_TRANSCRIPT_CONFIDENCE or LOW_QUALITY_FLAGS & set(quality["qualityFlags"]):
            for s in out["scores"]:
                s["confidence"] = "LOW"
            if "LIKELY_TRANSCRIPTION_ISSUE" not in flags:
                flags.append("LIKELY_TRANSCRIPTION_ISSUE")
            self._adjust.labels("low_transcript_quality").inc()
        if injection.detect(" ".join(s["text"] for s in transcript["segments"])) and \
                "PROMPT_INJECTION_SUSPECTED" not in flags:
            flags.append("PROMPT_INJECTION_SUSPECTED")
            self._adjust.labels("injection").inc()
        out["answerFlags"] = flags
        if "PROMPT_INJECTION_SUSPECTED" in flags:
            metrics.injection_flag(self.task)
        order = {c: i for i, c in enumerate(codes)}
        out["scores"].sort(key=lambda s: order[s["competencyCode"]])
        debug_capture.capture(ctx.storage, enabled=self.capture, job_id=ctx.job_id, task=self.task,
                              prompt_id=self.prompt.prompt_id, prompt_version=self.prompt.version,
                              calls=capture_calls(result))
        return self._write(ctx, out, run_meta(self.llm, self.prompt, result, TASK_PARAMS[self.task].model_slot, t0))

    def _write(self, ctx: JobContext, out: dict, run: dict) -> HandlerResult:
        contracts.validate_artefact(SCHEMA, out)
        ref = put_json(ctx.storage, generated_bucket(), self.spec.output_key(ctx.payload), out)
        log.info("assessment_done", job_id=ctx.job_id, scores={s["competencyCode"]: s["score"] for s in out["scores"]},
                 flags=out["answerFlags"])
        return HandlerResult(output=ref, run=run)

    def _messages(self, p: dict, comps: list[dict], texts: list[str], unscored: bool) -> list[dict]:
        q, rub = p["question"], p["question"]["expectedAnswerRubric"]
        names = ((p.get("redactionHints") or {}).get("fullName"),)
        prior = "none"
        if p.get("priorContext"):
            prior = "\n".join(f"Question: {c['questionText']}\nAnswer excerpt: "
                              f"{redact_transcript(c['transcriptExcerpt'], names=names).text}" for c in p["priorContext"])
        tq = p["transcriptQuality"]
        system = self.prompt.system()
        if unscored:
            system += "\n\n" + self.prompt.variants["System (WARMUP and CLOSING only)"]
        esc = injection.escape_tags
        user = self.prompt.user(
            questionType=q.get("questionType", "UNSPECIFIED"), turnKind=p["turnKind"], questionText=esc(q["text"]),
            keyPoints=esc("; ".join(rub["keyPoints"])), strongSignals=esc("; ".join(rub["strongSignals"])),
            weakSignals=esc("; ".join(rub["weakSignals"])), competencyLines=esc(competency_lines(comps)),
            priorContext=esc(prior), avgConfidence=f"{tq['avgConfidence']:.2f}",
            flags=", ".join(tq["qualityFlags"]) or "none", segmentLines=segment_lines(texts))
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def validate_model_output(value: dict, codes: list[str], texts: list[str]) -> Validation:
    errors = contracts.artefact_errors(SCHEMA, value, model_part=True)
    if errors:
        return Validation("schema", errors)
    sem = []
    got = [s["competencyCode"] for s in value["scores"]]
    if sorted(got) != sorted(codes):
        missing = [c for c in codes if c not in got]
        extra = sorted({c for c in got if c not in codes or got.count(c) > 1})
        sem.append(f"/scores: competency set must be exactly {codes}"
                   + (f"; missing {missing}" if missing else "") + (f"; unknown or duplicate {extra}" if extra else ""))
    for i, s in enumerate(value["scores"]):
        if (s["score"] is None) != (s["notAssessedReason"] is not None):
            sem.append(f"/scores/{i}: notAssessedReason is required exactly when score is null")
        if s["score"] is None and s["evidence"]:
            sem.append(f"/scores/{i}/evidence: must be empty when score is null")
        if s["score"] is not None and not (1 <= len(s["evidence"]) <= 3):
            sem.append(f"/scores/{i}/evidence: 1 to 3 items required for a score")
        for j, e in enumerate(s["evidence"]):
            if not quote_in_segments(e["quote"], texts, e["segmentIndex"]):
                sem.append(f"/scores/{i}/evidence/{j}: quote not found in segment {e['segmentIndex']} (±1)")
    for i, c in enumerate(value["candidateReportedClaims"]):
        if not 0 <= c["segmentIndex"] < len(texts):
            sem.append(f"/candidateReportedClaims/{i}/segmentIndex: no such segment")
    if sem:
        return Validation("semantic", sem)
    fields = {f"/scores/{i}/rationale": s["rationale"] for i, s in enumerate(value["scores"])}
    fields.update({f"/uncertainty/{i}": u for i, u in enumerate(value["uncertainty"])})
    pol = language.check_fields(fields)
    return Validation("policy", pol) if pol else Validation.ok()


def _no_call_run(llm, prompt, t0) -> dict:
    return {"provider": llm.provider_name, "model": llm.model_for("standard"), "promptId": prompt.prompt_id,
            "promptVersion": prompt.version, "promptSha256": prompt.sha256, "providerRequestId": None,
            "latencyMs": int((time.monotonic() - t0) * 1000), "inputTokens": None, "outputTokens": None,
            "providerAttempts": 0, "repairAttempted": False, "validationStatus": "NOT_CALLED",
            "workerVersion": worker_version()}
