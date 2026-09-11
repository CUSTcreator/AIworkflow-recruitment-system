from __future__ import annotations

from collections import Counter
from threading import Lock
from time import perf_counter
from typing import Iterator
from contextlib import contextmanager


class InMemoryMetrics:
    """Small Prometheus-compatible text exporter without a mandatory runtime dependency."""

    def __init__(self) -> None:
        self._counters: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()
        self._latencies: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._lock = Lock()

    def increment(self, name: str, **labels: str | int) -> None:
        key = (name, tuple(sorted((key, str(value)) for key, value in labels.items())))
        with self._lock:
            self._counters[key] += 1

    def set_gauge(self, name: str, value: float | int, **labels: str | int) -> None:
        """记录采样时刻的瞬时值，例如队列积压和过期租约数量。"""
        key = (name, tuple(sorted((key, str(value)) for key, value in labels.items())))
        with self._lock:
            self._gauges[key] = float(value)

    @contextmanager
    def measure(self, name: str, **labels: str | int) -> Iterator[None]:
        started = perf_counter()
        try:
            yield
        finally:
            key = (name, tuple(sorted((key, str(value)) for key, value in labels.items())))
            with self._lock:
                self._latencies[key] += perf_counter() - started

    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            for (name, labels), value in sorted(self._counters.items()):
                lines.append(f"{name}_total{_labels(labels)} {value}")
            for (name, labels), value in sorted(self._latencies.items()):
                lines.append(f"{name}_duration_seconds_sum{_labels(labels)} {value:.6f}")
            for (name, labels), value in sorted(self._gauges.items()):
                lines.append(f"{name}{_labels(labels)} {value:.6f}")
        return "\n".join(lines) + ("\n" if lines else "")


def _labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    escaped = ",".join(f'{key}="{value.replace(chr(34), chr(92) + chr(34))}"' for key, value in labels)
    return "{" + escaped + "}"


metrics = InMemoryMetrics()
