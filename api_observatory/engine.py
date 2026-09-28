"""Baseline capture and named drift detectors over real local HTTP responses."""

from __future__ import annotations

import json
from typing import Any

from .http_client import HTTPResponse, request, validate_local_url
from .models import make_incident, percentile
from .storage import append_incidents_idempotently, load_baselines, save_baseline


INTERESTING_HEADERS = ("content-type", "x-api-version")
MISSING = "<missing>"


def json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def inspect_schema(value: Any) -> dict[str, Any]:
    """Describe JSON key paths, observed types, and required object keys."""
    paths: dict[str, str] = {}
    required: dict[str, list[str]] = {}

    def visit(node: Any, path: str) -> None:
        kind = json_type(node)
        paths[path] = kind
        if isinstance(node, dict):
            required[path] = sorted(node)
            for key in sorted(node):
                visit(node[key], f"{path}.{key}")
        elif isinstance(node, list) and node:
            visit(node[0], f"{path}[]")

    visit(value, "$")
    return {"paths": dict(sorted(paths.items())), "required": dict(sorted(required.items()))}


def _capture(endpoint: str, samples: int) -> tuple[list[HTTPResponse], Any, bool]:
    validate_local_url(endpoint)
    responses = [request(endpoint) for _ in range(samples)]
    last = responses[-1]
    try:
        body = last.json()
        is_json = True
    except (UnicodeDecodeError, json.JSONDecodeError):
        body = last.body.decode("utf-8", errors="replace")
        is_json = False
    return responses, body, is_json


def capture_baseline(endpoint: str, *, samples: int = 5) -> dict[str, Any]:
    if samples < 1:
        raise ValueError("samples must be at least 1")
    # Warm the local server's request handler so startup cost does not pollute its distribution.
    request(endpoint)
    responses, body, is_json = _capture(endpoint, samples)
    final = responses[-1]
    selected_headers = {key: final.headers.get(key, "") for key in INTERESTING_HEADERS}
    latency_samples = [response.latency_ms for response in responses]
    snapshot = {
        "endpoint": endpoint,
        "captured_at": final.headers.get("x-fixture-time", "1970-01-01T00:00:00Z"),
        "status_code": final.status_code,
        "content_type": final.content_type,
        "headers": selected_headers,
        "is_json": is_json,
        "body": body,
        "schema": inspect_schema(body) if is_json else {"paths": {}, "required": {}},
        "latency_samples_ms": latency_samples,
        "latency_p50_ms": percentile(latency_samples, 0.50),
        "latency_p95_ms": percentile(latency_samples, 0.95),
    }
    save_baseline(snapshot)
    return snapshot


def _actual_path(path: str) -> str:
    return path.replace("[]", "[0]")


def _value_at(body: Any, path: str) -> Any:
    actual = _actual_path(path)
    if actual == "$":
        return body
    cursor = body
    tokens = actual[2:] if actual.startswith("$.") else actual[1:]
    for part in tokens.replace("[", ".").replace("]", "").split("."):
        if not part:
            continue
        if isinstance(cursor, list):
            try:
                cursor = cursor[int(part)]
            except (ValueError, IndexError):
                return MISSING
        elif isinstance(cursor, dict) and part in cursor:
            cursor = cursor[part]
        else:
            return MISSING
    return cursor


def _schema_incidents(endpoint: str, before: dict[str, Any], after: dict[str, Any], timestamp: str) -> list[dict[str, Any]]:
    old_paths = before.get("schema", {}).get("paths", {})
    new_paths = after.get("schema", {}).get("paths", {})
    differences: list[dict[str, Any]] = []
    for path in sorted(set(old_paths) | set(new_paths)):
        if old_paths.get(path) != new_paths.get(path):
            old_value = _value_at(before.get("body"), path) if path in old_paths else MISSING
            new_value = _value_at(after.get("body"), path) if path in new_paths else MISSING
            differences.append(make_incident(
                endpoint=endpoint, detector="schema_drift", severity="medium",
                field_path=_actual_path(path), before=old_value, after=new_value, timestamp=timestamp,
                measurement={"type_before": old_paths.get(path, MISSING), "type_after": new_paths.get(path, MISSING)},
            ))
    old_required = before.get("schema", {}).get("required", {})
    new_required = after.get("schema", {}).get("required", {})
    for path in sorted(set(old_required) | set(new_required)):
        if old_required.get(path) != new_required.get(path):
            differences.append(make_incident(
                endpoint=endpoint, detector="schema_drift", severity="medium",
                field_path=f"{_actual_path(path)}.$required",
                before=old_required.get(path, MISSING), after=new_required.get(path, MISSING), timestamp=timestamp,
            ))
    return differences


def _behavioral_differences(before: Any, after: Any, path: str = "$") -> list[tuple[str, Any, Any]]:
    differences: list[tuple[str, Any, Any]] = []
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) & set(after)):
            differences.extend(_behavioral_differences(before[key], after[key], f"{path}.{key}"))
    elif isinstance(before, list) and isinstance(after, list):
        common = min(len(before), len(after))
        for index in range(common):
            differences.extend(_behavioral_differences(before[index], after[index], f"{path}[{index}]"))
        if len(before) != len(after):
            differences.append((f"{path}.length", len(before), len(after)))
    elif before != after:
        differences.append((path, before, after))
    return differences


def _timestamp(response: HTTPResponse) -> str:
    return response.headers.get("x-fixture-time") or "1970-01-01T00:00:00Z"


def _make_response_incident(
    endpoint: str, detector: str, severity: str, path: str,
    before: Any, after: Any, timestamp: str, measurement: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return make_incident(
        endpoint=endpoint, detector=detector, severity=severity,
        field_path=path, before=before, after=after, timestamp=timestamp, measurement=measurement,
    )


def check_endpoint(endpoint: str, *, samples: int = 1, latency_threshold_ms: int = 150) -> dict[str, Any]:
    if samples < 1:
        raise ValueError("samples must be at least 1")
    baseline = load_baselines().get(endpoint)
    if baseline is None:
        raise ValueError(f"no baseline exists for {endpoint}; run obs baseline first")
    responses, body, is_json = _capture(endpoint, samples)
    final = responses[-1]
    timestamp = _timestamp(final)
    current_latency = [response.latency_ms for response in responses]
    current_p50 = percentile(current_latency, 0.50)
    current_p95 = percentile(current_latency, 0.95)
    incidents: list[dict[str, Any]] = []
    detector_states: dict[str, str] = {}
    detector_measurements: dict[str, dict[str, Any]] = {}

    # Auth failures have their own detector and take precedence over generic status drift.
    if baseline["status_code"] == 200 and final.status_code in (401, 403):
        incidents.append(_make_response_incident(
            endpoint, "auth_failure", "critical", "$response.status_code",
            baseline["status_code"], final.status_code, timestamp,
        ))
        detector_states["auth_failure"] = "incident"
    else:
        detector_states["auth_failure"] = "pass"
    detector_measurements["auth_failure"] = {
        "baseline_status_code": baseline["status_code"],
        "current_status_code": final.status_code,
    }

    breaking = False
    if final.status_code != baseline["status_code"] and final.status_code not in (401, 403):
        incidents.append(_make_response_incident(
            endpoint, "breaking_response", "high", "$response.status_code",
            baseline["status_code"], final.status_code, timestamp,
        ))
        breaking = True
    elif final.content_type != baseline["content_type"]:
        incidents.append(_make_response_incident(
            endpoint, "breaking_response", "high", "$response.content_type",
            baseline["content_type"], final.content_type, timestamp,
        ))
        breaking = True
    elif not is_json:
        incidents.append(_make_response_incident(
            endpoint, "breaking_response", "high", "$response.body",
            baseline.get("body"), body, timestamp,
        ))
        breaking = True
    if not breaking and not incidents:
        for header in INTERESTING_HEADERS[1:]:
            old_value = baseline.get("headers", {}).get(header, "")
            new_value = final.headers.get(header, "")
            if old_value != new_value:
                incidents.append(_make_response_incident(
                    endpoint, "breaking_response", "high", f"$response.headers.{header}",
                    old_value, new_value, timestamp,
                ))
                breaking = True
                break
    detector_states["breaking_response"] = "incident" if breaking else "pass"
    detector_measurements["breaking_response"] = {
        "baseline_status_code": baseline["status_code"],
        "current_status_code": final.status_code,
        "baseline_content_type": baseline["content_type"],
        "current_content_type": final.content_type,
        "current_is_json": is_json,
    }

    response_is_usable = final.status_code == baseline["status_code"] and is_json and baseline.get("is_json", False)
    schema_incidents: list[dict[str, Any]] = []
    if response_is_usable:
        schema_incidents = _schema_incidents(endpoint, baseline, {
            "schema": inspect_schema(body), "body": body,
        }, timestamp)
    if incidents and incidents[0]["detector"] in ("auth_failure", "breaking_response"):
        schema_incidents = []
    incidents.extend(schema_incidents)
    detector_states["schema_drift"] = "incident" if schema_incidents else "pass"
    detector_measurements["schema_drift"] = {
        "baseline_path_count": len(baseline.get("schema", {}).get("paths", {})),
        "current_path_count": len(inspect_schema(body).get("paths", {})) if is_json else 0,
        "changes": [incident["diff"] for incident in schema_incidents],
    }

    behavior_incidents: list[dict[str, Any]] = []
    behavior_differences: list[dict[str, Any]] = []
    changed_schema_paths = {
        incident["diff"]["field_path"].replace("[0]", "[]")
        for incident in schema_incidents
    }
    if response_is_usable and not (incidents and incidents[0]["detector"] in ("auth_failure", "breaking_response")):
        for path, old_value, new_value in _behavioral_differences(baseline.get("body"), body):
            if path.replace("[0]", "[]") in changed_schema_paths:
                continue
            behavior_differences.append({"field_path": path, "before": old_value, "after": new_value})
            behavior_incidents.append(_make_response_incident(
                endpoint, "behavioral_drift", "medium", path,
                old_value, new_value, timestamp,
            ))
    incidents.extend(behavior_incidents)
    detector_states["behavioral_drift"] = "incident" if behavior_incidents else "pass"
    detector_measurements["behavioral_drift"] = {"changed_values": behavior_differences}

    base_p50 = int(baseline.get("latency_p50_ms", 0))
    base_p95 = int(baseline.get("latency_p95_ms", 0))
    p50_delta = current_p50 - base_p50
    p95_delta = current_p95 - base_p95
    latency_incident = p50_delta >= latency_threshold_ms or p95_delta >= latency_threshold_ms
    if latency_incident:
        path = "$latency.p50_ms" if p50_delta >= latency_threshold_ms else "$latency.p95_ms"
        old_value = base_p50 if path.endswith("p50_ms") else base_p95
        new_value = current_p50 if path.endswith("p50_ms") else current_p95
        incidents.append(_make_response_incident(
            endpoint, "latency_spike", "high", path, old_value, new_value, timestamp,
            measurement={
                "p50_before_ms": base_p50, "p50_after_ms": current_p50, "p50_delta_ms": p50_delta,
                "p95_before_ms": base_p95, "p95_after_ms": current_p95, "p95_delta_ms": p95_delta,
                "threshold_ms": latency_threshold_ms,
            },
        ))
    detector_states["latency_spike"] = "incident" if latency_incident else "pass"
    detector_measurements["latency_spike"] = {
        "p50_before_ms": base_p50,
        "p50_after_ms": current_p50,
        "p50_delta_ms": p50_delta,
        "p95_before_ms": base_p95,
        "p95_after_ms": current_p95,
        "p95_delta_ms": p95_delta,
        "threshold_ms": latency_threshold_ms,
    }

    new_incidents = append_incidents_idempotently(incidents)
    return {
        "endpoint": endpoint,
        "detectors": detector_states,
        "detector_results": {
            name: {"status": detector_states[name], "measured": detector_measurements[name]}
            for name in sorted(detector_states)
        },
        "incidents": incidents,
        "new_incidents": new_incidents,
        "latency": {"samples_ms": current_latency, "p50_ms": current_p50, "p95_ms": current_p95},
    }


def check_all(*, samples: int = 1, latency_threshold_ms: int = 150) -> list[dict[str, Any]]:
    endpoints = sorted(load_baselines())
    if not endpoints:
        raise ValueError("no baselines exist; run obs baseline <endpoint> first")
    return [check_endpoint(endpoint, samples=samples, latency_threshold_ms=latency_threshold_ms) for endpoint in endpoints]
