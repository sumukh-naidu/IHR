"""structlog JSON logging (§3.2).

Only an allow-list of keys ever reaches the output. Payload bodies, redactionHints,
CV/transcript text, prompts, model responses and presigned URLs are dropped even if
a caller passes them by mistake.
"""
from __future__ import annotations

import logging
import sys

import structlog

ALLOWED_KEYS = frozenset({
    "event", "level", "timestamp", "logger",
    "correlationId", "eventId", "jobId", "task", "entityId", "attempt", "durationMs", "errorCode",
    "topic", "partition", "offset", "service", "role", "reason", "provider", "model",
    "promptVersion", "latencyMs", "inputTokens", "outputTokens", "providerAttempts", "status",
})


def _allow_list(_logger, _method, event_dict: dict) -> dict:
    return {k: v for k, v in event_dict.items() if k in ALLOWED_KEYS}


def configure(service: str, level: str = "INFO", stream=None) -> None:
    logging.basicConfig(stream=stream or sys.stdout, level=level, format="%(message)s", force=True)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _allow_list,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(stream or sys.stdout),
        cache_logger_on_first_use=False,
    )
    structlog.contextvars.bind_contextvars(service=service)


def get_logger(name: str = "ihr"):
    return structlog.get_logger(name)
