"""Leak tests (§14): sentinel PII through every AI handler; it must never appear in logs, metric labels,
model request bodies, outbound events or debug captures. (Identity values are allowed only in the CV_PARSE
.completed `identity` block, which core stores — §7.1.)"""
import copy
import json
import logging
from pathlib import Path

import pytest
from prometheus_client import REGISTRY

from ihr_worker_common.processor import Processor
from ihr_worker_common.storage import InMemoryStorage, put_json
from ihr_ai_worker.llm.mock import MockLlmClient
from ihr_ai_worker.pipelines.answer_assessment import AnswerAssessmentHandler
from ihr_ai_worker.pipelines.cv_match import CvMatchHandler
from ihr_ai_worker.pipelines.cv_parse import CvParseHandler
from ihr_ai_worker.pipelines.final_report import FinalReportHandler
from ihr_ai_worker.pipelines.followup import FollowupHandler
from ihr_ai_worker.pipelines.interview_plan import InterviewPlanHandler
from ihr_ai_worker.prompts import load_registry

from cv_docs import CV_LINES, docx

ROOT = Path(__file__).resolve().parents[3]
FIX = ROOT / "contracts/fixtures"
REG = load_registry(ROOT / "docs/prompts/active.yaml")

NAME = "Zorblax Quintavelle"
EMAIL = "zorblax.q@sentinel-mail.test"
PHONE = "+91 91234 56789"
AADHAAR = "2345 6789 0123"
SENTINELS = ["Zorblax", "Quintavelle", "zorblax.q", "91234", "2345 6789 0123"]


class Clean:
    def scan(self, d):
        pass


def _cv_lines():
    lines = list(CV_LINES)
    lines[0] = NAME
    lines[1] = f"Senior Software Engineer | {EMAIL} | {PHONE} | Aadhaar {AADHAAR}"
    lines.insert(5, f"{NAME.split()[0]} led the payments platform migration.")
    return lines


def _transcript_with_name():
    t = json.loads((FIX / "speech/transcription/expected_output.json").read_text())
    t["segments"][0]["text"] = f"Hi, I am {NAME}, email {EMAIL}, phone {PHONE}. " + t["segments"][0]["text"]
    return t


def _hints():
    return {"fullName": NAME, "email": EMAIL, "phone": PHONE}


def _run(handler, storage, env):
    proc = Processor(handler, storage, "ihr-ai-worker", "t_leak")
    [out] = proc.process(json.dumps(env).encode(), env["entityId"], {})
    proc.shutdown()
    return out


@pytest.fixture()
def captured_logs(capsys, caplog):
    caplog.set_level(logging.DEBUG)
    yield lambda: capsys.readouterr().out + capsys.readouterr().err + caplog.text


def _assert_clean(text: str, where: str):
    for s in SENTINELS:
        assert s not in text, f"sentinel {s!r} leaked into {where}"


def _llm_bodies(llm) -> str:
    return json.dumps([c["messages"] for c in llm.calls])


def test_cv_parse_and_match_do_not_leak(captured_logs):
    storage, llm = InMemoryStorage(), MockLlmClient()
    env = json.loads((FIX / "ai/cv_parse/request.json").read_text())
    data = docx(_cv_lines())
    p = env["payload"]
    p["document"] = storage.put_bytes(p["document"]["bucket"], p["document"]["key"], data, "application/octet-stream")
    p["originalExtension"], p["redactionHints"] = "docx", _hints()
    out = _run(CvParseHandler(llm, REG, scanner=Clean()), storage, env)
    ev = json.loads(out.value)
    assert out.topic.endswith(".completed")
    _assert_clean(_llm_bodies(llm), "CV_PARSE model request")
    text = storage.get_bytes(ev["payload"]["output"]["cvText"]["bucket"], ev["payload"]["output"]["cvText"]["key"]).decode()
    _assert_clean(text, "stored redacted CV text")
    profile = json.loads(storage.get_bytes(ev["payload"]["output"]["cvProfile"]["bucket"], ev["payload"]["output"]["cvProfile"]["key"]))
    _assert_clean(json.dumps({k: v for k, v in profile.items() if k != "identity"}), "profile outside identity")
    _assert_clean(json.dumps({k: v for k, v in ev["payload"].items() if k != "identity"}), ".completed outside identity")

    menv = json.loads((FIX / "ai/cv_match/request.json").read_text())
    menv["payload"]["cvProfile"], menv["payload"]["cvText"] = ev["payload"]["output"]["cvProfile"], ev["payload"]["output"]["cvText"]
    llm2 = MockLlmClient()
    _run(CvMatchHandler(llm2, REG), storage, menv)
    _assert_clean(_llm_bodies(llm2), "CV_MATCH model request")
    _assert_clean(json.dumps([json.loads(v) for (_, k), (v, _) in storage.objects.items() if k.startswith("ai-runs/")]),
                  "debug captures")
    _assert_clean(captured_logs(), "logs")


@pytest.mark.parametrize("task", ["FOLLOWUP", "ANSWER_ASSESSMENT", "FINAL_REPORT"])
def test_interview_tasks_do_not_leak(task, captured_logs):
    storage, llm = InMemoryStorage(), MockLlmClient()
    t = _transcript_with_name()
    d = {"FOLLOWUP": "followup_decision", "ANSWER_ASSESSMENT": "answer_assessment", "FINAL_REPORT": "final_report"}[task]
    env = json.loads((FIX / f"ai/{d}/request.json").read_text())
    p = env["payload"]
    p["redactionHints"] = _hints()
    if task == "FINAL_REPORT":
        a = json.loads((FIX / "ai/answer_assessment/expected_output.json").read_text())
        for turn in p["turns"]:
            turn["transcript"] = put_json(storage, turn["transcript"]["bucket"], turn["transcript"]["key"], t)
            turn["assessment"] = put_json(storage, turn["assessment"]["bucket"], turn["assessment"]["key"], a)
        handler = FinalReportHandler(llm, REG)
    else:
        p["transcript"] = put_json(storage, p["transcript"]["bucket"], p["transcript"]["key"], t)
        handler = (FollowupHandler if task == "FOLLOWUP" else AnswerAssessmentHandler)(llm, REG)
    out = _run(handler, storage, env)
    _assert_clean(_llm_bodies(llm), f"{task} model request")
    _assert_clean(out.value.decode(), f"{task} outbound event")
    _assert_clean(captured_logs(), "logs")


def test_plan_does_not_leak_identity():
    storage, llm = InMemoryStorage(), MockLlmClient()
    env = json.loads((FIX / "ai/interview_plan/request.json").read_text())
    prof = json.loads((FIX / "ai/cv_parse/expected_output.json").read_text())
    prof["identity"] = {"name": {"value": NAME, "confidence": 0.6, "source": "LOCAL_HEADER"},
                        "email": {"value": EMAIL, "confidence": 1.0, "source": "LOCAL_REGEX"},
                        "phone": {"value": PHONE, "confidence": 1.0, "source": "LOCAL_REGEX"}}
    env["payload"]["cvProfile"] = put_json(storage, "ihr-generated-private", "cv/x/profile/y.json", prof)
    _run(InterviewPlanHandler(llm, REG), storage, env)
    _assert_clean(_llm_bodies(llm), "INTERVIEW_PLAN model request")


def test_failed_event_message_has_no_pii():
    storage, llm = InMemoryStorage(), MockLlmClient(responses={"FOLLOWUP": ["not json"]})
    env = json.loads((FIX / "ai/followup_decision/request.json").read_text())
    env["payload"]["redactionHints"] = _hints()
    env["payload"]["transcript"] = put_json(storage, env["payload"]["transcript"]["bucket"],
                                            env["payload"]["transcript"]["key"], _transcript_with_name())
    out = _run(FollowupHandler(llm, REG), storage, env)
    assert out.topic.endswith(".failed")
    _assert_clean(out.value.decode(), ".failed event")


def test_metric_labels_have_no_pii():
    labels = []
    for metric in REGISTRY.collect():
        for sample in metric.samples:
            labels.append(json.dumps(sample.labels))
    _assert_clean("\n".join(labels), "metric labels")


def test_tts_rejects_pii_text():
    from speech.tts_mock import check_text
    from ihr_worker_common.errors import ProcessingError
    for text in (f"Hello {EMAIL}, tell me about Kafka.", f"Call {PHONE} later.", "Hello [CANDIDATE], welcome."):
        with pytest.raises(ProcessingError):
            check_text(text)


def test_leak_check_is_not_vacuous():
    with pytest.raises(AssertionError):
        _assert_clean(json.dumps(_transcript_with_name()), "raw transcript")
    with pytest.raises(AssertionError):
        _assert_clean("\n".join(_cv_lines()), "raw CV")
