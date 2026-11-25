"""Event envelope helpers (A.2)."""
from __future__ import annotations

import json
import os
import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


def uuid7() -> str:
    """UUIDv7 (A.2 eventId): 48-bit unix ms timestamp + random bits."""
    ms = int(time.time() * 1000)
    rand = int.from_bytes(secrets.token_bytes(10), "big")
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= ((rand >> 62) & 0xFFF) << 64
    value |= 0b10 << 62
    value |= rand & ((1 << 62) - 1)
    return str(uuid.UUID(int=value))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_ms(dt: datetime) -> str:
    """UTC ISO-8601 with milliseconds and Z (A.2 occurredAt)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass
class Outbound:
    topic: str
    key: str
    value: bytes
    headers: dict[str, str] = field(default_factory=dict)


def build_result(request: dict, key: str, result_topic: str, producer: str, payload: dict,
                 traceparent: str | None) -> Outbound:
    """Result envelope: same entity and correlation, causation = request eventId (A.2)."""
    envelope = {
        "eventId": uuid7(),
        "eventType": result_topic,
        "eventVersion": 1,
        "occurredAt": iso_ms(utc_now()),
        "correlationId": request["correlationId"],
        "causationId": request["eventId"],
        "producer": producer,
        "entityType": request["entityType"],
        "entityId": request["entityId"],
        "payload": payload,
    }
    headers = {"content-type": "application/json", "ihr-schema": f"{result_topic}.v1"}
    if traceparent:
        headers["traceparent"] = traceparent
    # Results keep the request's Kafka key (A.4: same ordering scope).
    return Outbound(result_topic, key, json.dumps(envelope, separators=(",", ":")).encode("utf-8"), headers)


def worker_version() -> str:
    return os.environ.get("WORKER_VERSION", "1.0.0+dev")
