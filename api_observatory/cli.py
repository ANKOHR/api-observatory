"""Command line interface for API Observatory."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Sequence

from .engine import capture_baseline, check_all, check_endpoint
from .fixtures import FixtureHTTPServer, SCENARIOS
from .http_client import request
from .models import HONESTY, canonical_json
from .reporting import render_report
from .storage import incident_path, load_baselines, load_incidents


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="obs",
        description="Deterministic API drift monitoring against a synthetic local fixture server.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    watch = subparsers.add_parser("watch", help="check a baseline repeatedly on a local schedule")
    watch.add_argument("endpoint", help="loopback fixture endpoint URL")
    watch.add_argument("--interval", type=float, default=30.0, help="seconds between checks")
    watch.add_argument("--runs", type=int, default=0, help="number of checks; 0 runs until interrupted")
    watch.add_argument("--samples", type=int, default=1, help="HTTP samples per check")
    watch.add_argument("--threshold-ms", type=int, default=150, help="minimum measured latency increase")

    check = subparsers.add_parser("check", help="check one endpoint or every stored baseline")
    check.add_argument("endpoint", nargs="?", help="loopback fixture endpoint URL; omit to check all baselines")
    check.add_argument("--samples", type=int, default=1, help="HTTP samples per check")
    check.add_argument("--threshold-ms", type=int, default=150, help="minimum measured latency increase")

    baseline = subparsers.add_parser("baseline", help="capture and save an endpoint baseline")
    baseline.add_argument("endpoint", help="loopback fixture endpoint URL")
    baseline.add_argument("--samples", type=int, default=5, help="HTTP latency samples to capture")

    incidents = subparsers.add_parser("incidents", help="list recorded incidents")
    incidents.add_argument("--limit", type=int, default=0, help="show only the newest N incidents")

    report = subparsers.add_parser("report", help="render endpoint health and evidence-pointed incidents")
    report.add_argument("--format", choices=("md", "html", "json"), default="md")
    report.add_argument("--output", help="write the report to this path")

    fixtures = subparsers.add_parser("fixtures", help="serve or control the synthetic local fixture API")
    mode = fixtures.add_mutually_exclusive_group()
    mode.add_argument("--serve", action="store_true", help="start a local fixture server")
    mode.add_argument("--scenario", choices=SCENARIOS, help="switch the running fixture scenario")
    mode.add_argument("--status", action="store_true", help="show the running fixture scenario")
    fixtures.add_argument("--url", help="base URL of the running loopback fixture server")
    fixtures.add_argument("--port", type=int, default=8765, help="port for --serve")
    fixtures.add_argument("--initial", choices=SCENARIOS, default="baseline", help="initial scenario for --serve")
    return parser


def _print_notice() -> None:
    print(f"API Observatory: {HONESTY}")


def _show_check(result: dict) -> None:
    incidents = result["incidents"]
    new_count = len(result["new_incidents"])
    print(f"Checked {result['endpoint']}: {len(incidents)} detected incident(s), {new_count} newly recorded.")
    for detector, status in result["detectors"].items():
        measured = canonical_json(result["detector_results"][detector]["measured"])
        print(f"  {detector}: {status}; measured={measured}")
    for incident in incidents:
        diff = incident["diff"]
        print(
            f"  {incident['detector']} {incident['severity']}: {diff['field_path']} "
            f"{canonical_json(diff['before'])} -> {canonical_json(diff['after'])}"
        )


def _serve_fixtures(port: int, initial: str) -> int:
    server = FixtureHTTPServer(("127.0.0.1", port))
    server.set_scenario(initial)
    host, bound_port = server.server_address[:2]
    print(f"API Observatory: {HONESTY}")
    print(f"Serving synthetic fixture API at http://{host}:{bound_port}/api/items")
    print(f"Initial scenario: {initial}. Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Fixture server stopped.")
    finally:
        server.server_close()
    return 0


def _fixtures_command(args: argparse.Namespace) -> int:
    if args.serve:
        return _serve_fixtures(args.port, args.initial)
    if args.scenario:
        if not args.url:
            raise ValueError("--url is required with --scenario")
        result = request(args.url.rstrip("/") + "/_control/scenario", data=json.dumps({"scenario": args.scenario}).encode("utf-8"), method="POST")
        if result.status_code != 200:
            raise RuntimeError(f"fixture server rejected scenario change with status {result.status_code}")
        print(f"API Observatory: {HONESTY}")
        print(f"Fixture scenario set to {args.scenario} at {args.url}.")
        return 0
    if args.status:
        if not args.url:
            raise ValueError("--url is required with --status")
        result = request(args.url.rstrip("/") + "/_control/scenario")
        print(f"API Observatory: {HONESTY}")
        print(result.body.decode("utf-8"))
        return 0 if result.status_code == 200 else 1
    print(f"API Observatory: {HONESTY}")
    print("Available fixture scenarios: " + ", ".join(SCENARIOS))
    print("Use obs fixtures --serve, then obs fixtures --scenario <name> --url <base-url>.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "fixtures":
            return _fixtures_command(args)
        if args.command == "baseline":
            _print_notice()
            snapshot = capture_baseline(args.endpoint, samples=args.samples)
            print(f"Baseline saved for {snapshot['endpoint']}: HTTP {snapshot['status_code']}, {len(snapshot['schema']['paths'])} schema paths.")
            print(f"Latency samples in ms: {canonical_json(snapshot['latency_samples_ms'])}")
            print(f"State file: {Path(os.environ.get('API_OBSERVATORY_HOME', Path.cwd() / '.api-observatory')) / 'baselines.json'}")
            return 0
        if args.command == "check":
            _print_notice()
            results = [check_endpoint(args.endpoint, samples=args.samples, latency_threshold_ms=args.threshold_ms)] if args.endpoint else check_all(samples=args.samples, latency_threshold_ms=args.threshold_ms)
            for result in results:
                _show_check(result)
            return 0
        if args.command == "watch":
            _print_notice()
            if args.interval <= 0 or args.runs < 0:
                raise ValueError("interval must be positive and runs cannot be negative")
            if args.endpoint not in load_baselines():
                snapshot = capture_baseline(args.endpoint)
                print(f"No baseline existed. Captured HTTP {snapshot['status_code']} baseline for {args.endpoint}.")
            count = 0
            while args.runs == 0 or count < args.runs:
                _show_check(check_endpoint(args.endpoint, samples=args.samples, latency_threshold_ms=args.threshold_ms))
                count += 1
                if args.runs == 0 or count < args.runs:
                    time.sleep(args.interval)
            return 0
        if args.command == "incidents":
            _print_notice()
            if args.limit < 0:
                raise ValueError("limit cannot be negative")
            records = load_incidents()
            if args.limit:
                records = records[-args.limit:]
            print(f"{len(records)} incident record(s). Log: {incident_path()}")
            for incident in records:
                diff = incident["diff"]
                print(
                    f"{incident['timestamp']} {incident['detector']} {incident['severity']} "
                    f"{incident['endpoint']} {diff['field_path']}: "
                    f"{canonical_json(diff['before'])} -> {canonical_json(diff['after'])} [{incident['id']}]"
                )
            return 0
        if args.command == "report":
            content = render_report(args.format)
            if args.output:
                path = Path(args.output)
                path.write_text(content, encoding="utf-8", newline="\n")
                print(f"Report written to {path.resolve()}")
                print(HONESTY)
            else:
                sys.stdout.write(content)
            return 0
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"API Observatory: {HONESTY}", file=sys.stderr)
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
