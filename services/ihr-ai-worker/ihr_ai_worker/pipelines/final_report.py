"""FINAL_REPORT (AI Specification v1.1 §7.6). The report contains no numbers of its own.

Validation: schema; exact competency set; every evidenceRef points to an existing turn label (T#, mapped back
to turnId through the request's turns[]) and segment; no score-like numbers; language policy (A-22); no
integrity words in limitations. Code attaches computedScores unchanged.
"""
from __future__ import annotations

import copy
import json
import re
import time

from ihr_worker_common import contracts
from ihr_worker_common.errors import ProcessingError
from ihr_worker_common.log import get_logger
from ihr_worker_common.processor import HandlerResult, JobContext
from ihr_worker_common.storage import put_json, read_verified

from .. import debug_capture
from ..llm.base import TASK_PARAMS, LlmClient
from ..llm.structured import Validation, call_structured
from ..policy import language
from ..privacy import injection
from ..privacy.redaction import redact_transcript
from ..prompts import PromptRegistry
from ..tasks import TASKS, generated_bucket
from .common import capture_calls, run_meta, capture_failure

log = get_logger("ihr.final_report")

SCHEMA = "final_report.v1"
NUMBER_PATTERNS = (
    re.compile(r"\b[0-5](\.\d)?\s*(/|out of)\s*5\b", re.I),
    re.compile(r"\b\d{1,3}\s*%"),
    re.compile(r"\bscore[sd]? (of )?\d", re.I),
)
# §7.6 "integrity words": the A-22 integrity lexicon plus misconduct vocabulary (ASSUMPTIONS #29)
LIMITATION_INTEGRITY = re.compile(r"(?i)\b(suspicious|suspect(ed)?|misconduct|malpractice|violat\w*|"
                                  r"unfair means|impersonat\w*|collu\w*)\b")
LOW_CONFIDENCE = 0.55
INTERRUPTION_EVENTS = ("SESSION_PAUSED", "PAGE_RELOADED")
NOT_TECHNICAL = set(INTERRUPTION_EVENTS) | {"SESSION_RESUMED"}


def has_score_number(text: str) -> bool:
    return any(p.search(text) for p in NUMBER_PATTERNS)


class FinalReportHandler:
    task = "FINAL_REPORT"

    def __init__(self, llm: LlmClient, prompts: PromptRegistry, *, debug_capture_enabled: bool = True) -> None:
        self.spec = TASKS["FINAL_REPORT"]
        self.request_topic = self.spec.request_topic
        self.output_schema_version = self.spec.output_schema_version
        self.llm = llm
        self.prompt = prompts.get(self.spec.prompt_id)
        self.capture = debug_capture_enabled

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
        names = ((p.get("redactionHints") or {}).get("fullName"),)
        turns = sorted(p["turns"], key=lambda t: t["turnNumber"])
        segments: dict[str, list[str]] = {}       # "T#" -> redacted segment texts
        blocks, low_conf = [], []
        for t in turns:
            label = f"T{t['turnNumber']}"
            texts, tr = [], None
            if t.get("transcript"):
                tr = json.loads(read_verified(ctx.storage, t["transcript"]))
                texts = [redact_transcript(s["text"], names=names).text
                         for s in sorted(tr["segments"], key=lambda s: s["index"])]
                if tr["avgConfidence"] < LOW_CONFIDENCE or {"LOW_CONFIDENCE", "POSSIBLE_HALLUCINATION"} & set(tr["qualityFlags"]):
                    low_conf.append(label)
            segments[label] = texts
            assessment = json.loads(read_verified(ctx.storage, t["assessment"])) if t.get("assessment") else None
            blocks.append(_turn_block(label, t, texts, assessment, tr))

        codes = [c["code"] for c in p["competencies"]]
        result = call_structured(self.llm, task=self.task, params=TASK_PARAMS[self.task],
                                 messages=self._messages(p, turns, blocks, low_conf), deadline=ctx.deadline,
                                 validate=lambda v: validate_model_output(v, codes, segments))
        report = copy.deepcopy(result.value)
        order = {c: i for i, c in enumerate(codes)}
        report["competencyNarratives"].sort(key=lambda n: order[n["competencyCode"]])
        report["computedScores"] = copy.deepcopy(p["computedScores"])      # attached unchanged (§7.6)
        contracts.validate_artefact(SCHEMA, report)
        ref = put_json(ctx.storage, generated_bucket(), self.spec.output_key(p), report)
        debug_capture.capture(ctx.storage, enabled=self.capture, job_id=ctx.job_id, task=self.task,
                              prompt_id=self.prompt.prompt_id, prompt_version=self.prompt.version,
                              calls=capture_calls(result))
        log.info("final_report_done", job_id=ctx.job_id, turns=len(turns), repaired=result.repaired)
        return HandlerResult(output=ref, run=run_meta(self.llm, self.prompt, result, TASK_PARAMS[self.task].model_slot, t0))

    def _messages(self, p: dict, turns: list[dict], blocks: list[str], low_conf: list[str]) -> list[dict]:
        scores = {c["code"]: c for c in p["computedScores"]["competencies"]}
        lines = []
        for c in p["competencies"]:
            s = scores.get(c["code"], {}).get("score")
            lines.append(f"{c['code']} | {c['name']} | weight {c['weight']:g} | "
                         f"{'mandatory' if c['mandatory'] else 'optional'} | "
                         f"{'not assessed' if s is None else f'computed score {s:g}'}")
        proc = p.get("proctoringSummary") or {}
        skipped = sum(1 for t in turns if t["transcriptStatus"] == "NOT_REQUESTED")
        interruptions = sum(proc.get(k, 0) for k in INTERRUPTION_EVENTS)
        tech = ", ".join(f"{k}: {v}" for k, v in sorted(proc.items()) if k not in NOT_TECHNICAL and v) or "none"
        cs = p["computedScores"]
        user = self.prompt.user(
            competencyLines=injection.escape_tags("\n" + "\n".join(lines) + "\n"),
            completionState=cs["completionState"], evidenceLevel=cs["evidenceLevel"],
            skippedCount=skipped, lowConfidenceTurns=", ".join(low_conf) or "none", interruptions=interruptions,
            technicalEvents=tech, turnBlocks="\n\n".join(blocks))
        return [{"role": "system", "content": self.prompt.system()}, {"role": "user", "content": user}]


def _turn_block(label: str, t: dict, texts: list[str], assessment: dict | None, tr: dict | None) -> str:
    esc = injection.escape_tags
    out = [f"{label} ({t['kind']}) question: {esc(t['questionText'])}", "transcript:"]
    if texts:
        out += [f"[{i}] {esc(x)}" for i, x in enumerate(texts)]
    else:
        out.append(f"(no transcript: {t['transcriptStatus']})")
    out.append("assessment:")
    if assessment and assessment["scores"]:
        for s in assessment["scores"]:
            score = "not assessed (" + str(s["notAssessedReason"]) + ")" if s["score"] is None else f"{s['score']}"
            quotes = "; ".join(f"[{e['segmentIndex']}] \"{esc(e['quote'])}\"" for e in s["evidence"])
            out.append(f"- {s['competencyCode']}: {score}; {esc(s['rationale'])}" + (f" Evidence: {quotes}" if quotes else ""))
    else:
        out.append("(none)" if not assessment else "(not scored)")
    return "\n".join(out)


def _refs(path: str, refs: list[dict], segments: dict[str, list[str]]) -> list[str]:
    errs = []
    for j, r in enumerate(refs):
        if r["turn"] not in segments:
            errs.append(f"{path}/evidenceRefs/{j}: unknown turn {r['turn']}")
        elif not 0 <= r["segmentIndex"] < len(segments[r["turn"]]):
            errs.append(f"{path}/evidenceRefs/{j}: {r['turn']} has no segment {r['segmentIndex']}")
    return errs


def text_fields(v: dict) -> dict[str, str]:
    f = {"/summary": v["summary"]}
    f.update({f"/competencyNarratives/{i}/narrative": n["narrative"] for i, n in enumerate(v["competencyNarratives"])})
    f.update({f"/strengths/{i}/text": s["text"] for i, s in enumerate(v["strengths"])})
    f.update({f"/gaps/{i}/text": s["text"] for i, s in enumerate(v["gaps"])})
    f.update({f"/reviewerFocusAreas/{i}": s for i, s in enumerate(v["reviewerFocusAreas"])})
    f.update({f"/limitations/{i}": s for i, s in enumerate(v["limitations"])})
    return f


def validate_model_output(v: dict, codes: list[str], segments: dict[str, list[str]]) -> Validation:
    errors = contracts.artefact_errors(SCHEMA, v, model_part=True)
    if errors:
        return Validation("schema", errors)
    sem = []
    got = [n["competencyCode"] for n in v["competencyNarratives"]]
    if sorted(got) != sorted(codes):
        sem.append(f"/competencyNarratives: exactly one per competency {codes} required")
    for i, n in enumerate(v["competencyNarratives"]):
        sem += _refs(f"/competencyNarratives/{i}", n["evidenceRefs"], segments)
    for key in ("strengths", "gaps"):
        for i, s in enumerate(v[key]):
            sem += _refs(f"/{key}/{i}", s["evidenceRefs"], segments)
    if sem:
        return Validation("semantic", sem)
    fields = text_fields(v)
    pol = [f"{path}: contains a score-like number (the report must not contain numbers of its own)"
           for path, text in fields.items() if has_score_number(text)]
    pol += language.check_fields(fields)
    pol += [f"/limitations/{i}: integrity wording is not allowed in limitations"
            for i, s in enumerate(v["limitations"]) if LIMITATION_INTEGRITY.search(s) or language.INTEGRITY.search(s)]
    return Validation("policy", pol) if pol else Validation.ok()
