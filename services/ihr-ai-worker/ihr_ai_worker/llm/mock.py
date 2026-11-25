"""MockLlmClient (§4.1, §7.7): returns contracts/fixtures/ai/{task}/kimi_response.json."""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from ihr_worker_common.contracts import contracts_dir

from .base import LlmResult, ModelSlot

TASK_DIRS = {
    "CV_PARSE": "cv_parse",
    "CV_MATCH": "cv_match",
    "INTERVIEW_PLAN": "interview_plan",
    "FOLLOWUP": "followup_decision",
    "ANSWER_ASSESSMENT": "answer_assessment",
    "FINAL_REPORT": "final_report",
}


class MockLlmClient:
    provider_name = "mock"

    def __init__(self, fixtures_root: Path | None = None, latency_s: float = 0.0,
                 responses: dict[str, list[str]] | None = None) -> None:
        self.root = fixtures_root or contracts_dir() / "fixtures" / "ai"
        self.latency_s = latency_s
        self._scripted = {k: list(v) for k, v in (responses or {}).items()}
        self.calls: list[dict] = []

    def model_for(self, model_slot: ModelSlot) -> str:
        return f"mock-{model_slot}"

    def complete_json(self, *, task: str, model_slot: ModelSlot, messages: list[dict],
                      temperature: float, max_tokens: int, timeout_s: float,
                      deadline: datetime) -> LlmResult:
        self.calls.append({"task": task, "messages": messages, "max_tokens": max_tokens})
        if self.latency_s:
            time.sleep(self.latency_s)
        if self._scripted.get(task):
            content = self._scripted[task].pop(0)
        else:
            content = (self.root / TASK_DIRS[task] / "kimi_response.json").read_text(encoding="utf-8")
        return LlmResult(content=content, provider_request_id="mock", input_tokens=None, output_tokens=None,
                         latency_ms=int(self.latency_s * 1000), provider_attempts=1, finish_reason="stop",
                         model=self.model_for(model_slot))
