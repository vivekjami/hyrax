"""Tests for hyrax.validator"""
import pytest
from hyrax.validator import validate, is_valid, ValidationError


VALID_COMPLETE = {
    "eventType": "COMPLETE",
    "eventTime": "2024-01-15T10:30:00Z",
    "run": {"runId": "550e8400-e29b-41d4-a716-446655440000"},
    "job": {"namespace": "hyrax-demo", "name": "stg_orders"},
    "inputs": [{"namespace": "hyrax-demo", "name": "raw_orders", "facets": {}}],
    "outputs": [{"namespace": "hyrax-demo", "name": "stg_orders", "facets": {}}],
}

VALID_START = {
    "eventType": "START",
    "eventTime": "2024-01-15T10:00:00Z",
    "run": {"runId": "550e8400-e29b-41d4-a716-446655440000"},
    "job": {"namespace": "hyrax-demo", "name": "stg_orders"},
    "inputs": [],
    "outputs": [],
}


def test_valid_complete_passes():
    validate(VALID_COMPLETE)  # should not raise


def test_valid_start_passes():
    validate(VALID_START)  # START may have empty inputs/outputs


def test_missing_run_id_raises():
    bad = {**VALID_COMPLETE, "run": {"runId": ""}}
    with pytest.raises(ValidationError, match="run.runId"):
        validate(bad)


def test_invalid_uuid_raises():
    bad = {**VALID_COMPLETE, "run": {"runId": "not-a-uuid"}}
    with pytest.raises(ValidationError, match="not a valid UUID"):
        validate(bad)


def test_missing_job_namespace_raises():
    bad = {**VALID_COMPLETE, "job": {"name": "stg_orders"}}
    with pytest.raises(ValidationError, match="job.namespace"):
        validate(bad)


def test_complete_without_inputs_raises():
    bad = {**VALID_COMPLETE, "inputs": []}
    with pytest.raises(ValidationError, match="input dataset"):
        validate(bad)


def test_complete_without_outputs_raises():
    bad = {**VALID_COMPLETE, "outputs": []}
    with pytest.raises(ValidationError, match="output dataset"):
        validate(bad)


def test_invalid_event_type_raises():
    bad = {**VALID_COMPLETE, "eventType": "BOGUS"}
    with pytest.raises(ValidationError, match="eventType"):
        validate(bad)


def test_is_valid_returns_false_on_bad_event():
    assert is_valid({"eventType": "COMPLETE"}) is False


def test_is_valid_returns_true_on_good_event():
    assert is_valid(VALID_COMPLETE) is True
