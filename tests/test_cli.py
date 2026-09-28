from __future__ import annotations

import subprocess
import sys

from api_observatory.cli import main
from api_observatory.models import HONESTY


def test_cli_smoke_for_all_subcommands(fixture_server, isolated_state, capsys):
    endpoint = fixture_server.url + "/api/items"
    base_url = fixture_server.url

    assert main(["fixtures"]) == 0
    assert "synthetic local fixture server" in capsys.readouterr().out
    assert main(["fixtures", "--status", "--url", base_url]) == 0
    assert '"scenario":"baseline"' in capsys.readouterr().out
    assert main(["fixtures", "--scenario", "baseline", "--url", base_url]) == 0
    assert main(["baseline", endpoint, "--samples", "2"]) == 0
    assert "LOCAL FIXTURE DEMO" in capsys.readouterr().out
    assert main(["check", endpoint]) == 0
    assert main(["watch", endpoint, "--runs", "1", "--interval", "0.01"]) == 0
    assert "Checked" in capsys.readouterr().out
    assert main(["incidents"]) == 0
    assert "incident record(s)" in capsys.readouterr().out

    fixture_server.set_scenario("schema")
    assert main(["check", endpoint]) == 0
    capsys.readouterr()
    assert main(["report", "--format", "md"]) == 0
    assert HONESTY in capsys.readouterr().out
    assert main(["report", "--format", "json"]) == 0
    assert HONESTY in capsys.readouterr().out
    assert main(["report", "--format", "html"]) == 0
    assert HONESTY in capsys.readouterr().out


def test_html_report_has_explicit_honesty_banner(fixture_server, isolated_state):
    from api_observatory.engine import capture_baseline
    from api_observatory.reporting import render_report

    capture_baseline(fixture_server.url + "/api/items", samples=1)
    html = render_report("html")
    assert "class=\"banner\"" in html
    assert HONESTY in html


def test_fixture_server_cli_starts_and_stops():
    process = subprocess.Popen(
        [sys.executable, "-u", "-m", "api_observatory", "fixtures", "--serve", "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        notice = process.stdout.readline().strip()
        server_line = process.stdout.readline().strip()
        assert "synthetic local fixture server" in notice
        assert "Serving synthetic fixture API at http://127.0.0.1:" in server_line
    finally:
        process.terminate()
        process.wait(timeout=5)
