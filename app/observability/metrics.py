from __future__ import annotations

from dataclasses import dataclass

from fastapi.responses import Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)


@dataclass(frozen=True)
class RequestMetrics:
    endpoint: str
    method: str
    status_code: int
    latency_ms: int
    client_id: int | None


class Metrics:
    def __init__(self) -> None:
        self._registry = CollectorRegistry()

        self._requests_total = Counter(
            "fcam_requests_total",
            "Total HTTP requests processed by FCAM",
            labelnames=("endpoint", "method", "status_code", "client_id"),
            registry=self._registry,
        )
        self._request_duration_ms = Histogram(
            "fcam_request_duration_ms",
            "HTTP request duration in milliseconds",
            labelnames=("endpoint", "method"),
            buckets=(25, 50, 100, 250, 500, 1000, 2000, 5000, 10000),
            registry=self._registry,
        )
        self._key_selected_total = Counter(
            "fcam_key_selected_total",
            "Total selected Firecrawl keys",
            labelnames=("key_id",),
            registry=self._registry,
        )
        self._key_cooldown_total = Counter(
            "fcam_key_cooldown_total",
            "Total key cooldown events (429)",
            labelnames=("key_id",),
            registry=self._registry,
        )
        self._quota_remaining = Gauge(
            "fcam_quota_remaining",
            "Remaining daily quota",
            labelnames=("scope", "id"),
            registry=self._registry,
        )

    def record_request(self, m: RequestMetrics) -> None:
        client_label = str(m.client_id) if m.client_id is not None else "unknown"
        self._requests_total.labels(
            endpoint=m.endpoint,
            method=m.method,
            status_code=str(int(m.status_code)),
            client_id=client_label,
        ).inc()
        self._request_duration_ms.labels(endpoint=m.endpoint, method=m.method).observe(float(m.latency_ms))

    def record_key_selected(self, key_id: int) -> None:
        self._key_selected_total.labels(key_id=str(int(key_id))).inc()

    def record_key_cooldown(self, key_id: int) -> None:
        self._key_cooldown_total.labels(key_id=str(int(key_id))).inc()

    def set_quota_remaining(self, *, scope: str, id: int, remaining: int) -> None:
        self._quota_remaining.labels(scope=scope, id=str(int(id))).set(float(remaining))

    def render(self) -> Response:
        payload = generate_latest(self._registry)
        return Response(content=payload, media_type=CONTENT_TYPE_LATEST)
