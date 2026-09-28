"""Small HTTP client restricted to loopback addresses for the local demo."""

from __future__ import annotations

import ipaddress
import json
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class LocalEndpointError(ValueError):
    pass


def validate_local_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "http" or not parts.hostname or parts.username or parts.password:
        raise LocalEndpointError("Only plain HTTP loopback fixture URLs are allowed in this demo.")
    hostname = parts.hostname.lower()
    try:
        is_loopback = ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        is_loopback = hostname == "localhost"
    if not is_loopback:
        raise LocalEndpointError("Endpoint must resolve to localhost or a loopback IP for this local fixture demo.")
    return url


@dataclass
class HTTPResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes
    latency_ms: int

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";", 1)[0].strip().lower()

    def json(self):
        return json.loads(self.body.decode("utf-8"))


def request(url: str, *, timeout: float = 5.0, data: bytes | None = None, method: str | None = None) -> HTTPResponse:
    validate_local_url(url)
    started = time.perf_counter_ns()
    headers = {
        "Accept": "application/json",
        "User-Agent": "api-observatory-local-demo/0.1",
        "X-API-Key": "fixture-valid-key",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers, method=method)
    try:
        response = urlopen(req, timeout=timeout)
    except HTTPError as exc:
        response = exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"local fixture request failed for {url}: {exc}") from exc
    with response:
        body = response.read()
        headers = {key.lower(): value for key, value in response.headers.items()}
        status_code = int(response.status)
    elapsed = (time.perf_counter_ns() - started) / 1_000_000
    # Use a 50 ms resolution for local round trips and suppress sub-resolution noise.
    latency_ms = int(round(elapsed / 50.0) * 50)
    if latency_ms < 50:
        latency_ms = 0
    return HTTPResponse(status_code, headers, body, latency_ms)
