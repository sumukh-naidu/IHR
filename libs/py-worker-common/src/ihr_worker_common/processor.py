"""Per-message processing (AI Specification v1.1 §3.2 steps 1-4, 6-8).

Kafka specifics (poll, semaphore/backpressure, commit) live in runner.py; this module
turns one inbound message into the outbound messages to produce, so it can be tested
without a broker.
"""
from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from opentelemetry import trace
from opentelemetry.propagate import extract

from . import contracts
from .errors import ProcessingError, failed_payload
from .events import Outbound, build_result, parse_ts, utc_now
from .log import get_logger
from .metrics import counter, histogram
from .storage import Storage

LRU_SIZE = 50_000
SIDECAR_SUFFIX = ".meta.json"

log = get_logger("ihr.worker")
tracer = trace.get_tracer("ihr.worker")


@dataclass
class JobContext:
    envelope: dict
    payload: dict
    deadline: datetime
    storage: Storage

    @property
    def job_id(self) -> str:
        return self.payload["jobId"]

    def seconds_left(self) -> float:
        return (self.deadline - utc_now()).total_seconds()


@dataclass
class HandlerResult:
    output: dict                       # ObjectRef, several ObjectRefs, or inline object (A.3)
    run: dict                          # RunMeta
    extras: dict = field(default_factory=dict)  # inline extras of the .completed payload (A.5)


class TaskHandler(Protocol):
    task: str                    # ProcessingJobType, e.g. "CV_PARSE"
    request_topic: str           # e.g. "ihr.cv.parse.requested"
    output_schema_version: str   # e.g. "cv_profile.v1"

    def output_location(self, payload: dict) -> tuple[str, str] | None:
        """Deterministic (bucket, key) of the primary output for this jobId, or None for inline outputs."""

    def handle(self, ctx: JobContext) -> HandlerResult: ...


def result_base(request_topic: str) -> str:
    return request_topic.removesuffix(".requested")


class EventLru:
    def __init__(self, size: int = LRU_SIZE) -> None:
        self._size = size
        self._items: OrderedDict[str, None] = OrderedDict()
        self._lock = threading.Lock()

    def seen(self, event_id: str) -> bool:
        with self._lock:
            if event_id in self._items:
                self._items.move_to_end(event_id)
                return True
            return False

    def add(self, event_id: str) -> None:
        with self._lock:
            self._items[event_id] = None
            self._items.move_to_end(event_id)
            while len(self._items) > self._size:
                self._items.popitem(last=False)


class Processor:
    def __init__(self, handler: TaskHandler, storage: Storage, producer_name: str,
                 metric_prefix: str, max_workers: int = 8) -> None:
        self.handler = handler
        self.storage = storage
        self.producer_name = producer_name
        self.lru = EventLru()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=f"{handler.task}-h")
        base = result_base(handler.request_topic)
        self.completed_topic = f"{base}.completed"
        self.failed_topic = f"{base}.failed"
        self.dlq_topic = f"{handler.request_topic}.dlq"
        self._jobs = counter(f"{metric_prefix}_jobs_total", "Jobs by outcome", ("task", "outcome", "error_code"))
        self._duration = histogram(f"{metric_prefix}_job_duration_seconds", "Handler time", ("task",))

    # ------------------------------------------------------------------ public

    def process(self, raw: bytes, kafka_key: str, headers: dict[str, str]) -> list[Outbound]:
        # Step 1: parse + validate envelope and topic schema; invalid -> DLQ.
        try:
            envelope = json.loads(raw)
            contracts.validate_envelope(envelope)
            if envelope["eventType"] != self.handler.request_topic:
                raise contracts.ContractError([f"eventType {envelope['eventType']} on wrong topic"])
            payload = envelope["payload"]
        except (ValueError, contracts.ContractError) as e:
            return [self._dlq(raw, kafka_key, headers, _reason(e))]

        # UNSUPPORTED_SCHEMA_VERSION is a .failed, not a DLQ case (A.3).
        version = payload.get("outputSchemaVersion")
        if version != self.handler.output_schema_version and _has_job_fields(payload):
            return [self._failed(envelope, kafka_key, headers,
                                 ProcessingError("UNSUPPORTED_SCHEMA_VERSION",
                                                 "Worker does not implement the requested output schema version."))]
        try:
            contracts.validate_payload(self.handler.request_topic, payload)
        except contracts.ContractError as e:
            return [self._dlq(raw, kafka_key, headers, _reason(e))]

        bind = {"eventId": envelope["eventId"], "jobId": payload["jobId"], "task": self.handler.task,
                "entityId": envelope["entityId"], "attempt": payload["attempt"],
                "correlationId": envelope["correlationId"]}

        # Step 2: duplicate eventId.
        if self.lru.seen(envelope["eventId"]):
            log.info("duplicate_event_skipped", **bind)
            return []

        # Step 3: deadline already passed -> drop with DEADLINE_EXCEEDED.
        deadline = parse_ts(payload["deadlineAt"])
        if deadline <= utc_now():
            out = [self._failed(envelope, kafka_key, headers,
                                ProcessingError("DEADLINE_EXCEEDED", "Deadline passed before processing started."))]
            self._count("failed", "DEADLINE_EXCEEDED")
            self.lru.add(envelope["eventId"])
            return out

        # Step 4: idempotency on the deterministic output object + sidecar.
        replay = self._replay(envelope, payload, kafka_key, headers)
        if replay is not None:
            log.info("idempotent_replay", **bind)
            self.lru.add(envelope["eventId"])
            return [replay]

        # Steps 6-8.
        start = time.monotonic()
        ctx = JobContext(envelope, payload, deadline, self.storage)
        with tracer.start_as_current_span(f"{self.handler.task}.handle", context=extract(headers)):
            try:
                result = self._run_with_timeout(ctx)
                out = self._completed(envelope, payload, kafka_key, headers, result)
                self._count("succeeded", "")
                log.info("job_succeeded", durationMs=int((time.monotonic() - start) * 1000), **bind)
            except ProcessingError as err:
                out = self._failed(envelope, kafka_key, headers, err)
                self._count("failed", err.code)
                log.warning("job_failed", errorCode=err.code,
                            durationMs=int((time.monotonic() - start) * 1000), **bind)
            except Exception:  # bug -> INTERNAL_ERROR (retryable, alert fires)
                err = ProcessingError("INTERNAL_ERROR", "Unexpected worker error.")
                out = self._failed(envelope, kafka_key, headers, err)
                self._count("failed", err.code)
                log.error("job_internal_error", errorCode=err.code, **bind)
        self._duration.labels(self.handler.task).observe(time.monotonic() - start)
        self.lru.add(envelope["eventId"])
        return [out]

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=True)

    # ------------------------------------------------------------------ internals

    def _run_with_timeout(self, ctx: JobContext) -> HandlerResult:
        # Step 6: handler in a worker thread with timeout deadlineAt - now.
        future = self._executor.submit(self.handler.handle, ctx)
        try:
            return future.result(timeout=max(ctx.seconds_left(), 0.0))
        except FutureTimeout:
            future.cancel()
            raise ProcessingError("DEADLINE_EXCEEDED", "Work could not finish before the deadline.") from None

    def _replay(self, envelope: dict, payload: dict, key: str, headers: dict) -> Outbound | None:
        loc = self.handler.output_location(payload)
        if loc is None:
            return None
        bucket, obj_key = loc
        if not (self.storage.exists(bucket, obj_key) and self.storage.exists(bucket, obj_key + SIDECAR_SUFFIX)):
            return None
        meta = json.loads(self.storage.get_bytes(bucket, obj_key + SIDECAR_SUFFIX))
        result = HandlerResult(output=meta["output"], run=meta["run"], extras=meta.get("extras", {}))
        return self._completed(envelope, payload, key, headers, result, write_sidecar=False)

    def _completed(self, envelope: dict, payload: dict, key: str, headers: dict,
                   result: HandlerResult, write_sidecar: bool = True) -> Outbound:
        body = {
            "jobId": payload["jobId"],
            "attempt": payload["attempt"],
            "output": result.output,
            "outputSchemaVersion": self.handler.output_schema_version,
            "run": result.run,
            **result.extras,
        }
        # Producers validate before publishing (A.1). A failure here is our bug.
        try:
            contracts.validate_payload(self.completed_topic, body)
        except contracts.ContractError:
            raise ProcessingError("INTERNAL_ERROR", "Worker produced a result that fails its own contract.") from None
        # Step 7: sidecar {key}.meta.json (RunMeta + inline extras) enables step-4 replay.
        loc = self.handler.output_location(payload)
        if write_sidecar and loc is not None:
            bucket, obj_key = loc
            sidecar = json.dumps({"output": result.output, "run": result.run, "extras": result.extras}).encode()
            self.storage.put_bytes(bucket, obj_key + SIDECAR_SUFFIX, sidecar, "application/json")
        return build_result(envelope, key, self.completed_topic, self.producer_name, body, headers.get("traceparent"))

    def _failed(self, envelope: dict, key: str, headers: dict, err: ProcessingError) -> Outbound:
        p = envelope["payload"]
        body = failed_payload(p["jobId"], p["attempt"], err)
        contracts.validate_payload(self.failed_topic, body)
        return build_result(envelope, key, self.failed_topic, self.producer_name, body, headers.get("traceparent"))

    def _dlq(self, raw: bytes, key: str, headers: dict, reason: str) -> Outbound:
        self._count("dead_lettered", "")
        log.warning("message_dead_lettered", task=self.handler.task, reason=reason[:200])
        return Outbound(self.dlq_topic, key, raw, {**headers, "ihr-dlq-reason": reason[:500]})

    def _count(self, outcome: str, code: str) -> None:
        self._jobs.labels(self.handler.task, outcome, code).inc()


def _has_job_fields(payload: dict) -> bool:
    return isinstance(payload.get("jobId"), str) and isinstance(payload.get("attempt"), int)


def _reason(e: Exception) -> str:
    # Schema messages may quote values; keep only paths/messages, truncated.
    if isinstance(e, contracts.ContractError):
        return "schema: " + " | ".join(err.split(":")[0] for err in e.errors[:5])
    return "unparseable JSON"
