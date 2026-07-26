# Hyrax event → span schema

## Core entities (required)

OpenLineage's spec requires three things on every event: a `Run` with a UUID `runId`, a `Job` with `namespace` + `name`, and at least one input and one output `Dataset`. Everything else is optional facet data. Hyrax's converter handles required fields first, then layers in facets — this is what allows graceful degradation across orchestrators with partial OpenLineage support.

## Field-by-field mapping

| OpenLineage field | Source | OTel mapping | Notes |
|---|---|---|---|
| `run.runId` | Run (required) | Trace ID (128-bit from UUID) | UUID bytes used directly as trace identifier |
| `job.namespace` + `job.name` | Job (required) | Span name; `service.name` = namespace | e.g. `hyrax-demo.stg_orders` |
| `eventType` | Run event | Span status + lifecycle | START opens; COMPLETE → OK; FAIL/ABORT → Error |
| `eventTime` | Run event | Span start/end timestamp (nanoseconds) | |
| Parent run facet | Run facet (optional) | `run.parent_run_id` attribute | For nested runs, e.g. Airflow task inside a DAG |
| Input datasets | Dataset (required, ≥1 on terminal events) | `dataset.input.N.namespace` / `.name` attributes | One attribute set per input |
| Output datasets | Dataset (required, ≥1 on terminal events) | `dataset.output.N.namespace` / `.name` attributes | One attribute set per output |
| `schema` facet | Dataset facet | `dataset.{dir}.N.schema` attribute | Field names and types as a compact string |
| `columnLineage` facet | Dataset facet | `dataset.{dir}.N.column_lineage` attribute | Output field → input field map (JSON) |
| `dataQualityAssertions` facet | Dataset facet | Span events `test.failed` / `test.passed` with `actual`, `expected`, `severity` | One span event per assertion |
| `dataQualityMetrics` facet | Dataset facet | `dataset.{dir}.N.dqm_row_count`, `dqm_null_count` attributes | |
| `inputStatistics` / `outputStatistics` | Dataset facet | `dataset.{dir}.N.row_count`, `.byte_size` attributes + OTel metrics | |
| `ownership` facet | Job facet | `owner.team` attribute | First owner entry |
| `sourceCodeLocation` facet | Job facet | `run.source_url`, `run.git_branch`, `run.git_sha` attributes | dbt auto-populates from git remote |
| `nominalTime` facet | Run facet | `run.nominal_start_time`, `run.nominal_end_time` attributes + freshness lag metric | |
| Error message | Run facet (on FAIL) | `run.error` attribute + span status Error | |
| Unknown / custom facets | Any | `custom.job.*`, `custom.run.*`, `custom.dataset.*` | Preserved, never dropped |

## Degradation strategy by orchestrator

| Orchestrator | Integration | Facet richness |
|---|---|---|
| dbt | `openlineage-dbt` (`dbt-ol` wrapper) | Strong — schema and columnLineage available if `dbt docs generate` has run; dataQualityAssertions from dbt tests |
| Airflow | `apache-airflow-providers-openlineage` | Strong on run/parent-run structure; dataset-level detail depends on the operator |
| Dagster | `dagster-openlineage` (v0.2+, Dagster ≥1.11.6) | Strong — asset-centric, schema, column-lineage, and data-quality-assertion facets natively |
| Spark / Flink | Native OpenLineage integration | Core fields reliable; schema and statistics depend on the engine version |

## Illustrative example

A `COMPLETE` event for `stg_orders` carries: a UUID run ID, job `hyrax-demo/stg_orders`, input `raw_orders`, output `stg_orders` with schema (`order_id:VARCHAR`, `amount:DECIMAL`) and outputStatistics (`rowCount=1000`, `size=65536`).

Hyrax converts this to a span named `hyrax-demo.stg_orders` with:
- Trace ID = UUID bytes
- Status = OK
- Attributes: `dataset.output.0.row_count=1000`, `dataset.output.0.byte_size=65536`, `dataset.output.0.schema=order_id:VARCHAR, amount:DECIMAL`
- Metrics: `pipeline.duration_ms`, `pipeline.row_count=1000`
