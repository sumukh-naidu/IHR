"""FINAL_REPORT (§7.6)."""
import copy
import json
from pathlib import Path

import pytest

from ihr_worker_common import contracts
from ihr_worker_common.processor import Processor
from ihr_worker_common.storage import InMemoryStorage, put_json
from ihr_ai_worker.llm.mock import MockLlmClient
from ihr_ai_worker.pipelines.final_report import FinalReportHandler, has_score_number
from ihr_ai_worker.prompts import load_registry

ROOT = Path(__file__).resolve().parents[3]
FIX = ROOT / "contracts/fixtures/ai"
REQ = json.loads((FIX / "final_report/request.json").read_text())
KIMI = json.loads((FIX / "final_report/kimi_response.json").read_text())
TRANSCRIPT = json.loads((ROOT / "contracts/fixtures/speech/transcription/expected_output.json").read_text())
ASSESSMENT = json.loads((FIX / "answer_assessment/expected_output.json").read_text())
REG = load_registry(ROOT / "docs/prompts/active.yaml")


def run(responses=None, transcript=None, mutate=None):
    storage = InMemoryStorage()
    env = copy.deepcopy(REQ)
    p = env["payload"]
    for t in p["turns"]:
        t["transcript"] = put_json(storage, t["transcript"]["bucket"], t["transcript"]["key"], transcript or TRANSCRIPT)
        t["assessment"] = put_json(storage, t["assessment"]["bucket"], t["assessment"]["key"], ASSESSMENT)
    if mutate:
        mutate(p)
    llm = MockLlmClient(responses={"FINAL_REPORT": [json.dumps(r) if isinstance(r, dict) else r for r in responses]}
                        if responses else None)
    proc = Processor(FinalReportHandler(llm, REG), storage, "ihr-ai-worker", "t_report")
    [out] = proc.process(json.dumps(env).encode(), env["entityId"], {})
    proc.shutdown()
    ev = json.loads(out.value)
    contracts.validate_payload(out.topic, ev["payload"])
    report = None
    if out.topic.endswith(".completed"):
        ref = ev["payload"]["output"]
        report = json.loads(storage.get_bytes(ref["bucket"], ref["key"]))
        contracts.validate_artefact("final_report.v1", report)
    return out, ev["payload"], report, llm


def test_report_completes_with_scores_attached_unchanged():
    out, p, r, llm = run()
    assert out.topic == "ihr.interview.final-report.completed"
    assert r["computedScores"] == REQ["payload"]["computedScores"]
    assert [n["competencyCode"] for n in r["competencyNarratives"]] == [c["code"] for c in REQ["payload"]["competencies"]]
    user = llm.calls[0]["messages"][1]["content"]
    assert "T1 (MAIN) question:" in user and "[1] I designed the topic layout" in user
    assert "ROLE_FIT | Role fit | weight 10 | optional | not assessed" in user
    assert "technical events: TAB_HIDDEN: 1" in user and "Asha" not in user


@pytest.mark.parametrize("text,bad", [
    ("Scored 4/5 on technical depth.", True), ("Rated 3.5 out of 5.", True), ("Covered 80 % of key points.", True),
    ("A score of 4 overall.", True), ("Scores 3 across answers.", True),
    ("Described 3 Kafka partitions and a 40-minute batch (T1 [3]).", False), ("Moved to Java 11 in 2022.", False),
])
def test_no_numbers_regex(text, bad):
    assert has_score_number(text) is bad


def bad(**kw):
    r = copy.deepcopy(KIMI)
    for path, val in kw.items():
        obj = r
        *head, last = path.split(".")
        for k in head:
            obj = obj[int(k)] if k.isdigit() else obj[k]
        obj[int(last) if last.isdigit() else last] = val
    return r


def test_number_in_summary_repaired_then_fails():
    b = bad(summary=KIMI["summary"] + " Overall the candidate scored 68 % on the interview.")
    out, p, _, llm = run(responses=[b, b])
    assert p["errorCode"] == "OUTPUT_POLICY_VIOLATION" and len(llm.calls) == 2
    assert "score-like number" in llm.calls[1]["messages"][-1]["content"]


def test_unknown_turn_ref_rejected():
    b = bad(**{"strengths.0.evidenceRefs": [{"turn": "T9", "segmentIndex": 0}]})
    _, p, _, llm = run(responses=[b, KIMI])
    assert p["run"]["validationStatus"] == "REPAIRED"
    assert "unknown turn T9" in llm.calls[1]["messages"][-1]["content"]


def test_missing_segment_ref_rejected():
    b = bad(**{"competencyNarratives.0.evidenceRefs": [{"turn": "T1", "segmentIndex": 42}]})
    _, p, _, _ = run(responses=[b, b])
    assert p["errorCode"] == "OUTPUT_SEMANTIC_INVALID"


def test_competency_set_exact():
    b = copy.deepcopy(KIMI)
    b["competencyNarratives"] = b["competencyNarratives"][:-1]
    _, p, _, _ = run(responses=[b, b])
    assert p["errorCode"] == "OUTPUT_SEMANTIC_INVALID"


@pytest.mark.parametrize("lim", ["The tab was hidden once, which is suspicious.", "Possible cheating during T1.",
                                 "The candidate may have used external help."])
def test_limitations_integrity_words(lim):
    _, p, _, _ = run(responses=[bad(limitations=[lim])] * 2)
    assert p["errorCode"] == "OUTPUT_POLICY_VIOLATION"


def test_neutral_technical_limitation_ok():
    _, p, r, _ = run(responses=[bad(limitations=["The browser tab was hidden once during the interview."])])
    assert r["limitations"] == ["The browser tab was hidden once during the interview."]


def test_hiring_language_rejected():
    _, p, _, _ = run(responses=[bad(summary=KIMI["summary"] + " We recommend to shortlist this candidate.")] * 2)
    assert p["errorCode"] == "OUTPUT_POLICY_VIOLATION"


def test_low_confidence_turn_listed_in_facts():
    t = {**TRANSCRIPT, "avgConfidence": 0.4, "qualityFlags": ["LOW_CONFIDENCE"]}
    _, _, _, llm = run(transcript=t)
    assert "answers with low transcript confidence: T1" in llm.calls[0]["messages"][1]["content"]


def test_turn_without_transcript():
    def mutate(p):
        p["turns"][0].update(transcript=None, assessment=None, transcriptStatus="NOT_REQUESTED", assessmentStatus="CANCELLED")
    b = copy.deepcopy(KIMI)
    for n in b["competencyNarratives"]:
        n["evidenceRefs"] = []
    for k in ("strengths", "gaps"):
        for s in b[k]:
            s["evidenceRefs"] = []
    _, p, r, llm = run(responses=[b], mutate=mutate)
    user = llm.calls[0]["messages"][1]["content"]
    assert "skipped questions: 1" in user and "(no transcript: NOT_REQUESTED)" in user and r is not None


def test_capture_failure_writes_validation_errors():
    from ihr_ai_worker.pipelines.final_report import FinalReportHandler as H
    env = copy.deepcopy(REQ)
    storage = InMemoryStorage()
    for t in env["payload"]["turns"]:
        put_json(storage, t["transcript"]["bucket"], t["transcript"]["key"], TRANSCRIPT)
        put_json(storage, t["assessment"]["bucket"], t["assessment"]["key"], ASSESSMENT)
        t["transcript"] = put_json(storage, t["transcript"]["bucket"], t["transcript"]["key"], TRANSCRIPT)
        t["assessment"] = put_json(storage, t["assessment"]["bucket"], t["assessment"]["key"], ASSESSMENT)
    b = json.dumps(bad(limitations=["The tab was hidden, which is suspicious."]))
    proc = Processor(H(MockLlmClient(responses={"FINAL_REPORT": [b, b]}), REG), storage, "w", "t")
    [out] = proc.process(json.dumps(env).encode(), env["entityId"], {})
    proc.shutdown()
    resp = [json.loads(v) for (_, k), (v, _) in storage.objects.items() if k.endswith("response.json")]
    assert out.topic.endswith(".failed") and resp and resp[0]["outcome"] == "OUTPUT_POLICY_VIOLATION"
    assert any("integrity wording" in e for e in resp[0]["validationErrors"][-1])
