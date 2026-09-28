from __future__ import annotations

import pytest

from api_observatory.fixtures import FixtureServer


@pytest.fixture
def fixture_server():
    with FixtureServer() as server:
        yield server


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setenv("API_OBSERVATORY_HOME", str(state))
    return state
