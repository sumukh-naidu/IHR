"""CV_MATCH (§7.2) through the Processor with MockLlmClient."""
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ihr_worker_common import contracts
from ihr_worker_common.processor import Processor
from ihr_worker_common.storage import InMemoryStorage, put_json
from ihr_ai_worker.enrich.experience import experience_status
from ihr_ai_worker.llm.mock import MockLlmClient
from ihr_ai_worker.pipelines.cv_match import CvMatchHandler
from ihr_ai_worker.prompts import load_registry

ROOT = Path(__file__).resolve().parents[3]
FIX = ROOT / "contracts/fixtures/ai"
KIMI = json.loads((FIX / "cv_match/kimi_response.json").read_text())
PROFILE = json.loads((FIX / "cv_parse/expected_output.json").read_text())
TEXT = (FIX / "cv_parse/cv_text.txt").read_text()
CLOCK = lambda: datetime(2026, 12, 2, tzinfo=timezone.utc)  # noqa: E731


def run(responses=None, profile=None, mutate=None):
    storage = InMemoryStorage()
    env = json.loads((FIX / "cv_match/request.json").read_text())
    p = env["payload"]
    p["cvProfile"] = put_json(storage, p["cvProfile"]["bucket"], p["cvProfile"]["key"], profile or PROFILE)
    p["cvText"] = storage.put_bytes(p["cvText"]["bucket"], p["cvText"]["key"], TEXT.encode(), "text/plain")
    if mutate:
        mutate(p)
    llm = MockLlmClient(responses={"CV_MATCH": [json.dumps(r) if isinstance(r, dict) else r for r in responses]}
                        if responses else None)
    proc = Processor(CvMatchHandler(llm, load_registry(ROOT / "docs/prompts/active.yaml"), clock=CLOCK),
                     storage, "ihr-ai-worker", "t_cvmatch")
    [out] = proc.process(json.dumps(env).encode(), env["entityId"], {})
    proc.shutdown()
    event = json.loads(out.value)
    contracts.validate_payload(out.topic, event["payload"])
    fit = None
    if out.topic.endswith(".completed"):
        ref = event["payload"]["output"]
        fit = json.loads(storage.get_bytes(ref["bucket"], ref["key"]))
        contracts.validate_artefact("cv_match.v1", fit)
    return out, event["payload"], fit, llm


def test_cv_match_completes_with_deterministic_experience():
    out, payload, fit, llm = run()
    assert out.topic == "ihr.cv.match.completed"
    r3 = next(c for c in fit["criteria"] if c["requirementCode"] == "R3")
    assert r3["statusSource"] == "DETERMINISTIC_EXPERIENCE" and r3["status"] == "MATCHED"
    assert r3["rationale"] == "Calculated from stated employment dates: 102 months against 36 required."
    assert {c["statusSource"] for c in fit["criteria"] if c["requirementCode"] != "R3"} == {"MODEL"}
    sent = llm.calls[0]["messages"][1]["content"]
    assert "asha.verma@example.com" not in sent and '"identity"' not in sent
    assert "- code: R1 | type: MUST_HAVE | category: SKILL | text: Strong Java backend development" in sent


def short(n):
    prof = copy.deepcopy(PROFILE)
    prof["derived"]["totalExperienceMonths"] = n
    return prof


@pytest.mark.parametrize("months,status", [(36, "MATCHED"), (27, "PARTIAL"), (26, "MISSING"), (None, "UNKNOWN")])
def test_experience_override(months, status):
    _, _, fit, _ = run(profile=short(months))
    r3 = next(c for c in fit["criteria"] if c["requirementCode"] == "R3")
    assert r3["status"] == status
    if status in ("MISSING", "UNKNOWN"):
        assert r3["evidence"] == []


def test_experience_status_never_penalises_over_max():
    assert experience_status(500, 36) == "MATCHED"


def test_missing_code_repairs_then_fails():
    bad = copy.deepcopy(KIMI)
    bad["criteria"] = bad["criteria"][:-1]
    out, payload, _, llm = run(responses=[bad, bad])
    assert payload["errorCode"] == "OUTPUT_SEMANTIC_INVALID" and len(llm.calls) == 2
    assert "missing requirementCode R4" in llm.calls[1]["messages"][-1]["content"]


def test_unknown_code_fixture_rejected():
    bad = (FIX / "cv_match/invalid_unknown_competency.json").read_text()
    out, payload, _, _ = run(responses=[bad, bad])
    assert payload["errorCode"] in ("OUTPUT_SEMANTIC_INVALID", "OUTPUT_SCHEMA_INVALID")


def test_extra_unverified_quote_dropped_without_repair():
    resp = copy.deepcopy(KIMI)
    resp["criteria"][0]["evidence"].append({"quote": "Led a team of forty Java developers"})
    out, payload, fit, llm = run(responses=[resp])
    assert len(llm.calls) == 1 and payload["run"]["validationStatus"] == "VALID"
    assert all("forty" not in e["quote"] for e in fit["criteria"][0]["evidence"]) and fit["criteria"][0]["evidence"]


def test_unverified_quote_repaired():
    bad = copy.deepcopy(KIMI)
    bad["criteria"][0]["evidence"] = [{"quote": "Led a team of forty Java developers"}]
    out, payload, _, llm = run(responses=[bad, KIMI])
    assert out.topic.endswith(".completed") and payload["run"]["validationStatus"] == "REPAIRED"


def test_missing_with_quote_rejected():
    bad = copy.deepcopy(KIMI)
    r4 = next(c for c in bad["criteria"] if c["requirementCode"] == "R4")
    r4["evidence"] = [{"quote": "Developed REST APIs in Java 11"}]
    _, payload, _, _ = run(responses=[bad, bad])
    assert payload["errorCode"] == "OUTPUT_SEMANTIC_INVALID"


def test_language_policy_violation():
    bad = copy.deepcopy(KIMI)
    bad["summary"] = "Strong hire: the best candidate for this role."
    out, payload, _, llm = run(responses=[bad, bad])
    assert payload["errorCode"] == "OUTPUT_POLICY_VIOLATION"
    assert "language policy HIRING_RECOMMENDATION" in llm.calls[1]["messages"][-1]["content"]


def test_bad_clarification_questions_dropped():
    resp = copy.deepcopy(KIMI)
    resp["clarificationQuestions"] = ["Which cloud platforms have you deployed services to?",
                                      "Are you married or planning to have children soon?"]
    _, _, fit, _ = run(responses=[resp])
    assert fit["clarificationQuestions"] == ["Which cloud platforms have you deployed services to?"]


def test_profile_warnings_carried():
    prof = copy.deepcopy(PROFILE)
    prof["extraction"]["warnings"] = ["PROMPT_INJECTION_SUSPECTED", "TEXT_TRUNCATED"]
    _, _, fit, _ = run(profile=prof)
    assert fit["warnings"] == ["CV_TEXT_TRUNCATED", "PROMPT_INJECTION_SUSPECTED"]


def test_criteria_in_request_order():
    resp = copy.deepcopy(KIMI)
    resp["criteria"] = list(reversed(resp["criteria"]))
    _, _, fit, _ = run(responses=[resp])
    assert [c["requirementCode"] for c in fit["criteria"]] == ["R1", "R2", "R3", "R4"]
