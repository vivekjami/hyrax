"""Tests for hyrax.converter — field mapping assertions against synthetic payloads."""
import uuid
from unittest.mock import MagicMock, patch, call

import pytest

from hyrax import converter
from hyrax.converter import convert, _run_id_to_trace_id, _iso_to_ns


RUN_ID = "550e8400-e29b-41d4-a716-446655440000"

START_EVENT = {
    "eventType": "START",
    "eventTime": "2024-01-15T10:00:00Z",
    "run": {
        "runId": RUN_ID,
        "facets": {
            "nominalTime": {
                "nominalStartTime": "2024-01-15T09:00:00Z",
                "nominalEndTime": "2024-01-15T10:00:00Z",
            }
        },
    },
    "job": {
        "namespace": "hyrax-demo",
        "name": "stg_orders",
        "facets": {
            "ownership": {"owners": [{"name": "data-platform-team"}]},
            "sourceCodeLocation": {"url": "https://github.com/org/repo", "branch": "main"},
        },
    },
    "inputs": [{"namespace": "hyrax-demo", "name": "raw_orders", "facets": {}}],
    "outputs": [
        {
            "namespace": "hyrax-demo",
            "name": "stg_orders",
            "facets": {
                "schema": {
                    "fields": [
                        {"name": "order_id", "type": "VARCHAR"},
                        {"name": "amount", "type": "DECIMAL"},
                    ]
                },
                "outputStatistics": {"rowCount": 1000, "size": 65536},
            },
        }
    ],
}

COMPLETE_EVENT = {
    "eventType": "COMPLETE",
    "eventTime": "2024-01-15T10:05:00Z",
    "run": {"runId": RUN_ID, "facets": {}},
    "job": {"namespace": "hyrax-demo", "name": "stg_orders", "facets": {}},
    "inputs": [{"namespace": "hyrax-demo", "name": "raw_orders", "facets": {}}],
    "outputs": [{"namespace": "hyrax-demo", "name": "stg_orders", "facets": {}}],
}

FAIL_EVENT = {
    "eventType": "FAIL",
    "eventTime": "2024-01-15T10:02:00Z",
    "run": {
        "runId": "660f9500-f3ac-52e5-b827-557766551111",
        "facets": {
            "errorMessage": {
                "message": "column 'this_column_does_not_exist' not found",
                "programmingLanguage": "SQL",
            }
        },
    },
    "job": {"namespace": "hyrax-demo", "name": "fct_orders", "facets": {}},
    "inputs": [{"namespace": "hyrax-demo", "name": "stg_orders", "facets": {}}],
    "outputs": [{"namespace": "hyrax-demo", "name": "fct_orders", "facets": {}}],
}


# ── Helper ───────────────────────────────────────────────────────────────────

def _make_mock_tracer():
    """Return a mock tracer whose start_span returns a recordable mock span."""
    mock_span = MagicMock()
    mock_tracer = MagicMock()
    mock_tracer.start_span.return_value = mock_span
    return mock_tracer, mock_span


# ── Tests ────────────────────────────────────────────────────────────────────

def test_run_id_to_trace_id_is_deterministic():
    tid = _run_id_to_trace_id(RUN_ID)
    assert tid == _run_id_to_trace_id(RUN_ID)
    assert isinstance(tid, int)
    assert 0 < tid < 2**128


def test_iso_to_ns_parses_utc():
    ns = _iso_to_ns("2024-01-15T10:00:00Z")
    assert ns is not None
    assert ns > 0


def test_iso_to_ns_returns_none_on_empty():
    assert _iso_to_ns("") is None


def test_start_event_opens_span():
    converter._active_spans.clear()
    mock_tracer, mock_span = _make_mock_tracer()

    convert(START_EVENT, mock_tracer)

    mock_tracer.start_span.assert_called_once()
    call_kwargs = mock_tracer.start_span.call_args
    assert "stg_orders" in call_kwargs[0][0]  # span name contains job name
    assert RUN_ID in converter._active_spans


def test_complete_event_closes_span():
    converter._active_spans.clear()
    mock_tracer, mock_span = _make_mock_tracer()

    convert(START_EVENT, mock_tracer)
    convert(COMPLETE_EVENT, mock_tracer)

    mock_span.end.assert_called_once()
    assert RUN_ID not in converter._active_spans


def test_complete_sets_ok_status():
    converter._active_spans.clear()
    mock_tracer, mock_span = _make_mock_tracer()

    convert(START_EVENT, mock_tracer)
    convert(COMPLETE_EVENT, mock_tracer)

    mock_span.set_status.assert_called()
    status_arg = mock_span.set_status.call_args[0][0]
    from opentelemetry.trace import StatusCode
    assert status_arg.status_code == StatusCode.OK


def test_fail_event_sets_error_status():
    converter._active_spans.clear()
    mock_tracer, mock_span = _make_mock_tracer()

    convert(FAIL_EVENT, mock_tracer)

    mock_span.set_status.assert_called()
    status_arg = mock_span.set_status.call_args[0][0]
    from opentelemetry.trace import StatusCode
    assert status_arg.status_code == StatusCode.ERROR


def test_fail_event_sets_error_attribute():
    converter._active_spans.clear()
    mock_tracer, mock_span = _make_mock_tracer()

    convert(FAIL_EVENT, mock_tracer)

    set_attr_calls = {c[0][0]: c[0][1] for c in mock_span.set_attribute.call_args_list}
    assert "run.error" in set_attr_calls
    assert "not found" in set_attr_calls["run.error"]


def test_ownership_facet_applied():
    converter._active_spans.clear()
    mock_tracer, mock_span = _make_mock_tracer()

    convert(START_EVENT, mock_tracer)

    set_attr_calls = {c[0][0]: c[0][1] for c in mock_span.set_attribute.call_args_list}
    assert set_attr_calls.get("owner.team") == "data-platform-team"


def test_source_location_facet_applied():
    converter._active_spans.clear()
    mock_tracer, mock_span = _make_mock_tracer()

    convert(START_EVENT, mock_tracer)

    set_attr_calls = {c[0][0]: c[0][1] for c in mock_span.set_attribute.call_args_list}
    assert set_attr_calls.get("run.source_url") == "https://github.com/org/repo"


def test_output_statistics_applied():
    converter._active_spans.clear()
    mock_tracer, mock_span = _make_mock_tracer()

    convert(START_EVENT, mock_tracer)

    set_attr_calls = {c[0][0]: c[0][1] for c in mock_span.set_attribute.call_args_list}
    assert set_attr_calls.get("dataset.output.0.row_count") == 1000
    assert set_attr_calls.get("dataset.output.0.byte_size") == 65536


def test_schema_facet_applied():
    converter._active_spans.clear()
    mock_tracer, mock_span = _make_mock_tracer()

    convert(START_EVENT, mock_tracer)

    set_attr_calls = {c[0][0]: c[0][1] for c in mock_span.set_attribute.call_args_list}
    schema_val = set_attr_calls.get("dataset.output.0.schema", "")
    assert "order_id" in schema_val


def test_orphan_complete_creates_and_closes_span():
    """A COMPLETE with no prior START should still produce a closed span."""
    converter._active_spans.clear()
    mock_tracer, mock_span = _make_mock_tracer()

    convert(COMPLETE_EVENT, mock_tracer)

    mock_tracer.start_span.assert_called_once()
    mock_span.end.assert_called_once()
