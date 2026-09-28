"""Deterministic Markdown, HTML, and JSON health reports."""

from __future__ import annotations

import html
import json
from collections import Counter
from typing import Any

from .models import HONESTY, canonical_json
from .storage import load_baselines, load_incidents


DETECTORS = ("schema_drift", "latency_spike", "breaking_response", "auth_failure", "behavioral_drift")
FIXED_REPORT_TIME = "2026-09-28T00:00:00Z"


def report_data() -> dict[str, Any]:
    baselines = load_baselines()
    incidents = sorted(load_incidents(), key=lambda item: (item.get("timestamp", ""), item.get("id", "")))
    counts = Counter(item.get("detector", "unknown") for item in incidents)
    endpoints = []
    for endpoint, baseline in sorted(baselines.items()):
        endpoint_incidents = [item for item in incidents if item.get("endpoint") == endpoint]
        endpoints.append({
            "endpoint": endpoint,
            "health": "incident" if endpoint_incidents else "healthy",
            "baseline_status": baseline.get("status_code"),
            "incident_count": len(endpoint_incidents),
        })
    timestamps = [item.get("timestamp") for item in incidents if item.get("timestamp")]
    baseline_times = [item.get("captured_at") for item in baselines.values() if item.get("captured_at")]
    generated_at = max(timestamps or baseline_times or [FIXED_REPORT_TIME])
    return {
        "demo_notice": HONESTY,
        "generated_at": generated_at,
        "endpoints": endpoints,
        "drift_counts": {detector: counts.get(detector, 0) for detector in DETECTORS},
        "incidents": incidents,
    }


def render_report(format_name: str) -> str:
    data = report_data()
    if format_name == "json":
        return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    if format_name == "md":
        lines = [
            "# API Observatory report",
            "",
            f"> **{data['demo_notice']}**",
            "",
            f"Generated: {data['generated_at']}",
            "",
            "## Endpoint health",
            "",
            "| Endpoint | Health | Baseline status | Incidents |",
            "| --- | --- | ---: | ---: |",
        ]
        for endpoint in data["endpoints"]:
            lines.append(f"| `{endpoint['endpoint']}` | {endpoint['health']} | {endpoint['baseline_status']} | {endpoint['incident_count']} |")
        lines.extend(["", "## Drift counts", ""])
        for detector, count in data["drift_counts"].items():
            lines.append(f"- {detector}: {count}")
        lines.extend(["", "## Incidents", ""])
        if not data["incidents"]:
            lines.append("No incidents recorded.")
        for incident in data["incidents"]:
            diff = incident["diff"]
            lines.extend([
                f"### {incident['detector']} ({incident['severity']})",
                "",
                f"- ID: `{incident['id']}`",
                f"- Time: {incident['timestamp']}",
                f"- Endpoint: `{incident['endpoint']}`",
                f"- Field path: `{diff['field_path']}`",
                f"- Before: `{canonical_json(diff['before'])}`",
                f"- After: `{canonical_json(diff['after'])}`",
                "",
            ])
        return "\n".join(lines).rstrip() + "\n"
    if format_name == "html":
        incident_rows = []
        for incident in data["incidents"]:
            diff = incident["diff"]
            incident_rows.append(
                "<tr>"
                f"<td>{html.escape(incident['timestamp'])}</td>"
                f"<td>{html.escape(incident['detector'])}</td>"
                f"<td>{html.escape(incident['severity'])}</td>"
                f"<td>{html.escape(incident['endpoint'])}</td>"
                f"<td><code>{html.escape(diff['field_path'])}</code></td>"
                f"<td><code>{html.escape(canonical_json(diff['before']))}</code></td>"
                f"<td><code>{html.escape(canonical_json(diff['after']))}</code></td>"
                "</tr>"
            )
        if not incident_rows:
            incident_rows.append('<tr><td colspan="7">No incidents recorded.</td></tr>')
        endpoint_rows = []
        for endpoint in data["endpoints"]:
            endpoint_rows.append(
                "<tr>"
                f"<td><code>{html.escape(endpoint['endpoint'])}</code></td>"
                f"<td>{html.escape(endpoint['health'])}</td>"
                f"<td>{html.escape(str(endpoint['baseline_status']))}</td>"
                f"<td>{endpoint['incident_count']}</td>"
                "</tr>"
            )
        count_rows = "".join(
            f"<li>{html.escape(detector)}: {count}</li>" for detector, count in data["drift_counts"].items()
        )
        return "\n".join([
            "<!doctype html>",
            '<html lang="en"><head><meta charset="utf-8"><title>API Observatory report</title>',
            "<style>body{font:16px system-ui,sans-serif;margin:2rem;color:#172033}"
            ".banner{padding:1rem;background:#fff1c2;border:1px solid #d7a928;font-weight:700}"
            "table{border-collapse:collapse;width:100%;margin:1rem 0 2rem}th,td{border:1px solid #ccd2dc;padding:.55rem;text-align:left}"
            "code{overflow-wrap:anywhere}</style></head><body>",
            "<h1>API Observatory report</h1>",
            f'<p class="banner">{html.escape(data["demo_notice"])}</p>',
            f"<p>Generated: {html.escape(data['generated_at'])}</p>",
            "<h2>Endpoint health</h2><table><thead><tr><th>Endpoint</th><th>Health</th><th>Baseline status</th><th>Incidents</th></tr></thead>",
            f"<tbody>{''.join(endpoint_rows)}</tbody></table>",
            f"<h2>Drift counts</h2><ul>{count_rows}</ul>",
            "<h2>Incidents and exact response diffs</h2><table><thead><tr><th>Time</th><th>Detector</th><th>Severity</th><th>Endpoint</th><th>Field path</th><th>Before</th><th>After</th></tr></thead>",
            f"<tbody>{''.join(incident_rows)}</tbody></table>",
            "</body></html>",
            "",
        ])
    raise ValueError("format must be md, html, or json")
