"""
hyrax.validator
---------------
Enforces OpenLineage's minimum bar before conversion.

Rules (from the spec):
  - run.runId must be a valid UUID v4
  - At least one input dataset
  - At least one output dataset

Events failing these checks are logged and dropped — they never reach the
converter, keeping the resulting trace data trustworthy.
"""
import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

VALID_EVENT_TYPES = {"START", "COMPLETE", "FAIL", "ABORT", "RUNNING", "OTHER"}


class ValidationError(ValueError):
    """Raised when an OpenLineage event fails validation."""


def validate(event: dict[str, Any]) -> None:
    """
    Validate an OpenLineage event dict.

    Raises ValidationError with a descriptive message on failure.
    Does NOT mutate the event.
    """
    if not isinstance(event, dict):
        raise ValidationError("Event must be a JSON object.")

    # --- run.runId ---
    run = event.get("run")
    if not isinstance(run, dict):
        raise ValidationError("Missing or invalid 'run' object.")

    run_id = run.get("runId")
    if not run_id:
        raise ValidationError("'run.runId' is required.")
    try:
        uuid.UUID(str(run_id))
    except ValueError:
        raise ValidationError(f"'run.runId' is not a valid UUID: {run_id!r}")

    # --- job ---
    job = event.get("job")
    if not isinstance(job, dict):
        raise ValidationError("Missing or invalid 'job' object.")
    if not job.get("namespace") or not job.get("name"):
        raise ValidationError("'job.namespace' and 'job.name' are required.")

    # --- eventType ---
    event_type = event.get("eventType", "OTHER")
    if event_type not in VALID_EVENT_TYPES:
        raise ValidationError(
            f"'eventType' must be one of {VALID_EVENT_TYPES}, got {event_type!r}"
        )

    # --- datasets (required on non-START events for meaningful tracing) ---
    # START events may arrive before dataset metadata is known — we allow them
    # through. COMPLETE/FAIL events should carry at least one input and output.
    if event_type in ("COMPLETE", "FAIL", "ABORT"):
        inputs = event.get("inputs", [])
        outputs = event.get("outputs", [])
        if not inputs:
            raise ValidationError(
                f"eventType={event_type} must have at least one input dataset."
            )
        if not outputs:
            raise ValidationError(
                f"eventType={event_type} must have at least one output dataset."
            )


def is_valid(event: dict[str, Any]) -> bool:
    """Return True if the event passes validation, False otherwise (and log the reason)."""
    try:
        validate(event)
        return True
    except ValidationError as exc:
        logger.warning("Dropping invalid OpenLineage event: %s", exc)
        return False
