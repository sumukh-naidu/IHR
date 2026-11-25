"""Retry policy (A-08), circuit breaker (A-08) and client-side rate limit (A-09)."""
from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from typing import Callable

from ihr_worker_common.metrics import gauge

Clock = Callable[[], float]

BACKOFF_S = (1.0, 4.0)
JITTER = 0.20
MAX_RETRY_AFTER_S = 20.0
DEADLINE_MARGIN_S = 2.0
REALTIME_MIN_REMAINING_S = 4.0


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int
    min_remaining_s: float = 0.0  # realtime: only retry if >= 4 s remain

    @classmethod
    def for_slot(cls, slot: str) -> "RetryPolicy":
        return cls(1, REALTIME_MIN_REMAINING_S) if slot == "realtime" else cls(2)

    def next_delay(self, retries_done: int, remaining_s: float, retry_after_s: float | None,
                   rng: random.Random | None = None) -> float | None:
        """Delay before the next attempt, or None when no retry is allowed."""
        if retries_done >= self.max_retries:
            return None
        if remaining_s < self.min_remaining_s:
            return None
        if retry_after_s is not None:
            delay = min(retry_after_s, MAX_RETRY_AFTER_S)
        else:
            r = (rng or random).uniform(-JITTER, JITTER)
            delay = BACKOFF_S[min(retries_done, len(BACKOFF_S) - 1)] * (1 + r)
        # never beyond deadlineAt - 2 s
        if delay >= remaining_s - DEADLINE_MARGIN_S:
            return None
        return delay


class CircuitBreaker:
    """Opens after 5 consecutive failures, stays open 30 s, then half-open with 1 trial call."""

    def __init__(self, name: str, failure_threshold: int = 5, open_seconds: float = 30.0,
                 clock: Clock = time.monotonic) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.open_seconds = open_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: float | None = None
        self._trial_in_flight = False
        self._gauge = gauge("ihr_provider_circuit_open", "Circuit state (0/1)", ("provider",))
        self._gauge.labels(name).set(0)

    @property
    def state(self) -> str:
        with self._lock:
            return self._state()

    def _state(self) -> str:
        if self._opened_at is None:
            return "closed"
        if self._clock() - self._opened_at < self.open_seconds:
            return "open"
        return "half_open"

    def allow(self) -> bool:
        with self._lock:
            st = self._state()
            if st == "closed":
                return True
            if st == "half_open" and not self._trial_in_flight:
                self._trial_in_flight = True
                return True
            return False

    def success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._trial_in_flight = False
            self._gauge.labels(self.name).set(0)

    def failure(self) -> None:
        with self._lock:
            if self._trial_in_flight:  # half-open trial failed -> open again
                self._opened_at = self._clock()
                self._trial_in_flight = False
                self._gauge.labels(self.name).set(1)
                return
            self._failures += 1
            if self._failures >= self.failure_threshold and self._opened_at is None:
                self._opened_at = self._clock()
                self._gauge.labels(self.name).set(1)


class TokenBucket:
    """Requests-per-minute limiter (KIMI_RPM_LIMIT per replica)."""

    def __init__(self, rpm: int, clock: Clock = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        self.capacity = float(rpm)
        self.rate = rpm / 60.0
        self._tokens = float(rpm)
        self._clock = clock
        self._sleep = sleep
        self._last = clock()
        self._lock = threading.Lock()

    def acquire(self, max_wait_s: float) -> bool:
        end = self._clock() + max_wait_s
        while True:
            with self._lock:
                now = self._clock()
                self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return True
                wait = (1 - self._tokens) / self.rate
            if self._clock() + wait > end:
                return False
            self._sleep(wait)
