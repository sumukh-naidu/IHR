"""Kimi adapter (A-03, A-08, A-09, §4.4) and repair-once flow (A-07)."""
import json
import random
from datetime import timedelta

import httpx
import openai
import pytest
import respx

from ihr_worker_common.errors import ProcessingError
from ihr_worker_common.events import utc_now
from ihr_ai_worker.llm.base import TASK_PARAMS, ContextLengthExceeded
from ihr_ai_worker.llm.kimi import KimiClient
from ihr_ai_worker.llm.mock import MockLlmClient
from ihr_ai_worker.llm.resilience import CircuitBreaker, RetryPolicy, TokenBucket
from ihr_ai_worker.llm.structured import Validation, call_structured

BASE = "https://kimi.test/v1"
URL = f"{BASE}/chat/completions"


def ok_body(content='{"a": 1}', finish="stop"):
    return {"id": "req-1", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "finish_reason": finish,
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


def client(sleeps=None):
    sleeps = sleeps if sleeps is not None else []
    return KimiClient(base_url=BASE, api_key="k", model_standard="std", model_realtime="rt",
                      max_concurrency=2, rpm_limit=600, sleep=sleeps.append)


def call(c, slot="standard", seconds=60):
    return c.complete_json(task="CV_MATCH", model_slot=slot, messages=[{"role": "user", "content": "x"}],
                           temperature=0.1, max_tokens=100, timeout_s=5,
                           deadline=utc_now() + timedelta(seconds=seconds))


# ---------------------------------------------------------------- policy units

def test_retry_policy_standard_two_retries_with_jitter():
    p = RetryPolicy.for_slot("standard")
    rng = random.Random(0)
    d0 = p.next_delay(0, 60, None, rng)
    d1 = p.next_delay(1, 60, None, rng)
    assert 0.8 <= d0 <= 1.2 and 3.2 <= d1 <= 4.8
    assert p.next_delay(2, 60, None) is None


def test_retry_after_capped_at_20s_and_deadline_margin():
    p = RetryPolicy.for_slot("standard")
    assert p.next_delay(0, 60, 45) == 20
    assert p.next_delay(0, 5, 4) is None  # 4 s >= 5 s - 2 s margin


def test_realtime_one_retry_only_with_4s_left():
    p = RetryPolicy.for_slot("realtime")
    assert p.next_delay(0, 3.9, None) is None
    assert p.next_delay(0, 10, None) is not None
    assert p.next_delay(1, 10, None) is None


def test_circuit_opens_after_5_failures_then_half_open_single_trial():
    now = [0.0]
    cb = CircuitBreaker("t-slot", clock=lambda: now[0])
    for _ in range(5):
        assert cb.allow()
        cb.failure()
    assert cb.state == "open" and not cb.allow()
    now[0] = 30.0
    assert cb.allow() and not cb.allow()  # one trial only
    cb.success()
    assert cb.state == "closed"


def test_token_bucket_waits_then_gives_up():
    now = [0.0]
    tb = TokenBucket(60, clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s))
    for _ in range(60):
        assert tb.acquire(0)
    assert not tb.acquire(0.5)
    assert tb.acquire(2)


# ---------------------------------------------------------------- Kimi client over HTTP stubs

@respx.mock
def test_success_returns_result_and_json_mode():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=ok_body()))
    r = call(client())
    sent = json.loads(route.calls[0].request.content)
    assert sent["response_format"] == {"type": "json_object"} and sent["n"] == 1 and sent["stream"] is False
    assert sent["model"] == "std"
    assert r.content == '{"a": 1}' and r.input_tokens == 10 and r.provider_attempts == 1


@respx.mock
def test_429_retried_honouring_retry_after():
    sleeps = []
    respx.post(URL).mock(side_effect=[httpx.Response(429, headers={"retry-after": "3"}, json={}),
                                      httpx.Response(200, json=ok_body())])
    r = call(client(sleeps))
    assert sleeps == [3.0] and r.provider_attempts == 2


@respx.mock
def test_5xx_exhausted_maps_to_provider_unavailable():
    respx.post(URL).mock(return_value=httpx.Response(503, json={}))
    with pytest.raises(ProcessingError) as e:
        call(client())
    assert e.value.code == "PROVIDER_UNAVAILABLE"


@respx.mock
def test_timeout_maps_to_provider_timeout():
    respx.post(URL).mock(side_effect=httpx.ReadTimeout("t"))
    with pytest.raises(ProcessingError) as e:
        call(client())
    assert e.value.code == "PROVIDER_TIMEOUT"


@respx.mock
def test_401_is_internal_error_without_retry():
    route = respx.post(URL).mock(return_value=httpx.Response(401, json={}))
    with pytest.raises(ProcessingError) as e:
        call(client())
    assert e.value.code == "INTERNAL_ERROR" and route.call_count == 1


@respx.mock
def test_context_length_400_is_signalled_to_caller():
    respx.post(URL).mock(return_value=httpx.Response(
        400, json={"error": {"message": "input exceeds the model's maximum context length"}}))
    with pytest.raises(ContextLengthExceeded):
        call(client())


@respx.mock
def test_open_circuit_fails_fast():
    route = respx.post(URL).mock(return_value=httpx.Response(503, json={}))
    c = client()
    for _ in range(2):  # 2 calls x 3 attempts = 6 failures >= 5
        with pytest.raises(ProcessingError):
            call(c)
    n = route.call_count
    with pytest.raises(ProcessingError) as e:
        call(c)
    assert e.value.code == "PROVIDER_UNAVAILABLE" and route.call_count == n


# ---------------------------------------------------------------- repair once

def _validate_has_a(v):
    return Validation.ok() if "a" in v else Validation("schema", ["/a: required"])


def test_repair_once_succeeds():
    mock = MockLlmClient(responses={"CV_MATCH": ['{"b": 1}', '{"a": 2}']})
    r = call_structured(mock, task="CV_MATCH", params=TASK_PARAMS["CV_MATCH"],
                        messages=[{"role": "user", "content": "x"}],
                        deadline=utc_now() + timedelta(seconds=60), validate=_validate_has_a)
    assert r.repaired and r.value == {"a": 2}
    repair_msg = mock.calls[1]["messages"][-1]["content"]
    assert repair_msg.startswith("Your JSON did not pass validation: /a: required")


def test_still_invalid_after_repair_is_permanent():
    mock = MockLlmClient(responses={"CV_MATCH": ['{"b": 1}', "not json"]})
    with pytest.raises(ProcessingError) as e:
        call_structured(mock, task="CV_MATCH", params=TASK_PARAMS["CV_MATCH"],
                        messages=[], deadline=utc_now() + timedelta(seconds=60), validate=_validate_has_a)
    assert e.value.code == "OUTPUT_SCHEMA_INVALID"


def test_followup_has_no_repair():
    mock = MockLlmClient(responses={"FOLLOWUP": ['{"b": 1}']})
    with pytest.raises(ProcessingError):
        call_structured(mock, task="FOLLOWUP", params=TASK_PARAMS["FOLLOWUP"], messages=[],
                        deadline=utc_now() + timedelta(seconds=12), validate=_validate_has_a, allow_repair=False)
    assert len(mock.calls) == 1


def test_mock_llm_returns_kimi_response_fixture():
    r = MockLlmClient().complete_json(task="CV_PARSE", model_slot="standard", messages=[], temperature=0.1,
                                      max_tokens=1, timeout_s=1, deadline=utc_now())
    assert json.loads(r.content)["currentEmployer"] == "Acme Payments"
