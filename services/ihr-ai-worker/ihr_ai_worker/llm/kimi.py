"""Moonshot Kimi adapter (S-11, A-03, A-08, A-09, §4.4).

openai.OpenAI(base_url=KIMI_API_BASE_URL, max_retries=0); Chat Completions;
response_format={"type": "json_object"}; no streaming, no tools, n=1.
Retries are our own policy (A-08), bounded by the job deadline.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

import openai

from ihr_worker_common.errors import ProcessingError
from ihr_worker_common.events import utc_now
from ihr_worker_common.log import get_logger
from ihr_worker_common.metrics import counter, histogram

from .base import ContextLengthExceeded, LlmResult, ModelSlot
from .resilience import CircuitBreaker, RetryPolicy, TokenBucket

log = get_logger("ihr.kimi")

_TRANSIENT = {
    "timeout": "PROVIDER_TIMEOUT",
    "rate_limited": "PROVIDER_RATE_LIMITED",
    "unavailable": "PROVIDER_UNAVAILABLE",
}


class KimiConfigError(Exception):
    """400 (other) / 401 / 403 / 404: configuration problem; no retry, alert."""


class _Transient(Exception):
    def __init__(self, kind: str, retry_after: float | None = None):
        super().__init__(kind)
        self.kind = kind
        self.retry_after = retry_after


class KimiClient:
    provider_name = "moonshot"

    def __init__(self, *, base_url: str, api_key: str, model_standard: str, model_realtime: str,
                 max_concurrency: int, rpm_limit: int, sdk_client: openai.OpenAI | None = None,
                 sleep=time.sleep, provider_name: str = "moonshot") -> None:
        self.provider_name = provider_name
        self._client = sdk_client or openai.OpenAI(base_url=base_url, api_key=api_key, max_retries=0)
        self._models = {"standard": model_standard, "realtime": model_realtime}
        self._breakers = {slot: CircuitBreaker(f"kimi-{slot}") for slot in self._models}
        self._sem = threading.BoundedSemaphore(max_concurrency)
        self._bucket = TokenBucket(rpm_limit)
        self._sleep = sleep
        self._latency = histogram("ihr_ai_llm_latency_seconds", "Provider latency", ("task", "model"))
        self._tokens = counter("ihr_ai_tokens_total", "Tokens", ("task", "model", "direction"))
        self._config_errors = counter("ihr_ai_kimi_config_errors_total", "KimiConfigError (alert)", ("status",))

    def model_for(self, model_slot: ModelSlot) -> str:
        return self._models[model_slot]

    def complete_json(self, *, task: str, model_slot: ModelSlot, messages: list[dict],
                      temperature: float, max_tokens: int, timeout_s: float,
                      deadline: datetime) -> LlmResult:
        model = self._models[model_slot]
        breaker = self._breakers[model_slot]
        policy = RetryPolicy.for_slot(model_slot)
        attempts = 0
        retries = 0
        started = time.monotonic()
        while True:
            if not breaker.allow():
                raise ProcessingError("PROVIDER_UNAVAILABLE", "Model provider circuit is open.")
            remaining = (deadline - utc_now()).total_seconds()
            if remaining <= 0:
                raise ProcessingError("DEADLINE_EXCEEDED", "Work could not finish before the deadline.")
            if not self._bucket.acquire(max_wait_s=max(remaining - 2, 0)):
                raise ProcessingError("PROVIDER_RATE_LIMITED", "Client-side rate limit reached before the deadline.")
            attempts += 1
            call_timeout = min(timeout_s, max((deadline - utc_now()).total_seconds(), 0.1))
            t0 = time.monotonic()
            try:
                with self._sem:
                    resp = self._call(model, messages, temperature, max_tokens, call_timeout)
            except _Transient as e:
                breaker.failure()
                remaining = (deadline - utc_now()).total_seconds()
                delay = policy.next_delay(retries, remaining, e.retry_after)
                if delay is None:
                    raise ProcessingError(_TRANSIENT[e.kind], f"Model provider call failed: {e.kind}.") from None
                retries += 1
                self._sleep(delay)
                continue
            except ContextLengthExceeded:
                breaker.success()  # the provider answered; not an availability problem
                raise
            except KimiConfigError as e:
                breaker.success()
                self._config_errors.labels(str(e)).inc()
                log.error("kimi_config_error", task=task, status=str(e))
                raise ProcessingError("INTERNAL_ERROR", "Model provider rejected the request configuration.") from None
            breaker.success()
            self._latency.labels(task, model).observe(time.monotonic() - t0)
            usage = getattr(resp, "usage", None)
            in_tok = getattr(usage, "prompt_tokens", None)
            out_tok = getattr(usage, "completion_tokens", None)
            if in_tok:
                self._tokens.labels(task, model, "in").inc(in_tok)
            if out_tok:
                self._tokens.labels(task, model, "out").inc(out_tok)
            choice = resp.choices[0]
            return LlmResult(
                content=choice.message.content or "",
                provider_request_id=getattr(resp, "id", None),
                input_tokens=in_tok,
                output_tokens=out_tok,
                latency_ms=int((time.monotonic() - started) * 1000),
                provider_attempts=attempts,
                finish_reason=choice.finish_reason or "",
                model=model,
            )

    def _call(self, model: str, messages: list[dict], temperature: float, max_tokens: int, timeout: float):
        try:
            return self._client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                n=1,
                stream=False,
                timeout=timeout,
            )
        except openai.APITimeoutError:
            raise _Transient("timeout") from None
        except openai.APIConnectionError:
            raise _Transient("timeout") from None  # §4.4: timeout / connection reset -> PROVIDER_TIMEOUT
        except openai.RateLimitError as e:
            raise _Transient("rate_limited", _retry_after(e)) from None
        except openai.BadRequestError as e:
            if _is_context_length(e):
                raise ContextLengthExceeded() from None
            raise KimiConfigError("400") from None
        except openai.APIStatusError as e:
            if e.status_code in (500, 502, 503, 504) or e.status_code >= 500:
                raise _Transient("unavailable", _retry_after(e)) from None
            if e.status_code == 429:
                raise _Transient("rate_limited", _retry_after(e)) from None
            raise KimiConfigError(str(e.status_code)) from None


def build_llm_client(cfg) -> "KimiClient":
    """Kimi in production; the same adapter against a local llama-server when AI_PROVIDER=local."""
    return KimiClient(
        base_url=cfg.kimi_api_base_url, api_key=cfg.kimi_api_key or "", model_standard=cfg.kimi_model_standard,
        model_realtime=cfg.kimi_model_realtime, max_concurrency=cfg.kimi_max_concurrency,
        rpm_limit=cfg.kimi_rpm_limit, provider_name="local" if cfg.ai_provider == "local" else "moonshot")


def _retry_after(e: openai.APIStatusError) -> float | None:
    try:
        value = e.response.headers.get("retry-after")
        return float(value) if value is not None else None
    except (AttributeError, ValueError):
        return None


def _is_context_length(e: openai.BadRequestError) -> bool:
    text = str(getattr(e, "message", "") or e).lower()
    return any(k in text for k in ("context length", "context_length", "maximum context", "too many tokens",
                                   "exceeds the model"))
