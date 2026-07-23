"""Lightweight in-process counters for run lifecycle rollout visibility.

These counters are intentionally simple and dependency-free so they can be
used before a full Prometheus/OpenTelemetry integration is finalized.
"""

from __future__ import annotations

from collections import Counter
from threading import Lock

_COUNTERS: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()
_LOCK = Lock()


def increment_counter(name: str, **labels: object) -> None:
    """Increment a named counter with optional stringified labels."""
    key = (
        name,
        tuple(sorted((k, str(v)) for k, v in labels.items())),
    )
    with _LOCK:
        _COUNTERS[key] += 1


def snapshot_counters() -> dict[str, int]:
    """Return a flattened snapshot suitable for logs/debug endpoints."""
    out: dict[str, int] = {}
    with _LOCK:
        for (name, labels), count in _COUNTERS.items():
            if labels:
                suffix = ",".join(f"{k}={v}" for k, v in labels)
                out[f"{name}{{{suffix}}}"] = count
            else:
                out[name] = count
    return out
