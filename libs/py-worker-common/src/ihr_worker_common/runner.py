"""Kafka consume/produce loop (§3.2) around one Processor per subscribed topic.

- one consumer per process, enable.auto.commit=false, max.poll.interval.ms=900000,
  isolation.level=read_committed
- task semaphore (A-09): when all slots are busy, assigned partitions are paused and
  resumed once a slot frees (backpressure)
- result produced (idempotent producer, acks=all) and flushed before the offset is
  committed; offsets are committed only up to the lowest unfinished offset per partition
- SIGTERM: stop polling, wait up to 30 s for in-flight work, commit finished work, exit
"""
from __future__ import annotations

import os
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .log import get_logger
from .processor import Processor

log = get_logger("ihr.runner")
SHUTDOWN_WAIT_S = 30


def kafka_base_config() -> dict:
    cfg = {"bootstrap.servers": os.environ["KAFKA_BOOTSTRAP_SERVERS"]}
    if os.environ.get("KAFKA_USERNAME"):
        cfg.update({
            "security.protocol": os.environ.get("KAFKA_SECURITY_PROTOCOL", "SASL_SSL"),
            "sasl.mechanisms": "SCRAM-SHA-512",
            "sasl.username": os.environ["KAFKA_USERNAME"],
            "sasl.password": os.environ["KAFKA_PASSWORD"],
        })
    return cfg


class OffsetTracker:
    """Tracks finished offsets per partition; commits only contiguous prefixes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[tuple[str, int], set[int]] = {}
        self._done: dict[tuple[str, int], set[int]] = {}

    def started(self, topic: str, partition: int, offset: int) -> None:
        with self._lock:
            self._pending.setdefault((topic, partition), set()).add(offset)

    def finished(self, topic: str, partition: int, offset: int) -> None:
        with self._lock:
            self._done.setdefault((topic, partition), set()).add(offset)

    def committable(self) -> dict[tuple[str, int], int]:
        """Return {(topic, partition): next_offset_to_commit} and forget committed offsets."""
        out: dict[tuple[str, int], int] = {}
        with self._lock:
            for tp, pending in self._pending.items():
                done = self._done.get(tp, set())
                highest = None
                for off in sorted(pending):
                    if off not in done:
                        break
                    highest = off
                if highest is not None:
                    out[tp] = highest + 1
                    cleared = {o for o in pending if o <= highest}
                    pending -= cleared
                    done -= cleared
        return out

    def in_flight(self) -> int:
        with self._lock:
            return sum(len(p - self._done.get(tp, set())) for tp, p in self._pending.items())

    def forget(self, topic: str, partition: int) -> None:
        with self._lock:
            self._pending.pop((topic, partition), None)
            self._done.pop((topic, partition), None)


class KafkaRunner:
    def __init__(self, processors: list[Processor], group_id: str, concurrency: int) -> None:
        from confluent_kafka import Consumer, Producer

        self.processors = {p.handler.request_topic: p for p in processors}
        self.topics = list(self.processors)
        self.concurrency = concurrency
        base = kafka_base_config()
        self.consumer = Consumer({
            **base,
            "group.id": group_id,
            "enable.auto.commit": False,
            "max.poll.interval.ms": 900000,
            "isolation.level": "read_committed",
            "auto.offset.reset": "earliest",
        })
        self.producer = Producer({**base, "enable.idempotence": True, "acks": "all"})
        self.tracker = OffsetTracker()
        self.slots = threading.Semaphore(concurrency)
        self.pool = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="job")
        self._stop = threading.Event()
        self._paused = False

    # -------------------------------------------------------------- lifecycle

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: self._stop.set())
        signal.signal(signal.SIGINT, lambda *_: self._stop.set())

    def run(self) -> None:
        self.consumer.subscribe(self.topics, on_revoke=self._on_revoke)
        log.info("runner_started", topic=",".join(self.topics))
        try:
            while not self._stop.is_set():
                self._commit()
                self._apply_backpressure()
                msg = self.consumer.poll(0.5)
                if msg is None:
                    continue
                if msg.error():
                    log.warning("kafka_error", reason=str(msg.error())[:200])
                    continue
                if not self.slots.acquire(blocking=False):
                    # Raced with pause: rewind so the message is re-polled after resume.
                    self.consumer.seek(msg)
                    continue
                self.tracker.started(msg.topic(), msg.partition(), msg.offset())
                self.pool.submit(self._handle, msg)
        finally:
            self._shutdown()

    def _shutdown(self) -> None:
        deadline = time.monotonic() + SHUTDOWN_WAIT_S
        while self.tracker.in_flight() and time.monotonic() < deadline:
            time.sleep(0.2)
        self._commit()
        self.pool.shutdown(wait=False, cancel_futures=True)
        for p in self.processors.values():
            p.shutdown(wait=False)
        self.producer.flush(5)
        self.consumer.close()
        log.info("runner_stopped", topic=",".join(self.topics))

    # -------------------------------------------------------------- internals

    def _handle(self, msg) -> None:
        try:
            headers = {k: (v.decode() if isinstance(v, bytes) else v) for k, v in (msg.headers() or [])}
            key = msg.key().decode() if msg.key() else ""
            outbound = self.processors[msg.topic()].process(msg.value(), key, headers)
            for out in outbound:
                self.producer.produce(out.topic, key=out.key, value=out.value, headers=list(out.headers.items()))
            if self.producer.flush(30) != 0:
                # Not delivered: do not commit; message is redelivered after restart.
                log.error("produce_flush_incomplete", topic=msg.topic())
                self._stop.set()
                return
            self.tracker.finished(msg.topic(), msg.partition(), msg.offset())
        except Exception:
            log.error("handle_crashed", topic=msg.topic())
            self._stop.set()  # leave uncommitted -> redelivered
        finally:
            self.slots.release()

    def _apply_backpressure(self) -> None:
        busy = self.tracker.in_flight() >= self.concurrency
        assignment = self.consumer.assignment()
        if busy and not self._paused and assignment:
            self.consumer.pause(assignment)
            self._paused = True
        elif not busy and self._paused:
            self.consumer.resume(assignment)
            self._paused = False

    def _commit(self) -> None:
        from confluent_kafka import TopicPartition

        offsets = self.tracker.committable()
        if offsets:
            self.consumer.commit(offsets=[TopicPartition(t, p, o) for (t, p), o in offsets.items()],
                                 asynchronous=False)

    def _on_revoke(self, consumer, partitions) -> None:
        self._commit()
        for tp in partitions:
            self.tracker.forget(tp.topic, tp.partition)
