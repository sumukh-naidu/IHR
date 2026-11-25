"""CV_MATCH (AI Specification v1.1 §7.2): load profile + text -> prompt -> Kimi -> validate ->
deterministic EXPERIENCE rule (A-20) -> write -> .completed. Core computes score and band (S-05).
"""
from __future__ import annotations

import copy
import json
import time
from datetime import datetime
from typing import Callable

from ihr_worker_common import contracts
from ihr_worker_common.errors import ProcessingError
from ihr_worker_common.events import utc_now
from ihr_worker_common.log import get_logger
from ihr_worker_common.metrics import counter
from ihr_worker_common.processor import HandlerResult, JobContext
from ihr_worker_common.storage import put_json, read_verified

from .. import debug_capture
from ..enrich.experience import experience_status, skill_months
from ..enrich.skills import canonical
from ..evidence import quote_found
from ..llm.base import TASK_PARAMS, ContextLengthExceeded, LlmClient
from ..llm.structured import Validation, call_structured
from ..policy import language, questions
from ..privacy import injection
from ..prompts import PromptRegistry
from ..tasks import TASKS, generated_bucket
from . import metrics
from .common import capture_calls, run_meta, capture_failure

log = get_logger("ihr.cv_match")

SCHEMA = "cv_match.v1"
CONTEXT_SHRINK = 0.75
CARRIED_WARNINGS = {"PROMPT_INJECTION_SUSPECTED": "PROMPT_INJECTION_SUSPECTED",
                    "NON_ENGLISH_CONTENT": "NON_ENGLISH_CONTENT", "TEXT_TRUNCATED": "CV_TEXT_TRUNCATED"}


def _years(months: int | None) -> str:
    return "not specified" if months is None else f"{months / 12:g}"


def requirement_lines(reqs: list[dict]) -> str:
    return "\n".join(f"- code: {r['code']} | type: {r['type']} | category: {r['category']} | text: {r['text']}"
                     for r in reqs)


class CvMatchHandler:
    task = "CV_MATCH"

    def __init__(self, llm: LlmClient, prompts: PromptRegistry, *, debug_capture_enabled: bool = True,
                 clock: Callable[[], datetime] = utc_now) -> None:
        self.spec = TASKS["CV_MATCH"]
        self.request_topic = self.spec.request_topic
        self.output_schema_version = self.spec.output_schema_version
        self.llm = llm
        self.prompt = prompts.get(self.spec.prompt_id)
        self.capture = debug_capture_enabled
        self.clock = clock
        self._dropped_quotes = counter("ihr_ai_evidence_dropped_total", "Unverified quotes dropped", ("task",))
        self._overrides = counter("ihr_ai_experience_override_total", "A-20 deterministic overrides", ("changed",))

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
        profile = json.loads(read_verified(ctx.storage, p["cvProfile"]))
        text = read_verified(ctx.storage, p["cvText"]).decode("utf-8")
        reqs = p["requirements"]
        codes = [r["code"] for r in reqs]
        if len(set(codes)) != len(codes):
            raise ProcessingError("OUTPUT_SEMANTIC_INVALID", "Request contains duplicate requirement codes.")

        # §5.1: parsed profile without the identity block (extraction metadata is not needed either)
        model_profile = {k: v for k, v in profile.items() if k not in ("identity", "extraction")}
        try:
            result = self._call(ctx, p, model_profile, text)
        except ContextLengthExceeded:
            text = text[: int(len(text) * CONTEXT_SHRINK)]
            try:
                result = self._call(ctx, p, model_profile, text)
            except ContextLengthExceeded:
                raise ProcessingError("INPUT_TOO_LARGE", "The CV text is too long for the model.") from None

        fit = copy.deepcopy(result.value)
        by_code = {c["requirementCode"]: c for c in fit["criteria"]}
        fit["criteria"] = [by_code[c] for c in codes]           # request order
        dropped = 0
        for c in fit["criteria"]:
            c["statusSource"] = "MODEL"
            verified = [e for e in c["evidence"] if quote_found(e["quote"], text)]
            dropped += len(c["evidence"]) - len(verified)
            c["evidence"] = verified
        if dropped:
            self._dropped_quotes.labels(self.task).inc(dropped)

        # A-20 deterministic EXPERIENCE override
        current_month = self.clock().strftime("%Y-%m")
        history = profile.get("employmentHistory") or []
        total = (profile.get("derived") or {}).get("totalExperienceMonths")
        for r in reqs:
            if r["category"] != "EXPERIENCE" or r.get("minMonths") is None:
                continue
            months = total
            if r.get("skillName"):
                months = skill_months(history, r["skillName"], current_month, canonical) or total
            c = by_code[r["code"]]
            status = experience_status(months, r["minMonths"])
            self._overrides.labels(str(status != c["status"]).lower()).inc()
            c["status"] = status
            c["statusSource"] = "DETERMINISTIC_EXPERIENCE"
            if status in ("MISSING", "UNKNOWN"):
                c["evidence"] = []
            c["rationale"] = (f"Calculated from stated employment dates: {months} months against {r['minMonths']} required."
                              if months is not None else
                              f"No dated employment history is stated to compare against {r['minMonths']} months required.")

        # A-21: failing clarification questions are dropped
        kept = []
        for q in fit["clarificationQuestions"]:
            rules = questions.violations(q)
            for r in rules:
                metrics.policy_rejection(self.task, r)
            if not rules:
                kept.append(q)
        fit["clarificationQuestions"] = kept

        warnings = set(fit["warnings"])
        for src, dst in CARRIED_WARNINGS.items():
            if src in ((profile.get("extraction") or {}).get("warnings") or []):
                warnings.add(dst)
        if injection.detect(text):
            warnings.add("PROMPT_INJECTION_SUSPECTED")
        if "PROMPT_INJECTION_SUSPECTED" in warnings:
            metrics.injection_flag(self.task)
        fit["warnings"] = sorted(warnings)
        contracts.validate_artefact(SCHEMA, fit)

        ref = put_json(ctx.storage, generated_bucket(), self.spec.output_key(p), fit)
        debug_capture.capture(ctx.storage, enabled=self.capture, job_id=ctx.job_id, task=self.task,
                              prompt_id=self.prompt.prompt_id, prompt_version=self.prompt.version,
                              calls=capture_calls(result))
        log.info("cv_match_done", job_id=ctx.job_id, statuses={c["requirementCode"]: c["status"] for c in fit["criteria"]},
                 repaired=result.repaired)
        return HandlerResult(output=ref, run=run_meta(self.llm, self.prompt, result,
                                                      TASK_PARAMS[self.task].model_slot, t0))

    def _call(self, ctx: JobContext, p: dict, model_profile: dict, text: str):
        job = p["job"]
        user = self.prompt.user(
            jobTitle=injection.escape_tags(job["title"]), jobSummary=injection.escape_tags(job.get("summary") or ""),
            minYears=_years(job.get("experienceMinMonths")), maxYears=_years(job.get("experienceMaxMonths")),
            requirementLines=injection.escape_tags(requirement_lines(p["requirements"])),
            profileJson=injection.escape_tags(json.dumps(model_profile, ensure_ascii=False, separators=(",", ":"))),
            redactedText=text)
        messages = [{"role": "system", "content": self.prompt.system()}, {"role": "user", "content": user}]
        codes = [r["code"] for r in p["requirements"]]
        return call_structured(self.llm, task=self.task, params=TASK_PARAMS[self.task], messages=messages,
                               deadline=ctx.deadline, validate=lambda v: validate_model_output(v, codes, text))


def validate_model_output(value: dict, codes: list[str], text: str) -> Validation:
    """§7.2 validation: exact requirement set, quote rules (A-23), language policy (A-22)."""
    errors = contracts.artefact_errors(SCHEMA, value, model_part=True)
    if errors:
        return Validation("schema", errors)
    sem = []
    got = [c["requirementCode"] for c in value["criteria"]]
    missing = [c for c in codes if c not in got]
    extra = sorted({c for c in got if c not in codes})
    dups = sorted({c for c in got if got.count(c) > 1})
    if missing:
        sem.append(f"/criteria: missing requirementCode {', '.join(missing)}")
    if extra:
        sem.append(f"/criteria: unknown requirementCode {', '.join(extra)}")
    if dups:
        sem.append(f"/criteria: duplicate requirementCode {', '.join(dups)}")
    for i, c in enumerate(value["criteria"]):
        ev = c["evidence"]
        if c["status"] in ("MATCHED", "PARTIAL"):
            # §7.2: at least 1 verified quote; unverified extra quotes are dropped in post-processing
            if not ev:
                sem.append(f"/criteria/{i}/evidence: {c['status']} needs 1 to 3 quotes")
            elif not any(quote_found(e["quote"], text) for e in ev):
                sem.extend(f"/criteria/{i}/evidence/{j}/quote: not found verbatim in <cv_text>" for j in range(len(ev)))
        elif ev:
            sem.append(f"/criteria/{i}/evidence: {c['status']} must have no quotes")
    if sem:
        return Validation("semantic", sem)
    fields = {"/summary": value["summary"]}
    fields.update({f"/strengths/{i}": s for i, s in enumerate(value["strengths"])})
    fields.update({f"/gaps/{i}": s for i, s in enumerate(value["gaps"])})
    fields.update({f"/criteria/{i}/rationale": c["rationale"] for i, c in enumerate(value["criteria"])})
    pol = language.check_fields(fields)
    return Validation("policy", pol) if pol else Validation.ok()
