"""
hyrax.metrics
-------------
OTel metrics emitted on every converted event.

Metrics exported:
  pipeline.duration_ms        Histogram — end-to-end run duration in ms
  pipeline.runs_total         Counter  — total runs (labels: status, job_namespace, job_name)
  pipeline.row_count          Histogram — output rows per run
  pipeline.byte_size          Histogram — output bytes per run
  pipeline.freshness_lag_s    Histogram — seconds between nominal_end and actual_end
  pipeline.test_assertions    Counter  — dbt test assertions (labels: result=passed|failed)
"""
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

logger = logging.getLogger(__name__)

_meter_provider: MeterProvider | None = None
_meter = None


def _get_meter():
    global _meter_provider, _meter
    if _meter is not None:
        return _meter

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    insecure = os.getenv("OTEL_EXPORTER_OTLP_INSECURE", "true").lower() == "true"

    exporter = OTLPMetricExporter(endpoint=endpoint, insecure=insecure)
    reader = PeriodicExportingMetricReader(
        exporter,
        export_interval_millis=int(os.getenv("HYRAX_METRIC_INTERVAL_MS", "15000")),
    )
    _meter_provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(_meter_provider)
    _meter = _meter_provider.get_meter("hyrax.metrics", version="0.1.0")
    return _meter


def record(event: dict[str, Any], duration_ms: Optional[float] = None) -> None:
    """
    Emit OTel metrics for one OpenLineage event.

    Only fires on COMPLETE / FAIL / ABORT events where we have real terminal state.
    """
    event_type = event.get("eventType", "OTHER")
    if event_type not in ("COMPLETE", "FAIL", "ABORT"):
        return

    try:
        meter = _get_meter()
        run = event.get("run", {})
        job = event.get("job", {})
        run_facets = run.get("facets", {})
        outputs = event.get("outputs", [])

        job_namespace = job.get("namespace", "unknown")
        job_name = job.get("name", "unknown")
        status = "success" if event_type == "COMPLETE" else "failure"

        attrs = {
            "job_namespace": job_namespace,
            "job_name": job_name,
            "status": status,
        }

        # pipeline.runs_total
        runs_counter = meter.create_counter(
            "pipeline.runs_total",
            description="Total pipeline runs by status.",
        )
        runs_counter.add(1, attrs)

        # pipeline.duration_ms
        if duration_ms is not None:
            duration_hist = meter.create_histogram(
                "pipeline.duration_ms",
                unit="ms",
                description="End-to-end run duration in milliseconds.",
            )
            duration_hist.record(duration_ms, attrs)

        # pipeline.row_count and pipeline.byte_size from output statistics
        for ds in outputs:
            facets = ds.get("facets", {})
            for stat_key in ("outputStatistics", "inputStatistics"):
                stats = facets.get(stat_key, {})
                if stats.get("rowCount") is not None:
                    row_hist = meter.create_histogram(
                        "pipeline.row_count",
                        description="Output row count per run.",
                    )
                    row_hist.record(int(stats["rowCount"]), attrs)
                if stats.get("size") is not None:
                    byte_hist = meter.create_histogram(
                        "pipeline.byte_size",
                        unit="By",
                        description="Output byte size per run.",
                    )
                    byte_hist.record(int(stats["size"]), attrs)

        # pipeline.freshness_lag_s from nominalTime
        nominal = run_facets.get("nominalTime", {})
        nominal_end = nominal.get("nominalEndTime", "")
        actual_end = event.get("eventTime", "")
        if nominal_end and actual_end:
            try:
                def _parse(s):
                    return datetime.fromisoformat(s.replace("Z", "+00:00"))
                lag = (_parse(actual_end) - _parse(nominal_end)).total_seconds()
                if lag > 0:
                    lag_hist = meter.create_histogram(
                        "pipeline.freshness_lag_s",
                        unit="s",
                        description="Seconds between nominal end and actual end (SLA lag).",
                    )
                    lag_hist.record(lag, attrs)
            except Exception:
                pass

        # pipeline.test_assertions from dataQualityAssertions
        for ds in outputs + event.get("inputs", []):
            dqa = ds.get("facets", {}).get("dataQualityAssertions", {})
            for assertion in dqa.get("assertions", []):
                result = "passed" if assertion.get("success", True) else "failed"
                assert_counter = meter.create_counter(
                    "pipeline.test_assertions",
                    description="dbt test assertion results.",
                )
                assert_counter.add(1, {**attrs, "result": result})

    except Exception as exc:
        logger.warning("Metrics recording failed: %s", exc)


def shutdown() -> None:
    global _meter_provider
    if _meter_provider:
        _meter_provider.shutdown()
        _meter_provider = None
