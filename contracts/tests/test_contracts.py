"""contracts-check (A.9): schemas are valid, closed, regenerate cleanly, and every fixture validates."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ihr_worker_common import contracts

ROOT = Path(__file__).resolve().parents[2]
C = ROOT / "contracts"

AI_TASKS = {
    "cv_parse": "cv_profile.v1",
    "cv_match": "cv_match.v1",
    "interview_plan": "interview_plan.v1",
    "followup_decision": "followup_decision.v1",
    "answer_assessment": "answer_assessment.v1",
    "final_report": "final_report.v1",
}
NEGATIVE = ["invalid_unknown_competency.json", "invalid_quote.json", "policy_violation.json"]


def _schemas():
    return [p for p in C.glob("*/*.json") if "$schema" in json.loads(p.read_text())]


@pytest.mark.parametrize("path", _schemas(), ids=lambda p: p.name)
def test_schema_is_valid_draft_2020_12(path):
    Draft202012Validator.check_schema(json.loads(path.read_text()))


def _objects(node):
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            yield node
        for v in node.values():
            yield from _objects(v)
    elif isinstance(node, list):
        for v in node:
            yield from _objects(v)


@pytest.mark.parametrize("path", _schemas(), ids=lambda p: p.name)
def test_every_object_is_closed(path):
    for o in _objects(json.loads(path.read_text())):
        assert o.get("additionalProperties") is False, path.name


def test_generated_files_are_up_to_date(tmp_path):
    before = {p: p.read_bytes() for p in C.glob("*/*.json")}
    subprocess.run([sys.executable, str(ROOT / "tools" / "gen_contracts.py")], check=True, capture_output=True)
    after = {p: p.read_bytes() for p in C.glob("*/*.json")}
    assert before == after, "contracts are stale: run tools/gen_contracts.py and commit"


@pytest.mark.parametrize("task,version", AI_TASKS.items())
def test_ai_fixtures(task, version):
    d = C / "fixtures" / "ai" / task
    req = json.loads((d / "request.json").read_text())
    contracts.validate_envelope(req)
    contracts.validate_payload(req["eventType"], req["payload"])
    assert req["payload"]["outputSchemaVersion"] == version
    contracts.validate_artefact(version, json.loads((d / "expected_output.json").read_text()))
    contracts.validate_artefact(version, json.loads((d / "kimi_response.json").read_text()), model_part=True)
    # negative fixtures are schema-valid model output; they fail only semantic/policy checks
    for name in NEGATIVE:
        if (d / name).exists():
            contracts.validate_artefact(version, json.loads((d / name).read_text()), model_part=True)


@pytest.mark.parametrize("kind,version", [("transcription", "transcript.v1"), ("synthesis", "tts_audio.v1")])
def test_speech_fixtures(kind, version):
    d = C / "fixtures" / "speech" / kind
    req = json.loads((d / "request.json").read_text())
    contracts.validate_envelope(req)
    contracts.validate_payload(req["eventType"], req["payload"])
    contracts.validate_artefact(version, json.loads((d / "expected_output.json").read_text()))


def test_error_codes_match_shared_enum():
    from ihr_worker_common.errors import ERROR_TABLE

    table = json.loads((C / "events" / "processing_error_codes.v1.json").read_text())
    assert {k: (v["errorClass"], v["retryable"]) for k, v in table.items()} == ERROR_TABLE
