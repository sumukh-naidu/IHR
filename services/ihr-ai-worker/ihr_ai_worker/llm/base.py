"""LLM provider interface (AI Specification v1.1 §4.1).

Business code depends only on LlmClient and never on a model name.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

ModelSlot = Literal["standard", "realtime"]


@dataclass
class LlmResult:
    content: str                     # raw JSON text
    provider_request_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int
    provider_attempts: int
    finish_reason: str               # "stop" expected; "length" -> treated as invalid output
    model: str = ""


class LlmClient(Protocol):
    provider_name: str

    def model_for(self, model_slot: ModelSlot) -> str: ...

    def complete_json(self, *, task: str, model_slot: ModelSlot, messages: list[dict],
                      temperature: float, max_tokens: int, timeout_s: float,
                      deadline: datetime) -> LlmResult: ...


class ContextLengthExceeded(Exception):
    """HTTP 400 for context length (§4.4): caller may shorten input by 25 % once and retry."""


@dataclass(frozen=True)
class TaskParams:
    model_slot: ModelSlot
    temperature: float
    max_tokens: int
    repair_cap: int
    timeout_s: float


# §4.3 per-task parameters
TASK_PARAMS: dict[str, TaskParams] = {
    "CV_PARSE": TaskParams("standard", 0.1, 4000, 6000, 60),
    "CV_MATCH": TaskParams("standard", 0.1, 3000, 4500, 60),
    "INTERVIEW_PLAN": TaskParams("standard", 0.5, 4000, 6000, 60),
    "FOLLOWUP": TaskParams("realtime", 0.3, 400, 600, 8),
    "ANSWER_ASSESSMENT": TaskParams("standard", 0.1, 1500, 2250, 45),
    "FINAL_REPORT": TaskParams("standard", 0.2, 3000, 4500, 90),
}
