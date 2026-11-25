import copy
import io
import json
import threading
import time
from pathlib import Path

import pytest

from ihr_worker_common import log as wlog
from ihr_worker_common.errors import ProcessingError
from ihr_worker_common.events import iso_ms, utc_now
from ihr_worker_common.processor import EventLru, HandlerResult, Processor
from ihr_worker_common.runner import OffsetTracker
from ihr_worker_common.storage import InMemoryStorage, put_json

FIXTURE = Path(__file__).resolve().parents[3] / "contracts/fixtures/ai/answer_assessment/request.json"
RUN = {"provider": "test", "model": "t", "promptId": None, "promptVersion": None, "promptSha256": None,
       "providerRequestId": None, "latencyMs": 1, "inputTokens": None, "outputTokens": None,
       "providerAttempts": 1, "repairAttempted": False, "validationStatus": "VALID", "workerVersion": "1"}


class Handler:
    task = "ANSWER_ASSESSMENT"
    request_topic = "ihr.interview.answer-assessment.requested"
    output_schema_version = "answer_assessment.v1"

    def __init__(self, behaviour=None):
        self.calls = 0
        self.behaviour = behaviour

    def output_location(self, p):
        return "gen", f"interview/{p['interviewId']}/assessments/{p['turnId']}/{p['jobId']}.json"

    def handle(self, ctx):
        self.calls += 1
        if self.behaviour:
            self.behaviour(ctx)
        ref = put_json(ctx.storage, *self.output_location(ctx.payload), {"ok": True})
        return HandlerResult(output=ref, run=RUN)


def request(**payload_overrides):
    env = json.loads(FIXTURE.read_text())
    env["payload"].update(payload_overrides)
    return env


def run(proc, env):
    return proc.process(json.dumps(env).encode(), env["entityId"], {"traceparent": "00-abc-def-01"})


@pytest.fixture
def proc():
    p = Processor(Handler(), InMemoryStorage(), "ihr-ai-worker", "t_proc")
    yield p
    p.shutdown()


def body(out):
    return json.loads(out.value)


def test_success_produces_completed_with_same_key_and_causation(proc):
    env = request()
    [out] = run(proc, env)
    assert out.topic == "ihr.interview.answer-assessment.completed"
    assert out.key == env["entityId"]
    e = body(out)
    assert e["causationId"] == env["eventId"] and e["correlationId"] == env["correlationId"]
    assert e["producer"] == "ihr-ai-worker" and e["payload"]["jobId"] == env["payload"]["jobId"]
    assert out.headers["traceparent"] == "00-abc-def-01"


def test_unparseable_goes_to_dlq_with_original_bytes(proc):
    [out] = proc.process(b"{not json", "k", {})
    assert out.topic == "ihr.interview.answer-assessment.requested.dlq"
    assert out.value == b"{not json" and "ihr-dlq-reason" in out.headers


def test_schema_invalid_goes_to_dlq(proc):
    env = request()
    del env["payload"]["transcript"]
    [out] = run(proc, env)
    assert out.topic.endswith(".dlq")


def test_unsupported_schema_version_is_failed(proc):
    [out] = run(proc, request(outputSchemaVersion="answer_assessment.v2"))
    p = body(out)["payload"]
    assert out.topic.endswith(".failed") and p["errorCode"] == "UNSUPPORTED_SCHEMA_VERSION"
    assert p["retryable"] is False and p["errorClass"] == "PERMANENT"


def test_duplicate_event_is_skipped(proc):
    env = request()
    assert len(run(proc, env)) == 1
    assert run(proc, env) == []
    assert proc.handler.calls == 1


def test_expired_deadline_fails_without_running(proc):
    [out] = run(proc, request(deadlineAt="2020-01-01T00:00:00.000Z"))
    p = body(out)["payload"]
    assert p["errorCode"] == "DEADLINE_EXCEEDED" and p["retryable"] is True
    assert proc.handler.calls == 0


def test_idempotent_replay_for_new_event_same_job(proc):
    env = request()
    [first] = run(proc, env)
    env2 = copy.deepcopy(env)
    env2["eventId"] = "0192f000-0000-7000-8000-000000099999"
    [second] = run(proc, env2)
    assert proc.handler.calls == 1
    assert body(second)["payload"]["output"] == body(first)["payload"]["output"]


def test_handler_timeout_maps_to_deadline_exceeded():
    from datetime import timedelta

    p = Processor(Handler(lambda ctx: time.sleep(2)), InMemoryStorage(), "ihr-ai-worker", "t_proc")
    env = request(deadlineAt=iso_ms(utc_now() + timedelta(milliseconds=300)))
    [out] = run(p, env)
    assert body(out)["payload"]["errorCode"] == "DEADLINE_EXCEEDED"
    p.shutdown(wait=False)


def test_processing_error_maps_to_failed():
    def boom(ctx):
        raise ProcessingError("INPUT_NOT_FOUND", "Referenced object is missing.")
    p = Processor(Handler(boom), InMemoryStorage(), "ihr-ai-worker", "t_proc")
    [out] = run(p, request())
    assert body(out)["payload"]["errorCode"] == "INPUT_NOT_FOUND"
    p.shutdown()


def test_unexpected_exception_is_internal_error_without_leaking_text():
    def boom(ctx):
        raise RuntimeError("SECRET transcript text")
    p = Processor(Handler(boom), InMemoryStorage(), "ihr-ai-worker", "t_proc")
    [out] = run(p, request())
    pl = body(out)["payload"]
    assert pl["errorCode"] == "INTERNAL_ERROR" and "SECRET" not in pl["message"]
    p.shutdown()


def test_sentinel_pii_never_reaches_logs():
    """§3.2: payload bodies, redactionHints, transcript text etc. never reach logs."""
    stream = io.StringIO()
    wlog.configure("test", stream=stream)
    sentinel = "SENTINEL-PII-7f3a"

    def leaky(ctx):
        wlog.get_logger().info("handler_log", fullName=sentinel, payload=ctx.payload, text=sentinel)
        raise RuntimeError(sentinel)

    p = Processor(Handler(leaky), InMemoryStorage(), "ihr-ai-worker", "t_proc")
    env = request(redactionHints={"fullName": sentinel, "email": f"{sentinel}@x.com", "phone": None})
    run(p, env)
    p.shutdown()
    assert stream.getvalue(), "expected some log output"
    assert sentinel not in stream.getvalue()


def test_lru_evicts_oldest():
    lru = EventLru(size=2)
    for e in ("a", "b", "c"):
        lru.add(e)
    assert not lru.seen("a") and lru.seen("b") and lru.seen("c")


def test_offset_tracker_commits_only_contiguous_prefix():
    t = OffsetTracker()
    for o in (10, 11, 12):
        t.started("t", 0, o)
    t.finished("t", 0, 12)
    assert t.committable() == {}
    t.finished("t", 0, 10)
    assert t.committable() == {("t", 0): 11}
    t.finished("t", 0, 11)
    assert t.committable() == {("t", 0): 13}
    assert t.in_flight() == 0
