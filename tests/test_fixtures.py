from __future__ import annotations

import time

import pytest

from api_observatory.fixtures import BASE_BODY, SCENARIOS
from api_observatory.http_client import request


def fetch(server):
    return request(server.url + "/api/items")


def test_server_starts_and_serves_deterministic_baseline(fixture_server):
    first = fetch(fixture_server)
    second = fetch(fixture_server)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == BASE_BODY
    assert first.headers["x-fixture-time"] == second.headers["x-fixture-time"]
    assert first.headers["x-fixture-source"] == "synthetic-local"


@pytest.mark.parametrize("scenario", SCENARIOS[1:])
def test_each_fixture_scenario_changes_only_its_scripted_aspect(fixture_server, scenario):
    baseline = fetch(fixture_server)
    started = time.perf_counter()
    fixture_server.set_scenario(scenario)
    drifted = fetch(fixture_server)
    elapsed = time.perf_counter() - started

    if scenario == "schema":
        assert drifted.status_code == baseline.status_code
        assert drifted.content_type == baseline.content_type
        assert drifted.json()["page"] == "1"
        assert {**drifted.json(), "page": 1} == baseline.json()
    elif scenario == "latency":
        assert drifted.status_code == baseline.status_code
        assert drifted.json() == baseline.json()
        assert 0.25 <= elapsed <= 0.45
    elif scenario == "breaking":
        assert drifted.status_code == 500
        assert drifted.json() == baseline.json()
        assert drifted.content_type == baseline.content_type
    elif scenario == "auth":
        assert drifted.status_code == 401
        assert drifted.json() == baseline.json()
        assert drifted.content_type == baseline.content_type
    elif scenario == "behavioral":
        assert drifted.status_code == baseline.status_code
        assert drifted.content_type == baseline.content_type
        assert drifted.json()["items"][0]["currency"] == "EUR"
        changed_back = drifted.json()
        changed_back["items"][0]["currency"] = "USD"
        assert changed_back == baseline.json()


def test_fixture_lists_all_named_scenarios(fixture_server):
    response = request(fixture_server.url + "/_control/scenario")
    assert response.json()["available"] == list(SCENARIOS)
