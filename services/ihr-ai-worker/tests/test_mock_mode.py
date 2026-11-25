"""AI_PROVIDER=mock end to end through the Processor (A.8)."""
import json
import random
from pathlib import Path

import pytest

from ihr_worker_common import contracts
from ihr_worker_common.processor import Processor
from ihr_worker_common.storage import InMemoryStorage
from ihr_ai_worker.mock_handlers import MockTaskHandler
from ihr_ai_worker.tasks import TASKS, tasks_for_role

FIX = Path(__file__).resolve().parents[3] / "contracts/fixtures/ai"


def run_task(task, failure_rate=0.0):
    spec = TASKS[task]
    storage = InMemoryStorage()
    handler = MockTaskHandler(spec, failure_rate=failure_rate, latency_s=0, rng=random.Random(1))
    proc = Processor(handler, storage, "ihr-ai-worker", "t_mock")
    env = json.loads((FIX / spec.fixture_dir / "request.json").read_text())
    [out] = proc.process(json.dumps(env).encode(), env["entityId"], {})
    proc.shutdown()
    return spec, storage, out, json.loads(out.value)


@pytest.mark.parametrize("task", list(TASKS))
def test_mock_task_completes_and_writes_valid_artefact(task):
    spec, storage, out, event = run_task(task)
    assert out.topic == spec.request_topic.replace(".requested", ".completed")
    contracts.validate_envelope(event)
    contracts.validate_payload(out.topic, event["payload"])
    output = event["payload"]["output"]
    if task == "FOLLOWUP":
        contracts.validate_artefact("followup_decision.v1", output)
        return
    refs = output.values() if task == "CV_PARSE" else [output]
    for ref in refs:
        assert storage.exists(ref["bucket"], ref["key"])
    if task != "CV_PARSE":
        artefact = json.loads(storage.get_bytes(output["bucket"], output["key"]))
        contracts.validate_artefact(spec.output_schema_version, artefact)
    else:
        assert output["cvProfile"]["key"].startswith("cv/") and output["cvText"]["key"].endswith(".txt")
        assert event["payload"]["identity"]["email"]["source"] == "LOCAL_REGEX"


def test_final_report_attaches_request_scores_unchanged():
    _, storage, _, event = run_task("FINAL_REPORT")
    ref = event["payload"]["output"]
    report = json.loads(storage.get_bytes(ref["bucket"], ref["key"]))
    req = json.loads((FIX / "final_report/request.json").read_text())
    assert report["computedScores"] == req["payload"]["computedScores"]


def test_mock_failure_rate_injects_transient_failure():
    _, _, out, event = run_task("CV_MATCH", failure_rate=1.0)
    p = event["payload"]
    assert out.topic.endswith(".failed") and p["errorClass"] == "TRANSIENT" and p["retryable"]


def test_roles_split_followup_onto_realtime():
    assert [t.task for t in tasks_for_role("realtime")] == ["FOLLOWUP"]
    assert "FOLLOWUP" not in [t.task for t in tasks_for_role("standard")]
