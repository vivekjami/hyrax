# Hyrax

**An OpenLineage → OpenTelemetry bridge for data pipeline observability.**

Data teams have distributed tracing for microservices. They don't have it for data pipelines. Hyrax fixes that — it turns dbt runs, Airflow DAGs, and Dagster asset materializations into OpenTelemetry traces and metrics, without touching a single line of pipeline code.

---

## The problem

A typical dbt model failure takes **15–45 minutes** to diagnose without tracing: grep through logs, screenshot the dbt UI, manually correlate timestamps across runs. With a distributed trace, the same failure is root-caused in under 2 minutes — you can see exactly which upstream task failed, when, and why, in one view.

58% of data teams cite pipeline reliability as their top operational concern ([dbt Labs State of Analytics Engineering 2024](https://www.getdbt.com/resources/reports/state-of-analytics-engineering-2024)). The tools already exist on the service side (Datadog, SigNoz, Tempo). The gap is on the data side.

**The specific questions that stay unanswered without tracing:**

- Which upstream task caused the downstream delay?
- Was the failure data quality, infra saturation, or a retry storm?
- Which model is the recurring p95 bottleneck across 30 days of runs?
- How does a failed dbt test connect to the specific run that produced it?

---

## How it works

OpenLineage is already emitted natively by dbt, Airflow, Dagster, Spark, and Flink — **zero instrumentation code required** in your pipelines. Hyrax is a thin HTTP server that receives those events and converts them to OTel spans and metrics over OTLP.

```
dbt-ol run
Airflow (openlineage provider)    →  Hyrax (:5050)  →  SigNoz (:4317)
Dagster (dagster-openlineage)
```

Each pipeline run becomes:

| OpenLineage concept | OTel concept |
|---|---|
| `run` (UUID) | Trace (trace ID = UUID bytes) |
| `job` (namespace + name) | Root span |
| `eventType=START` | Span opens |
| `eventType=COMPLETE` / `FAIL` | Span closes with OK / Error status |
| Input / output datasets | Span attributes (`dataset.input.N.*`) |
| `schema` facet | `dataset.output.0.schema` attribute |
| `dataQualityAssertions` facet | `test.failed` / `test.passed` span events |
| `outputStatistics.rowCount` | `pipeline.row_count` metric + span attribute |
| `nominalTime` facet | `pipeline.freshness_lag_s` metric |
| `ownership` facet | `owner.team` attribute |

Full field mapping: [SCHEMA.md](./SCHEMA.md)

---

## Quickstart

### Prerequisites

- [SigNoz](https://signoz.io/docs/install/docker/) running locally — one command:
  ```bash
  git clone https://github.com/SigNoz/signoz.git
  cd signoz/deploy && ./install.sh
  ```
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Docker (for the containerised path)

### Option A — Docker (recommended)

```bash
git clone https://github.com/<you>/hyrax.git
cd hyrax
cp .env.example .env          # edit OTEL_EXPORTER_OTLP_ENDPOINT if needed
docker compose up -d
```

Hyrax listens on `:5050`. Point your orchestrator at it:

```bash
export OPENLINEAGE_URL=http://localhost:5050
dbt-ol run                    # dbt with openlineage-dbt installed
```

### Option B — uv (local dev)

```bash
git clone https://github.com/<you>/hyrax.git
cd hyrax
uv sync                       # installs runtime deps
uv sync --extra demo          # adds dbt-duckdb + openlineage-dbt
uv run python -m hyrax.listener
```

### Run the demo pipeline

```bash
./demo/run_demo.sh
```

This runs a 6-model DuckDB dbt project (raw → staging → marts over 100 real e-commerce orders) with OpenLineage emission, then opens a health summary. Open SigNoz at `http://localhost:3301` and navigate to Traces — your pipeline appears as a distributed trace within seconds.

---

## Demo: two failure scenarios

### 1. Upstream model breaks → cascading span errors

```bash
./demo/break_upstream.sh
```

Injects a bad column reference into `stg_orders`. In SigNoz you see:
- `stg_orders` span → Error (column not found)
- `fct_orders` span → Error (upstream failed)
- `fct_customer_ltv` span → Error (upstream failed)

Three cascading failures, one trace, root cause visible immediately.

### 2. dbt test failure → `test.failed` span event

```bash
./demo/fail_test.sh
```

Seeds a row with an invalid `status='cancelled'`. The `accepted_values` test fires a `test.failed` span event in SigNoz with:
```
assertion.column   = status
assertion.actual   = cancelled
assertion.severity = ERROR
```

That's the difference from a log line: the failure is attached to the exact run that produced it, with the actual vs. expected values in context.

---

## Metrics shipped

| Metric | Unit | Description |
|---|---|---|
| `pipeline.runs_total` | count | Total runs, labelled by `status` / `job_name` |
| `pipeline.duration_ms` | ms | End-to-end run wall time |
| `pipeline.row_count` | rows | Output rows per run |
| `pipeline.byte_size` | bytes | Output bytes per run |
| `pipeline.freshness_lag_s` | seconds | Actual end minus nominal end (SLA lag) |
| `pipeline.test_assertions` | count | dbt test assertions, labelled `result=passed|failed` |

---

## Pre-built SigNoz dashboard

Import `dashboards/signoz-dashboards.json` in SigNoz → Dashboards → Import. You get 13 panels out of the box:

- Run summary KPIs (total runs, success rate, avg duration, p95 freshness lag)
- Duration trend over 7 days, per job
- p95 latency by job name (table)
- Failure hotspots — top jobs by error count (24h)
- dbt test assertions: passed vs. failed (time series)
- Freshness lag by dataset (bar chart)
- Output row count trend

---

## Repo layout

```
hyrax/
├── hyrax/
│   ├── listener.py       # HTTP endpoint (POST /) + health probe
│   ├── validator.py      # Enforces OpenLineage minimum spec
│   ├── converter.py      # OpenLineage → OTel span/metric mapping
│   ├── exporter.py       # OTLP gRPC exporter (SigNoz or any OTel backend)
│   └── metrics.py        # OTel metrics (duration, row count, freshness lag)
├── demo/
│   ├── dbt_project/      # 6-model DuckDB project, 100 real e-commerce records
│   ├── run_demo.sh        # End-to-end demo runner
│   ├── break_upstream.sh  # Demo 1: cascading failure trace
│   └── fail_test.sh       # Demo 2: dbt test.failed span event
├── dashboards/
│   └── signoz-dashboards.json  # 13-panel SigNoz dashboard
├── tests/
│   ├── test_validator.py
│   └── test_converter.py
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml        # uv-managed
├── ARCHITECTURE.md
└── SCHEMA.md
```

---

## Configuration

All configuration via environment variables (see `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | SigNoz otel-collector gRPC endpoint |
| `OTEL_EXPORTER_OTLP_INSECURE` | `true` | Skip TLS (for local SigNoz) |
| `HYRAX_PORT` | `5050` | Listener port |
| `LOG_LEVEL` | `INFO` | Python log level |
| `HYRAX_DEBUG_SPANS` | `false` | Print every span to stdout |
| `HYRAX_BATCH_SIZE` | `64` | OTLP batch size |
| `HYRAX_METRIC_INTERVAL_MS` | `15000` | Metrics push interval |

---

## Run tests

```bash
uv sync
uv run pytest tests/ -v
```

---

## Technical numbers

- ~650 lines of Python across 5 source files
- < 5ms added latency per OpenLineage event (HTTP receive → OTLP enqueue)
- Zero changes required to dbt, Airflow, or Dagster config beyond setting `OPENLINEAGE_URL`
- Stateless — SigNoz is the system of record; Hyrax can restart freely without data loss
- Works with any OTel-compatible backend (Tempo, Jaeger, Honeycomb, Datadog OTLP endpoint)

---

## Why OpenLineage as input

OpenLineage is a CNCF incubating project with native integration in dbt, Airflow, Dagster, Spark, and Flink. By building against this standard rather than any individual orchestrator's API, Hyrax gets multi-orchestrator support for free — and any future orchestrator that adds OpenLineage support becomes compatible with zero code changes on our side.

---

## Roadmap

- Package the converter as an OpenTelemetry Collector receiver component, so it plugs into any Collector pipeline instead of running standalone.
- Add Spark and Flink demo integrations — no Hyrax code changes required, both already emit standard OpenLineage.

---

## Docs

- [ARCHITECTURE.md](./ARCHITECTURE.md) — system design, component responsibilities, data flow
- [SCHEMA.md](./SCHEMA.md) — field-by-field OpenLineage → OTel mapping

---

## Hackathon

Built for [**Agents of SigNoz**](https://wemakedevs.org/events/signoz) (WeMakeDevs × SigNoz, Jul 20–26 2026), Track 3 — *Build Your Own* — under the example prompt "bridge an unsupported data source into SigNoz."

---

## License

Apache 2.0 — matching OpenLineage's and SigNoz's own licensing.
