"""INTERVIEW_PLAN (AI Specification v1.1 §7.3): allocation (A-25) -> profile summary -> Kimi ->
validate per question -> replace invalid questions from the fallback bank (§8.2) -> write -> .completed.
Never .failed for invalid model output while a fallback question is possible.
"""
from __future__ import annotations

import copy
import json
import time
from datetime import datetime
from typing import Callable

from rapidfuzz import fuzz

from ihr_worker_common import contracts
from ihr_worker_common.errors import ProcessingError
from ihr_worker_common.events import utc_now
from ihr_worker_common.log import get_logger
from ihr_worker_common.metrics import counter
from ihr_worker_common.processor import HandlerResult, JobContext
from ihr_worker_common.storage import put_json, read_verified

from .. import debug_capture
from ..enrich.experience import role_interval
from ..evidence import quote_found
from ..llm.base import TASK_PARAMS, LlmClient
from ..llm.structured import Validation, call_structured
from ..policy import questions as qpolicy
from ..privacy import injection
from ..prompts import PromptRegistry
from ..tasks import TASKS, generated_bucket
from . import metrics
from .common import capture_calls, run_meta, capture_failure
from .plan_allocation import allocate, fallback_question

log = get_logger("ihr.interview_plan")

SCHEMA = "interview_plan.v1"
SIMILARITY_LIMIT = 0.85
MAX_ANCHORS = 2
ALL_TYPES = ["TECHNICAL_CONCEPT", "SCENARIO", "PAST_EXPERIENCE", "PROBLEM_SOLVING", "ROLE_MOTIVATION"]


def profile_summary(profile: dict | None, current_month: str) -> str:
    """§7.3: headline, roles (title, employer, duration months), canonical skills, projects; or "none"."""
    if not profile:
        return "none"
    roles = []
    for e in profile.get("employmentHistory") or []:
        iv = role_interval(e, current_month)
        roles.append({"title": e.get("title"), "employer": e.get("employer"),
                      "durationMonths": (iv[1] - iv[0] + 1) if iv else None})
    summary = {"headline": profile.get("headline"), "roles": roles,
               "skills": [s["name"] for s in (profile.get("derived") or {}).get("skillsCanonical") or []],
               "projects": [{"name": p.get("name"), "summary": p.get("summary")} for p in profile.get("projects") or []]}
    return json.dumps(summary, ensure_ascii=False, separators=(",", ":"))


def similarity(a: str, b: str) -> float:
    return fuzz.token_set_ratio(a, b) / 100


class InterviewPlanHandler:
    task = "INTERVIEW_PLAN"

    def __init__(self, llm: LlmClient, prompts: PromptRegistry, *, debug_capture_enabled: bool = True,
                 clock: Callable[[], datetime] = utc_now) -> None:
        self.spec = TASKS["INTERVIEW_PLAN"]
        self.request_topic = self.spec.request_topic
        self.output_schema_version = self.spec.output_schema_version
        self.llm = llm
        self.prompt = prompts.get(self.spec.prompt_id)
        self.capture = debug_capture_enabled
        self.clock = clock

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
        n = p["mainQuestionCount"]
        comps = {c["code"]: c for c in p["competencies"]}
        slots = allocate(p["competencies"], n)
        if len(slots) < n:
            raise ProcessingError("OUTPUT_SEMANTIC_INVALID", "Competencies cannot cover the requested question count.")

        profile, cv_text = None, ""
        if p.get("cvProfile"):
            profile = json.loads(read_verified(ctx.storage, p["cvProfile"]))
            text_key = p["cvProfile"]["key"].replace("/profile/", "/text/").removesuffix(".json") + ".txt"
            if ctx.storage.exists(p["cvProfile"]["bucket"], text_key):   # same CV_PARSE job (A.6 key layout)
                cv_text = ctx.storage.get_bytes(p["cvProfile"]["bucket"], text_key).decode("utf-8")
        summary = profile_summary(profile, self.clock().strftime("%Y-%m"))
        anchor_source = summary + "\n" + cv_text

        messages = self._messages(p, slots, comps, summary)
        name_tokens: tuple[str, ...] = ()
        validator = PlanValidator(slots, comps, anchor_source, name_tokens)
        result = None
        try:
            result = call_structured(self.llm, task=self.task, params=TASK_PARAMS[self.task], messages=messages,
                                     deadline=ctx.deadline, validate=validator.validate)
            plan_q, invalid = validator.final(result.value)
            for i in sorted(invalid):
                for rule in {m.split("question policy ")[1] for m in validator.question_errors(result.value["questions"]).get(i, [])
                             if "question policy " in m}:
                    metrics.policy_rejection(self.task, rule)
        except ProcessingError as e:
            if e.code not in ("OUTPUT_SCHEMA_INVALID", "OUTPUT_SEMANTIC_INVALID", "OUTPUT_POLICY_VIOLATION"):
                raise
            # Repair failed: plan built entirely from the fallback bank (§7.3)
            plan_q, invalid = [None] * n, set(range(n))
            log.warning("plan_full_fallback", job_id=ctx.job_id, reason=e.code)

        used: set[str] = set()
        final = []
        for i, q in enumerate(plan_q):
            if i in invalid or q is None:
                fb = fallback_question(comps[slots[i]], used, p["maxAnswerSeconds"], i + 1)
                if fb is None:
                    raise ProcessingError("OUTPUT_SEMANTIC_INVALID", "No fallback question left for a competency.")
                metrics.fallback_question("full_plan" if len(invalid) == n else "invalid_question")
                final.append(fb)
            else:
                q = copy.deepcopy(q)
                q["source"] = "AI_GENERATED"
                final.append(q)
        for i, q in enumerate(final):
            q["questionId"] = f"q{i + 1}"
            q["slot"] = i + 1
        fallback_count = sum(1 for q in final if q["source"] == "FALLBACK_BANK")

        coverage: dict[str, list[str]] = {}
        for q in final:
            for code in q["competencyCodes"]:
                coverage.setdefault(code, []).append(q["questionId"])
        warnings = []
        if fallback_count:
            warnings.append("QUESTIONS_REPLACED_BY_FALLBACK")
        if profile is None:
            warnings.append("CV_CONTEXT_MISSING")
        plan = {"questions": [_ordered(q) for q in final], "coverage": coverage, "warnings": warnings}
        contracts.validate_artefact(SCHEMA, plan)

        ref = put_json(ctx.storage, generated_bucket(), self.spec.output_key(p), plan)
        if result is not None:
            debug_capture.capture(ctx.storage, enabled=self.capture, job_id=ctx.job_id, task=self.task,
                                  prompt_id=self.prompt.prompt_id, prompt_version=self.prompt.version,
                                  calls=capture_calls(result))
            run = run_meta(self.llm, self.prompt, result, TASK_PARAMS[self.task].model_slot, t0)
        else:
            run = {**_fallback_run(self.llm, self.prompt, t0)}
        log.info("plan_done", job_id=ctx.job_id, slots=slots, fallback=fallback_count)
        return HandlerResult(output=ref, run=run, extras={"questionCount": n, "fallbackUsedCount": fallback_count})

    def _messages(self, p: dict, slots: list[str], comps: dict, summary: str) -> list[dict]:
        job = p["job"]
        mins = p["maxAnswerSeconds"] / 60
        comp_lines = "\n".join(
            f"- code: {c['code']} | name: {c['name']} | description: {c['description']} | "
            f"anchors: 1={c['anchors']['1']}; 3={c['anchors']['3']}; 5={c['anchors']['5']}" for c in p["competencies"])
        slot_lines = "\n".join(f"{i + 1}. {code}" for i, code in enumerate(slots))
        system = self.prompt.system(maxAnswerMinutes=f"{mins:g}", allowedTypes=", ".join(ALL_TYPES),
                                    maxAnswerSeconds=p["maxAnswerSeconds"])
        user = self.prompt.user(
            jobTitle=injection.escape_tags(job["title"]), jobSummary=injection.escape_tags(job.get("summary") or ""),
            mustHaveSkills=injection.escape_tags(", ".join(job.get("mustHaveSkills") or []) or "none"),
            preferredSkills=injection.escape_tags(", ".join(job.get("preferredSkills") or []) or "none"),
            minYears="not specified", maxYears="not specified",
            competencyLines=injection.escape_tags(comp_lines), slotLines=slot_lines,
            profileSummary=injection.escape_tags(summary))
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]


class PlanValidator:
    """§7.3 validation. validate() drives repair: only plan-level problems or > half invalid questions
    trigger it; final() returns the questions and the set of invalid slot indexes to replace."""

    def __init__(self, slots, comps, anchor_source, name_tokens):
        self.slots, self.comps, self.source, self.names = slots, comps, anchor_source, name_tokens

    def question_errors(self, qs: list[dict]) -> dict[int, list[str]]:
        errs: dict[int, list[str]] = {}
        anchors = 0
        allowed = set(self.comps)
        for i, q in enumerate(qs):
            e = []
            if q["slot"] != i + 1:
                e.append(f"/questions/{i}/slot: must be {i + 1}")
            codes = q["competencyCodes"]
            if i < len(self.slots) and codes[0] != self.slots[i]:
                e.append(f"/questions/{i}/competencyCodes/0: must be {self.slots[i]}")
            if not set(codes) <= allowed or len(set(codes)) != len(codes):
                e.append(f"/questions/{i}/competencyCodes: unknown or duplicate code")
            if q["questionType"] not in ALL_TYPES:
                e.append(f"/questions/{i}/questionType: not allowed")
            for rule in qpolicy.violations(q["text"], name_tokens=self.names, competency_codes=codes,
                                           allowed_codes=allowed):
                e.append(f"/questions/{i}/text: question policy {rule}")
            for j, other in enumerate(qs):
                if j != i and similarity(q["text"], other["text"]) >= SIMILARITY_LIMIT:
                    e.append(f"/questions/{i}/text: too similar to question {j + 1}")
                    break
            if q.get("cvAnchor"):
                anchors += 1
                if anchors > MAX_ANCHORS:
                    e.append(f"/questions/{i}/cvAnchor: at most {MAX_ANCHORS} questions may be anchored")
                if not quote_found(q["cvAnchor"]["quote"], self.source):
                    e.append(f"/questions/{i}/cvAnchor/quote: not found verbatim in <cv_profile>")
            if e:
                errs[i] = e
        return errs

    def validate(self, value: dict) -> Validation:
        errors = contracts.artefact_errors(SCHEMA, value, model_part=True)
        if errors:
            return Validation("schema", errors)
        qs = value["questions"]
        if len(qs) != len(self.slots):
            return Validation("semantic", [f"/questions: exactly {len(self.slots)} questions required, got {len(qs)}"])
        errs = self.question_errors(qs)
        plan_errs = []
        if len({q["questionType"] for q in qs}) < 2:
            plan_errs.append("/questions: use at least two different question types")
        if len(errs) * 2 > len(qs) or plan_errs:
            return Validation("semantic", plan_errs + [m for i in sorted(errs) for m in errs[i]])
        return Validation.ok()

    def final(self, value: dict) -> tuple[list[dict], set[int]]:
        return value["questions"], set(self.question_errors(value["questions"]))


_KEY_ORDER = ("questionId", "slot", "text", "competencyCodes", "questionType", "expectedAnswerRubric",
              "timeBudgetSeconds", "cvAnchor", "source")


def _ordered(q: dict) -> dict:
    return {k: q.get(k) for k in _KEY_ORDER}


def _fallback_run(llm, prompt, t0) -> dict:
    from ihr_worker_common.events import worker_version
    return {"provider": llm.provider_name, "model": llm.model_for("standard"), "promptId": prompt.prompt_id,
            "promptVersion": prompt.version, "promptSha256": prompt.sha256, "providerRequestId": None,
            "latencyMs": int((time.monotonic() - t0) * 1000), "inputTokens": None, "outputTokens": None,
            "providerAttempts": 2, "repairAttempted": True, "validationStatus": "FALLBACK", "workerVersion": worker_version()}
