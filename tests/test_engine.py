from __future__ import annotations

import json

import pytest

from api_observatory.engine import capture_baseline, check_endpoint
from api_observatory.fixtures import SCENARIOS
from api_observatory.http_client import LocalEndpointError
from api_observatory.storage import load_incidents


@pytest.fixture(autouse=True)
def state(isolated_state):
    return isolated_state


def setup_baseline(server):
    return capture_baseline(server.url + "/api/items", samples=5)


def test_repeated_baseline_snapshot_is_identical(fixture_server):
    first = capture_baseline(fixture_server.url + "/api/items", samples=5)
    second = capture_baseline(fixture_server.url + "/api/items", samples=5)
    assert first == second
    assert first["schema"]["paths"]["$.page"] == "integer"
    assert first["schema"]["required"]["$"] == ["items", "page"]
    assert len(first["latency_samples_ms"]) == 5


def test_clean_fixture_run_has_no_incidents(fixture_server):
    setup_baseline(fixture_server)
    result = check_endpoint(fixture_server.url + "/api/items")
    assert result["incidents"] == []
    assert set(result["detectors"].values()) == {"pass"}
    assert load_incidents() == []


@pytest.mark.parametrize(
    ("scenario", "detector", "field_path", "before", "after"),
    [
        ("schema", "schema_drift", "$.page", 1, "1"),
        ("latency", "latency_spike", "$latency.p50_ms", None, None),
        ("breaking", "breaking_response", "$response.status_code", 200, 500),
        ("auth", "auth_failure", "$response.status_code", 200, 401),
        ("behavioral", "behavioral_drift", "$.items[0].currency", "USD", "EUR"),
    ],
)
def test_each_drift_has_one_named_incident_with_exact_diff(
    fixture_server, scenario, detector, field_path, before, after
):
    setup_baseline(fixture_server)
    fixture_server.set_scenario(scenario)
    result = check_endpoint(fixture_server.url + "/api/items", samples=1)
    assert len(result["incidents"]) == 1
    incident = result["incidents"][0]
    assert incident["detector"] == detector
    assert incident["endpoint"] == fixture_server.url + "/api/items"
    assert incident["diff"]["field_path"] == field_path
    if scenario == "latency":
        assert incident["diff"]["before"] == 0
        assert incident["diff"]["after"] == result["latency"]["p50_ms"]
    else:
        assert incident["diff"] == {"field_path": field_path, "before": before, "after": after}
    assert len(result["new_incidents"]) == 1
    assert len(load_incidents()) == 1
    assert set(SCENARIOS) >= {scenario}


def test_latency_spike_reports_measured_delta_within_scripted_tolerance(fixture_server):
    baseline = setup_baseline(fixture_server)
    fixture_server.set_scenario("latency")
    result = check_endpoint(fixture_server.url + "/api/items", samples=3)
    incident = result["incidents"][0]
    measurement = incident["measurement"]
    measured_delta = measurement["p50_delta_ms"]
    assert incident["detector"] == "latency_spike"
    assert 250 <= measured_delta <= 350
    assert result["latency"]["p50_ms"] - baseline["latency_p50_ms"] == measured_delta
    assert measurement["p95_after_ms"] - measurement["p95_before_ms"] >= 250


def test_same_drift_is_idempotent(fixture_server):
    setup_baseline(fixture_server)
    fixture_server.set_scenario("behavioral")
    first = check_endpoint(fixture_server.url + "/api/items")
    second = check_endpoint(fixture_server.url + "/api/items")
    assert len(first["new_incidents"]) == 1
    assert second["incidents"][0]["id"] == first["incidents"][0]["id"]
    assert second["new_incidents"] == []
    assert len(load_incidents()) == 1
    row = json.loads((isolated_state_path() / "ao-incidents.jsonl").read_text(encoding="utf-8"))
    assert row["id"] == first["incidents"][0]["id"]


def isolated_state_path():
    import os

    from pathlib import Path

    return Path(os.environ["API_OBSERVATORY_HOME"])


def test_non_loopback_endpoints_are_rejected():
    with pytest.raises(LocalEndpointError):
        capture_baseline("http://example.com/api")
