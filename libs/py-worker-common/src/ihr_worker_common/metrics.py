"""Prometheus metrics registry helpers (§12.1). Labels never carry PII or text."""
from __future__ import annotations

import threading

from prometheus_client import Counter, Gauge, Histogram

_lock = threading.Lock()
_metrics: dict[str, object] = {}


def _get(kind, name: str, doc: str, labels: tuple[str, ...]):
    with _lock:
        if name not in _metrics:
            _metrics[name] = kind(name, doc, labels)
        return _metrics[name]


def counter(name: str, doc: str, labels: tuple[str, ...] = ()) -> Counter:
    return _get(Counter, name, doc, labels)


def histogram(name: str, doc: str, labels: tuple[str, ...] = ()) -> Histogram:
    return _get(Histogram, name, doc, labels)


def gauge(name: str, doc: str, labels: tuple[str, ...] = ()) -> Gauge:
    return _get(Gauge, name, doc, labels)
