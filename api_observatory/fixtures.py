"""Synthetic local HTTP fixtures with switchable, scripted drift scenarios."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit


SCENARIOS = ("baseline", "schema", "latency", "breaking", "auth", "behavioral")
FIXTURE_TIME = "2026-09-28T00:00:00Z"
BASE_BODY = {"items": [{"id": 1, "name": "alpha", "currency": "USD"}], "page": 1}


class FixtureHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int]):
        super().__init__(address, FixtureHandler)
        self.scenario = "baseline"
        self.scenario_lock = threading.Lock()

    def set_scenario(self, scenario: str) -> None:
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown fixture scenario: {scenario}")
        with self.scenario_lock:
            self.scenario = scenario

    def get_scenario(self) -> str:
        with self.scenario_lock:
            return self.scenario


class FixtureHandler(BaseHTTPRequestHandler):
    server: FixtureHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, status: int, payload: Any, headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-API-Version", "1")
        self.send_header("X-Fixture-Time", FIXTURE_TIME)
        self.send_header("X-Fixture-Source", "synthetic-local")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/_control/scenario":
            self._send_json(200, {"scenario": self.server.get_scenario(), "available": list(SCENARIOS)})
            return
        if path == "/health":
            self._send_json(200, {"status": "ok", "source": "synthetic-local"})
            return
        if path != "/api/items":
            self._send_json(404, {"error": "not found"})
            return

        scenario = self.server.get_scenario()
        if scenario == "latency":
            time.sleep(0.30)

        body = json.loads(json.dumps(BASE_BODY))
        if scenario == "schema":
            body["page"] = "1"
        elif scenario == "behavioral":
            body["items"][0]["currency"] = "EUR"

        status = 200
        if scenario == "breaking":
            status = 500
        elif scenario == "auth" or self.headers.get("X-API-Key") != "fixture-valid-key":
            status = 401
        self._send_json(status, body)

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path != "/_control/scenario":
            self._send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            scenario = payload.get("scenario")
            self.server.set_scenario(scenario)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._send_json(200, {"scenario": scenario, "source": "synthetic-local"})


class FixtureServer:
    """Context manager for an ephemeral loopback fixture server."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self.httpd = FixtureHTTPServer((host, port))
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> "FixtureServer":
        self.thread.start()
        return self

    def set_scenario(self, scenario: str) -> None:
        self.httpd.set_scenario(scenario)

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        if self.thread.is_alive():
            self.thread.join(timeout=2)

    def __enter__(self) -> "FixtureServer":
        return self.start()

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()
