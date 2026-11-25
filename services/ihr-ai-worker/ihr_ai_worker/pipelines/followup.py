"""FOLLOWUP (AI Specification v1.1 §7.4), realtime deployment.

Deadline 12 s, at most 1 provider retry (A-08 realtime), no repair call. Invalid JSON -> .failed
OUTPUT_SCHEMA_INVALID (core moves on). Policy or consistency failure -> NEXT_QUESTION / NOT_USEFUL
(not a failure), counted in ihr_ai_followup_policy_rejected_total. Output is inline in .completed (A.5).
"""
from __future__ import annotations

import time
from datetime import timedelta

from ihr_worker_common import contracts
from ihr_worker_common.errors import ProcessingError
from ihr_worker_common.events import utc_now
from ihr_worker_common.log import get_logger
from ihr_worker_common.metrics import counter
from ihr_worker_common.processor import HandlerResult, JobContext

from .. import debug_capture
from ..llm.base import TASK_PARAMS, LlmClient
from ..llm.structured import Validation, call_structured
from ..policy import questions as qpolicy
from ..privacy import injection
from ..prompts import PromptRegistry
from ..tasks import TASKS
from . import metrics
from .common import capture_calls, run_meta, capture_failure
from .interview_plan import SIMILARITY_LIMIT, similarity
from .transcripts import load_segments, name_tokens, segment_lines

log = get_logger("ihr.followup")

SCHEMA = "followup_decision.v1"
DEADLINE_S = 12
ASK_REASONS = {"CLARIFY_AMBIGUITY", "PROBE_DEPTH", "ANSWER_INCOMPLETE"}
NEXT = {"decision": "NEXT_QUESTION", "reasonCode": "NOT_USEFUL", "followUp": None}


def check_decision(d: dict, question: dict, names: tuple[str, ...]) -> list[str]:
    """Consistency (§7.4) and question policy (A-21, follow-up limits). Empty list = accept."""
    problems = []
    ask = d["decision"] == "ASK_FOLLOW_UP"
    fu = d.get("followUp")
    if ask != (fu is not None) or ask != (d["reasonCode"] in ASK_REASONS):
        problems.append("INCONSISTENT")
    if fu is not None and ask:
        problems += qpolicy.violations(fu["text"], followup=True, name_tokens=names,
                                       competency_codes=fu["competencyCodes"],
                                       allowed_codes=set(question["competencyCodes"]))
        if similarity(fu["text"], question["text"]) >= SIMILARITY_LIMIT:
            problems.append("REPEATS_QUESTION")
    return problems


class FollowupHandler:
    task = "FOLLOWUP"

    def __init__(self, llm: LlmClient, prompts: PromptRegistry, *, debug_capture_enabled: bool = True) -> None:
        self.spec = TASKS["FOLLOWUP"]
        self.request_topic = self.spec.request_topic
        self.output_schema_version = self.spec.output_schema_version
        self.llm = llm
        self.prompt = prompts.get(self.spec.prompt_id)
        self.capture = debug_capture_enabled

    def output_location(self, payload: dict) -> None:
        return None   # inline output (A.5)

    def handle(self, ctx: JobContext) -> HandlerResult:
        try:
            return self._handle(ctx)
        except ProcessingError as e:
            capture_failure(self, ctx, e)
            raise

    def _handle(self, ctx: JobContext) -> HandlerResult:
        t0 = time.monotonic()
        p = ctx.payload
        q = p["question"]
        transcript, texts = load_segments(ctx.storage, p["transcript"], p.get("redactionHints"))
        deadline = min(ctx.deadline, utc_now() + timedelta(seconds=DEADLINE_S))
        if not texts or transcript.get("speechDetected") is False:
            # Nothing to follow up on: no model call needed (ASSUMPTIONS #28)
            return HandlerResult(output=dict(NEXT), run=_no_call_run(self.llm, self.prompt, t0))

        user = self.prompt.user(codes=",".join(q["competencyCodes"]), questionText=injection.escape_tags(q["text"]),
                                keyPoints=injection.escape_tags("; ".join(q["expectedAnswerRubric"]["keyPoints"])),
                                segmentLines=segment_lines(texts))
        messages = [{"role": "system", "content": self.prompt.system()}, {"role": "user", "content": user}]
        result = call_structured(
            self.llm, task=self.task, params=TASK_PARAMS[self.task], messages=messages, deadline=deadline,
            validate=lambda v: Validation("schema", e) if (e := contracts.artefact_errors(SCHEMA, v, model_part=True))
            else Validation.ok(), allow_repair=False)

        decision = dict(result.value)
        problems = check_decision(decision, q, name_tokens(p.get("redactionHints")))
        if problems:
            for rule in problems:
                metrics.policy_rejection(self.task, rule)
            log.info("followup_rejected", job_id=ctx.job_id, rules=problems)
            decision = dict(NEXT)
        elif decision["decision"] == "NEXT_QUESTION":
            decision["followUp"] = None
        contracts.validate_artefact(SCHEMA, decision)
        debug_capture.capture(ctx.storage, enabled=self.capture, job_id=ctx.job_id, task=self.task,
                              prompt_id=self.prompt.prompt_id, prompt_version=self.prompt.version,
                              calls=capture_calls(result))
        return HandlerResult(output=decision, run=run_meta(self.llm, self.prompt, result,
                                                           TASK_PARAMS[self.task].model_slot, t0))


def _no_call_run(llm, prompt, t0) -> dict:
    from ihr_worker_common.events import worker_version
    return {"provider": llm.provider_name, "model": llm.model_for("realtime"), "promptId": prompt.prompt_id,
            "promptVersion": prompt.version, "promptSha256": prompt.sha256, "providerRequestId": None,
            "latencyMs": int((time.monotonic() - t0) * 1000), "inputTokens": None, "outputTokens": None,
            "providerAttempts": 0, "repairAttempted": False, "validationStatus": "NOT_CALLED",
            "workerVersion": worker_version()}
