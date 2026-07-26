"""
hyrax.converter
---------------
Core logic: OpenLineage event → OpenTelemetry span.

Full field mapping is documented in SCHEMA.md. Key design decisions:

1. runId (UUID) → OTel trace ID (128-bit int from UUID bytes).
2. START event opens a span and stores it in _active_spans.
3. COMPLETE / FAIL / ABORT closes the span with appropriate status.
4. Orphan COMPLETE events (no matching START) create+close a span immediately.
5. Every facet is optional and additive — missing facets produce a thinner
   but still valid span, never a failure.
6. Unknown / custom facets are preserved under the 'custom.*' namespace.
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from opentelemetry import trace
from opentelemetry.trace import (
    SpanContext,
    SpanKind,
    TraceFlags,
    Status,
    StatusCode,
    NonRecordingSpan,
)
from opentelemetry.sdk.trace import Tracer

logger = logging.getLogger(__name__)

# In-memory span store: runId -> open SDK span.
# Stateless across restarts by design (SigNoz is the system of record).
_active_spans: dict[str, Any] = {}

# Facets we handle explicitly; everything else lands under custom.*
_KNOWN_JOB_FACETS = {"ownership", "sourceCodeLocation", "sql", "documentation"}
_KNOWN_RUN_FACETS = {
    "nominalTime",
    "parentRun",
    "errorMessage",
    "externalQuery",
}
_KNOWN_DATASET_FACETS = {
    "schema",
    "columnLineage",
    "dataQualityAssertions",
    "dataQualityMetrics",
    "inputStatistics",
    "outputStatistics",
    "dataSource",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_id_to_trace_id(run_id: str) -> int:
    """UUID → 128-bit OTel trace ID."""
    return uuid.UUID(run_id).int


def _run_id_to_span_id(run_id: str) -> int:
    """Upper 8 bytes of UUID → 64-bit OTel span ID."""
    return uuid.UUID(run_id).int >> 64


def _iso_to_ns(iso: str) -> Optional[int]:
    """ISO 8601 string → nanoseconds since epoch, or None on parse failure."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1_000_000_000)
    except ValueError:
        logger.debug("Could not parse timestamp: %r", iso)
        return None


def _make_parent_context(trace_id: int) -> Any:
    """Create a non-recording parent span so child inherits our trace ID."""
    parent_span_ctx = SpanContext(
        trace_id=trace_id,
        span_id=trace_id & 0xFFFFFFFFFFFFFFFF,  # lower 64 bits as synthetic parent
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    return trace.set_span_in_context(NonRecordingSpan(parent_span_ctx))


# ---------------------------------------------------------------------------
# Facet handlers
# ---------------------------------------------------------------------------

def _apply_job_facets(span: Any, facets: dict) -> None:
    ownership = facets.get("ownership", {})
    owners = ownership.get("owners", [])
    if owners:
        span.set_attribute("owner.team", owners[0].get("name", ""))
        if len(owners) > 1:
            span.set_attribute("owner.email", owners[1].get("name", ""))

    src = facets.get("sourceCodeLocation", {})
    if src.get("url"):
        span.set_attribute("run.source_url", src["url"])
    if src.get("branch"):
        span.set_attribute("run.git_branch", src["branch"])
    if src.get("version"):
        span.set_attribute("run.git_sha", src["version"])

    # Custom/unknown facets → custom.job.*
    for key, val in facets.items():
        if key not in _KNOWN_JOB_FACETS:
            try:
                span.set_attribute(f"custom.job.{key}", json.dumps(val))
            except Exception:
                pass


def _apply_run_facets(span: Any, facets: dict) -> None:
    nominal = facets.get("nominalTime", {})
    if nominal.get("nominalStartTime"):
        span.set_attribute("run.nominal_start_time", nominal["nominalStartTime"])
    if nominal.get("nominalEndTime"):
        span.set_attribute("run.nominal_end_time", nominal["nominalEndTime"])

    error = facets.get("errorMessage", {})
    if error.get("message"):
        span.set_attribute("run.error", error["message"])
    if error.get("programmingLanguage"):
        span.set_attribute("run.error_language", error["programmingLanguage"])

    parent = facets.get("parentRun", {})
    if parent.get("run", {}).get("runId"):
        span.set_attribute("run.parent_run_id", parent["run"]["runId"])

    for key, val in facets.items():
        if key not in _KNOWN_RUN_FACETS:
            try:
                span.set_attribute(f"custom.run.{key}", json.dumps(val))
            except Exception:
                pass


def _apply_datasets(span: Any, datasets: list, direction: str) -> None:
    """Map input/output dataset facets onto span attributes and events."""
    for i, ds in enumerate(datasets):
        prefix = f"dataset.{direction}.{i}"
        ns = ds.get("namespace", "")
        name = ds.get("name", "")
        span.set_attribute(f"{prefix}.namespace", ns)
        span.set_attribute(f"{prefix}.name", name)

        facets = ds.get("facets", {})

        # schema facet → dataset.schema attribute
        schema_facet = facets.get("schema", {})
        fields = schema_facet.get("fields", [])
        if fields:
            schema_str = ", ".join(
                f"{f.get('name')}:{f.get('type', '?')}" for f in fields
            )
            span.set_attribute(f"{prefix}.schema", schema_str)

        # columnLineage facet
        col_lineage = facets.get("columnLineage", {})
        if col_lineage.get("fields"):
            span.set_attribute(
                f"{prefix}.column_lineage", json.dumps(col_lineage["fields"])
            )

        # statistics
        for stat_key in ("inputStatistics", "outputStatistics"):
            stats = facets.get(stat_key, {})
            if stats.get("rowCount") is not None:
                span.set_attribute(f"{prefix}.row_count", int(stats["rowCount"]))
            if stats.get("size") is not None:
                span.set_attribute(f"{prefix}.byte_size", int(stats["size"]))

        # dataQualityAssertions → span events
        dqa = facets.get("dataQualityAssertions", {})
        for assertion in dqa.get("assertions", []):
            event_name = (
                "test.passed" if assertion.get("success", True) else "test.failed"
            )
            span.add_event(
                event_name,
                {
                    "assertion.column": assertion.get("column", ""),
                    "assertion.property": assertion.get("property", ""),
                    "assertion.actual": str(assertion.get("actualValue", "")),
                    "assertion.expected": str(assertion.get("expectedValue", "")),
                    "assertion.severity": assertion.get("severity", "ERROR"),
                    "dataset.name": name,
                    "dataset.namespace": ns,
                },
            )

        # dataQualityMetrics
        dqm = facets.get("dataQualityMetrics", {})
        if dqm.get("rowCount") is not None:
            span.set_attribute(f"{prefix}.dqm_row_count", int(dqm["rowCount"]))
        if dqm.get("nullCount") is not None:
            span.set_attribute(f"{prefix}.dqm_null_count", int(dqm["nullCount"]))

        # Custom dataset facets → custom.dataset.*
        for key, val in facets.items():
            if key not in _KNOWN_DATASET_FACETS:
                try:
                    span.set_attribute(f"custom.dataset.{direction}.{i}.{key}", json.dumps(val))
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

def convert(event: dict[str, Any], tracer: Tracer) -> None:
    """
    Convert one OpenLineage event into an OTel span operation.

    START  → opens a span, stores it in _active_spans.
    COMPLETE/FAIL/ABORT → closes the stored span (or creates+closes an orphan).
    RUNNING/OTHER → enriches the active span if present; otherwise no-op.
    """
    run = event.get("run", {})
    job = event.get("job", {})
    event_type = event.get("eventType", "OTHER")
    event_time = event.get("eventTime", "")
    inputs = event.get("inputs", [])
    outputs = event.get("outputs", [])

    run_id: str = run.get("runId", "")
    job_namespace: str = job.get("namespace", "unknown")
    job_name: str = job.get("name", "unknown")
    run_facets: dict = run.get("facets", {})
    job_facets: dict = job.get("facets", {})

    span_name = f"{job_namespace}.{job_name}"
    trace_id = _run_id_to_trace_id(run_id)
    event_ns = _iso_to_ns(event_time)

    if event_type == "START":
        ctx = _make_parent_context(trace_id)
        span = tracer.start_span(
            span_name,
            context=ctx,
            kind=SpanKind.INTERNAL,
            start_time=event_ns,
        )
        span.set_attribute("pipeline.run_id", run_id)
        span.set_attribute("pipeline.job_namespace", job_namespace)
        span.set_attribute("pipeline.job_name", job_name)
        span.set_attribute("service.name", job_namespace)

        _apply_job_facets(span, job_facets)
        _apply_run_facets(span, run_facets)
        _apply_datasets(span, inputs, "input")
        _apply_datasets(span, outputs, "output")

        _active_spans[run_id] = span
        logger.info("Opened span for run %s (%s)", run_id, span_name)

    elif event_type in ("COMPLETE", "FAIL", "ABORT"):
        span = _active_spans.pop(run_id, None)
        if span is None:
            # Orphan COMPLETE — create and close immediately
            logger.debug("Orphan %s event for run %s — creating synthetic span", event_type, run_id)
            ctx = _make_parent_context(trace_id)
            span = tracer.start_span(span_name, context=ctx, kind=SpanKind.INTERNAL)
            span.set_attribute("pipeline.run_id", run_id)
            span.set_attribute("pipeline.job_namespace", job_namespace)
            span.set_attribute("pipeline.job_name", job_name)
            span.set_attribute("service.name", job_namespace)

        _apply_job_facets(span, job_facets)
        _apply_run_facets(span, run_facets)
        _apply_datasets(span, inputs, "input")
        _apply_datasets(span, outputs, "output")

        if event_type == "COMPLETE":
            span.set_status(Status(StatusCode.OK))
        else:
            error_msg = (
                run_facets.get("errorMessage", {}).get("message")
                or f"Run ended with status: {event_type}"
            )
            span.set_attribute("run.error", error_msg)
            span.set_status(Status(StatusCode.ERROR, error_msg))

        span.end(end_time=event_ns)
        logger.info("Closed span for run %s with status %s", run_id, event_type)

    elif event_type == "RUNNING":
        span = _active_spans.get(run_id)
        if span:
            _apply_run_facets(span, run_facets)
        # else: no-op — RUNNING before START is ignorable

    else:
        logger.debug("Ignoring eventType=%s for run %s", event_type, run_id)
