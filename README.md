# API Observatory

API Observatory is a deterministic HTTP response drift monitor with a scripted local fixture API. It captures an endpoint baseline, makes real HTTP requests during checks, computes response differences, and stores idempotent incidents with exact evidence paths.

## Quickstart

Python 3.10 or newer is required. The runtime uses the Python standard library. Install the test runner and the local command:

```text
python -m pip install -e . pytest
```

Start the synthetic API in one terminal:

```text
obs fixtures --serve --port 8765
```

In another terminal, capture a baseline and check it:

```text
obs baseline http://127.0.0.1:8765/api/items
obs check http://127.0.0.1:8765/api/items
```

Switch the local fixture to a scripted scenario, then check again:

```text
obs fixtures --scenario schema --url http://127.0.0.1:8765
obs check http://127.0.0.1:8765/api/items
obs incidents
obs report --format html --output report.html
```

The available scenarios are `baseline`, `schema`, `latency`, `breaking`, `auth`, and `behavioral`. Return to the baseline with `obs fixtures --scenario baseline --url http://127.0.0.1:8765`. The `watch` command runs scheduled checks; use `obs watch <endpoint> --interval 30 --runs 3` for a finite local demonstration. A baseline is captured automatically when watch has no saved baseline.

Run the test suite with `pytest`.

## Honesty boundary

**This demo runs only against a synthetic local fixture server. No real production API is monitored.** The CLI accepts only HTTP URLs on `localhost` or a loopback IP address. The fixture data and all scenario changes are synthetic. No third-party API is called.

## Architecture

```text
obs CLI
  | baseline/check/watch
  v
loopback-only HTTP client ---- HTTP request ----> synthetic fixture API
  |                                                | baseline/schema/latency/
  | measured response                              | breaking/auth/behavioral
  v                                                v
baseline engine <---- saved JSON snapshot     scenario control endpoint
  |
  +--> schema drift detector
  +--> latency spike detector
  +--> breaking response detector
  +--> auth failure detector
  +--> behavioural drift detector
  |
  v
idempotent ao-incidents.jsonl --> Markdown, HTML, or JSON report
```

Baselines and the JSONL incident log live under `.api-observatory/` by default. Set `API_OBSERVATORY_HOME` to use a different local state directory. A baseline includes JSON key paths, observed types, required object keys, status, selected headers, representative response values, and rounded latency samples with p50 and p95. Baseline capture sends one discarded warm-up request, then measures its configured sample count from actual HTTP round trips. Samples use 50 ms buckets, with sub-50 ms timing noise recorded as zero, to keep local fixture snapshots repeatable.

Fixture timestamps are fixed at `2026-09-28T00:00:00Z` and fixture scenarios use no random values. Incident IDs are hashes of endpoint, detector, field path, and before/after values. Together these rules make repeated runs of the same fixture scenario produce stable incidents and reports. The suite uses no GPU packages or GPU workloads.

## Drift taxonomy

- **Schema drift** compares JSON field paths, observed JSON types, and required object keys. Added, removed, or retyped fields produce evidence at the corresponding response path. The fixture retypes `$.page` from integer `1` to string `"1"`.
- **Latency spike** compares measured p50 and p95 response latency with the baseline. An incident includes both percentile measurements and their deltas. The fixture adds a scripted 300 ms delay.
- **Breaking response** detects a status-code change, content-type change, non-JSON response, or selected API-version header change. The fixture changes HTTP 200 to HTTP 500 while preserving the response body.
- **Auth failure** detects HTTP 401 or 403 when the baseline status was HTTP 200. The fixture changes only the status to 401.
- **Behavioural drift** compares representative JSON values and array lengths when the response shape remains compatible. The fixture changes `$.items[0].currency` from `USD` to `EUR` while keeping status and schema the same.

Every incident includes an endpoint and a `diff` with `field_path`, `before`, and `after`. Repeated detection of the same diff is idempotent in the JSONL log. Reports summarize endpoint health and counts per detector and include all recorded evidence. The HTML report has a visible local-fixture honesty banner.
