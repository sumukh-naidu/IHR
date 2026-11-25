"""INTERVIEW_PLAN (§7.3, A-25, §8.2)."""
import copy
import json
from pathlib import Path

import pytest

from ihr_worker_common import contracts
from ihr_worker_common.processor import Processor
from ihr_worker_common.storage import InMemoryStorage, put_json
from ihr_ai_worker.llm.mock import MockLlmClient
from ihr_ai_worker.pipelines.interview_plan import InterviewPlanHandler
from ihr_ai_worker.pipelines.plan_allocation import allocate, generic_rubric
from ihr_ai_worker.prompts import load_registry

ROOT = Path(__file__).resolve().parents[3]
FIX = ROOT / "contracts/fixtures/ai"
REQ = json.loads((FIX / "interview_plan/request.json").read_text())
KIMI = json.loads((FIX / "interview_plan/kimi_response.json").read_text())
PROFILE = json.loads((FIX / "cv_parse/expected_output.json").read_text())
TEXT = (FIX / "cv_parse/cv_text.txt").read_text()
COMPS = REQ["payload"]["competencies"]


def comp(code, weight, mandatory):
    base = copy.deepcopy(COMPS[0])
    return {**base, "code": code, "weight": weight, "mandatory": mandatory}


def test_allocation_matches_fixture():
    assert allocate(COMPS, 6) == ["PRACTICAL_EXPERIENCE", "TECHNICAL_SKILL", "PROBLEM_SOLVING", "TECHNICAL_SKILL",
                                  "COMMUNICATION", "ROLE_FIT"]


@pytest.mark.parametrize("n", [4, 5, 6, 7, 8, 9, 10])
def test_allocation_invariants(n):
    slots = allocate(COMPS, n)
    assert len(slots) == n
    for code in ("TECHNICAL_SKILL", "PROBLEM_SOLVING", "PRACTICAL_EXPERIENCE"):
        assert code in slots                                   # every mandatory competency at least once
    assert max(slots.count(c) for c in set(slots)) <= 3
    assert slots[0] == "PRACTICAL_EXPERIENCE"
    repeats = sum(1 for a, b in zip(slots, slots[1:]) if a == b)
    assert repeats == 0 or n >= 10


def test_allocation_without_practical_experience_and_caps():
    comps = [comp("A", 70, True), comp("B", 20, False), comp("C", 10, False)]
    slots = allocate(comps, 7)
    assert slots.count("A") == 3 and len(slots) == 7 or slots.count("A") <= 3
    assert slots[0] in ("A", "B", "C")


def test_generic_rubric_from_anchors():
    r = generic_rubric(COMPS[0]["anchors"])
    assert 3 <= len(r["keyPoints"]) <= 6 and all(len(k) <= 200 for k in r["keyPoints"])
    assert r["strongSignals"] == [COMPS[0]["anchors"]["5"]]


def run(responses=None, with_cv=True, mutate=None):
    storage = InMemoryStorage()
    env = copy.deepcopy(REQ)
    p = env["payload"]
    if with_cv:
        p["cvProfile"] = put_json(storage, p["cvProfile"]["bucket"], p["cvProfile"]["key"], PROFILE)
        tkey = p["cvProfile"]["key"].replace("/profile/", "/text/").removesuffix(".json") + ".txt"
        storage.put_bytes(p["cvProfile"]["bucket"], tkey, TEXT.encode(), "text/plain")
    else:
        p["cvProfile"] = None
    if mutate:
        mutate(p)
    llm = MockLlmClient(responses={"INTERVIEW_PLAN": [json.dumps(r) if isinstance(r, dict) else r for r in responses]}
                        if responses else None)
    proc = Processor(InterviewPlanHandler(llm, load_registry(ROOT / "docs/prompts/active.yaml")), storage,
                     "ihr-ai-worker", "t_plan")
    [out] = proc.process(json.dumps(env).encode(), env["entityId"], {})
    proc.shutdown()
    event = json.loads(out.value)
    contracts.validate_payload(out.topic, event["payload"])
    plan = None
    if out.topic.endswith(".completed"):
        ref = event["payload"]["output"]
        plan = json.loads(storage.get_bytes(ref["bucket"], ref["key"]))
        contracts.validate_artefact("interview_plan.v1", plan)
    return out, event["payload"], plan, llm


def test_plan_completes_like_fixture():
    out, payload, plan, llm = run()
    expected = json.loads((FIX / "interview_plan/expected_output.json").read_text())
    assert out.topic == "ihr.interview.plan.completed"
    assert plan["coverage"] == expected["coverage"] and plan["warnings"] == []
    assert [q["questionId"] for q in plan["questions"]] == [f"q{i}" for i in range(1, 7)]
    assert {q["source"] for q in plan["questions"]} == {"AI_GENERATED"}
    assert payload["questionCount"] == 6 and payload["fallbackUsedCount"] == 0
    user = llm.calls[0]["messages"][1]["content"]
    assert "1. PRACTICAL_EXPERIENCE" in user and "asha" not in user.lower()


def one_bad(i, **change):
    resp = copy.deepcopy(KIMI)
    resp["questions"][i].update(change)
    return resp


def test_policy_violation_replaced_by_fallback():
    bad = one_bad(4, text="Are you married, and how would you explain your work to your family?")
    out, payload, plan, llm = run(responses=[bad])
    q5 = plan["questions"][4]
    assert len(llm.calls) == 1 and q5["source"] == "FALLBACK_BANK"
    assert q5["text"] == COMPS[3]["fallbackQuestions"][0] and q5["competencyCodes"] == ["COMMUNICATION"]
    assert payload["fallbackUsedCount"] == 1 and plan["warnings"] == ["QUESTIONS_REPLACED_BY_FALLBACK"]


def test_near_duplicate_replaced():
    bad = one_bad(3, text=KIMI["questions"][1]["text"] + " Please be specific.")
    _, _, plan, _ = run(responses=[bad])
    assert sum(q["source"] == "FALLBACK_BANK" for q in plan["questions"]) >= 1


def test_unverified_anchor_replaced():
    bad = one_bad(0, cvAnchor={"claim": "Led a platform team", "quote": "Led a platform team of twenty engineers"})
    _, _, plan, _ = run(responses=[bad])
    assert plan["questions"][0]["source"] == "FALLBACK_BANK" and plan["questions"][0]["cvAnchor"] is None


def test_wrong_slot_competency_replaced():
    bad = one_bad(2, competencyCodes=["COMMUNICATION"])
    _, _, plan, _ = run(responses=[bad])
    assert plan["questions"][2]["source"] == "FALLBACK_BANK"
    assert plan["questions"][2]["competencyCodes"] == ["PROBLEM_SOLVING"]


def test_more_than_half_invalid_repairs_then_full_fallback():
    bad = copy.deepcopy(KIMI)
    for q in bad["questions"]:
        q["text"] = "What is your current salary and are you married?"
    out, payload, plan, llm = run(responses=[bad, bad])
    assert out.topic.endswith(".completed") and len(llm.calls) == 2
    assert payload["fallbackUsedCount"] == 6 and {q["source"] for q in plan["questions"]} == {"FALLBACK_BANK"}
    assert plan["questions"][1]["text"] != plan["questions"][3]["text"]          # two TECHNICAL_SKILL slots


def test_unparseable_output_still_completes_from_bank():
    out, payload, plan, _ = run(responses=["not json", "still not json"])
    assert out.topic.endswith(".completed") and payload["fallbackUsedCount"] == 6


def test_repair_succeeds():
    bad = copy.deepcopy(KIMI)
    for q in bad["questions"][:4]:
        q["text"] = "Tell me your age and where are you from originally?"
    out, payload, plan, llm = run(responses=[bad, KIMI])
    assert payload["run"]["validationStatus"] == "REPAIRED" and payload["fallbackUsedCount"] == 0


def test_no_cv_profile_warning():
    _, _, plan, llm = run(with_cv=False, responses=[one_bad(0, cvAnchor=None)])
    assert "CV_CONTEXT_MISSING" in plan["warnings"]
    assert "<cv_profile>none</cv_profile>" in llm.calls[0]["messages"][1]["content"]


def test_fallback_exhausted_fails():
    def mutate(p):           # N=8 gives TECHNICAL_SKILL 3 slots; it keeps only 2 fallback questions
        p["mainQuestionCount"] = 8
        for c in p["competencies"]:
            if c["code"] == "TECHNICAL_SKILL":
                c["fallbackQuestions"] = c["fallbackQuestions"][:2]
    bad = copy.deepcopy(KIMI)
    for q in bad["questions"]:
        q["text"] = "What is your current salary and are you married?"
    out, payload, _, _ = run(responses=[bad, bad], mutate=mutate)
    assert payload["errorCode"] == "OUTPUT_SEMANTIC_INVALID"
