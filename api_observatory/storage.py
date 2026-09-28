"""Local persistence for endpoint baselines and idempotent incident records."""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Any

from .models import json_bytes


_LOCK = RLock()


def state_dir() -> Path:
    configured = os.environ.get("API_OBSERVATORY_HOME")
    path = Path(configured).expanduser() if configured else Path.cwd() / ".api-observatory"
    path.mkdir(parents=True, exist_ok=True)
    return path


def baseline_path() -> Path:
    return state_dir() / "baselines.json"


def incident_path() -> Path:
    return state_dir() / "ao-incidents.jsonl"


def load_baselines() -> dict[str, dict[str, Any]]:
    path = baseline_path()
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data.get("endpoints", {})


def save_baseline(snapshot: dict[str, Any]) -> None:
    with _LOCK:
        endpoints = load_baselines()
        endpoints[snapshot["endpoint"]] = snapshot
        payload = {"endpoints": dict(sorted(endpoints.items()))}
        baseline_path().write_bytes(json_bytes(payload))


def load_incidents() -> list[dict[str, Any]]:
    path = incident_path()
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    return records


def append_incidents_idempotently(incidents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Append only unseen incident IDs and return the newly written records."""
    if not incidents:
        return []
    with _LOCK:
        path = incident_path()
        existing_ids = {record["id"] for record in load_incidents()}
        new_records = [record for record in incidents if record["id"] not in existing_ids]
        if new_records:
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                for record in new_records:
                    handle.write(json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
                    handle.write("\n")
        return new_records
