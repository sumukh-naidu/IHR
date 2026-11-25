"""§12.1 metric helpers shared by the pipelines (labels never carry PII or text)."""
from __future__ import annotations

from ihr_worker_common.metrics import counter


def policy_rejection(task: str, rule: str, n: int = 1) -> None:
    counter("ihr_ai_policy_rejections_total", "Question/language policy hits", ("task", "rule")).labels(task, rule).inc(n)


def injection_flag(task: str) -> None:
    counter("ihr_ai_injection_flags_total", "Injection detector hits", ("task",)).labels(task).inc()


def fallback_question(reason: str) -> None:
    counter("ihr_ai_fallback_questions_total", "Plan fallback usage", ("reason",)).labels(reason).inc()
