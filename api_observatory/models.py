"""Shared data helpers for snapshots and evidence-pointed incidents."""

from __future__ import annotations

import hashlib
import json
from typing import Any


HONESTY = (
    "LOCAL FIXTURE DEMO: all monitoring shown is against a synthetic local fixture server; "
    "no real production API is monitored."
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def percentile(values: list[int], fraction: float) -> int:
    """Return a deterministic nearest-rank percentile for an ordered sample."""
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) * fraction + 0.999999)) - 1))
    return ordered[index]


def make_incident(
    *, endpoint: str, detector: str, severity: str, field_path: str,
    before: Any, after: Any, timestamp: str, measurement: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a stable incident ID from the exact response diff."""
    identity = {
        "endpoint": endpoint,
        "detector": detector,
        "field_path": field_path,
        "before": before,
        "after": after,
    }
    incident_id = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()[:20]
    incident: dict[str, Any] = {
        "id": incident_id,
        "timestamp": timestamp,
        "endpoint": endpoint,
        "detector": detector,
        "severity": severity,
        "diff": {"field_path": field_path, "before": before, "after": after},
    }
    if measurement:
        incident["measurement"] = measurement
    return incident
