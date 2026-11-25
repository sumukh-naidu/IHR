"""Structured call with one repair attempt (A-05, A-07, §4.2).

- finish_reason == "length" -> invalid output -> repair once with max_tokens x 1.5 (capped)
- unparseable / schema-invalid / semantically invalid -> one repair call listing the
  validation errors (paths + messages, no source text); then permanent failure
- never coerce, truncate or default a value
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from ihr_worker_common.errors import ProcessingError
from ihr_worker_common.metrics import counter

from .base import LlmClient, LlmResult, TaskParams

REPAIR_TEMPLATE = ("Your JSON did not pass validation: {errors}. "
                   "Return the full corrected JSON object only.")
MAX_ERRORS_IN_REPAIR = 20


@dataclass
class Validation:
    kind: str | None         # None (valid), "schema", "semantic" or "policy"
    errors: list[str]

    @classmethod
    def ok(cls) -> "Validation":
        return cls(None, [])


@dataclass
class StructuredResult:
    value: dict
    results: list[LlmResult]
    repaired: bool
    requests: list[list[dict]] = field(default_factory=list)   # messages sent per call (A-12 capture)
    errors: list[list[str]] = field(default_factory=list)      # validation errors per call (A-12 capture)

    @property
    def provider_attempts(self) -> int:
        return sum(r.provider_attempts for r in self.results)

    @property
    def input_tokens(self) -> int | None:
        vals = [r.input_tokens for r in self.results if r.input_tokens is not None]
        return sum(vals) if vals else None

    @property
    def output_tokens(self) -> int | None:
        vals = [r.output_tokens for r in self.results if r.output_tokens is not None]
        return sum(vals) if vals else None

    @property
    def latency_ms(self) -> int:
        return sum(r.latency_ms for r in self.results)


_FINAL_CODE = {
    "schema": "OUTPUT_SCHEMA_INVALID",
    "semantic": "OUTPUT_SEMANTIC_INVALID",
    "policy": "OUTPUT_POLICY_VIOLATION",
}


def _parse(result: LlmResult, validate: Callable[[dict], Validation]) -> tuple[dict | None, Validation]:
    if result.finish_reason == "length":
        return None, Validation("schema", ["/: output truncated (finish_reason=length)"])
    if not result.content.strip():
        return None, Validation("schema", ["/: empty content"])
    try:
        value = json.loads(result.content)
    except json.JSONDecodeError as e:
        return None, Validation("schema", [f"/: not valid JSON ({e.msg})"])
    if not isinstance(value, dict):
        return None, Validation("schema", ["/: top level must be a JSON object"])
    return value, validate(value)


def _count_policy(task: str, check: Validation) -> None:
    if check.kind == "policy":
        from ..pipelines.metrics import policy_rejection
        rules = {e.split("policy ")[-1] if "policy " in e else "OUTPUT_POLICY" for e in check.errors}
        for r in rules:
            policy_rejection(task, r.strip())


def call_structured(client: LlmClient, *, task: str, params: TaskParams, messages: list[dict],
                    deadline: datetime, validate: Callable[[dict], Validation],
                    allow_repair: bool = True) -> StructuredResult:
    validations = counter("ihr_ai_validation_total", "Validation outcome", ("task", "result"))

    def call(msgs: list[dict], max_tokens: int) -> LlmResult:
        return client.complete_json(task=task, model_slot=params.model_slot, messages=msgs,
                                    temperature=params.temperature, max_tokens=max_tokens,
                                    timeout_s=params.timeout_s, deadline=deadline)

    first = call(messages, params.max_tokens)
    value, check = _parse(first, validate)
    _count_policy(task, check)
    if value is not None and check.kind is None:
        validations.labels(task, "valid").inc()
        return StructuredResult(value, [first], repaired=False, requests=[messages], errors=[[]])
    if not allow_repair:
        validations.labels(task, "invalid").inc()
        err = ProcessingError(_FINAL_CODE[check.kind or "schema"], "Model output failed validation.")
        err.calls = StructuredResult({}, [first], False, [messages], [check.errors])
        raise err

    max_tokens = params.max_tokens
    if first.finish_reason == "length":
        max_tokens = min(int(params.max_tokens * 1.5), params.repair_cap)
    errors = "; ".join(check.errors[:MAX_ERRORS_IN_REPAIR])
    repair_msgs = messages + [
        {"role": "assistant", "content": first.content},
        {"role": "user", "content": REPAIR_TEMPLATE.format(errors=errors)},
    ]
    second = call(repair_msgs, max_tokens)
    value, check2 = _parse(second, validate)
    _count_policy(task, check2)
    if value is not None and check2.kind is None:
        validations.labels(task, "repaired").inc()
        return StructuredResult(value, [first, second], repaired=True, requests=[messages, repair_msgs],
                                errors=[check.errors, []])
    validations.labels(task, "invalid").inc()
    err = ProcessingError(_FINAL_CODE[check2.kind or "schema"], "Model output failed validation after one repair.")
    err.calls = StructuredResult({}, [first, second], True, [messages, repair_msgs], [check.errors, check2.errors])
    raise err
