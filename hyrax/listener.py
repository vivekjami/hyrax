"""
hyrax.listener
--------------
HTTP server exposing a single POST / endpoint, compatible with the standard
OPENLINEAGE_URL convention used by every OpenLineage-emitting orchestrator.

Flow: receive JSON → validate → convert → record metrics → respond 202.

Start with:
    python -m hyrax.listener          # default port 5050
    HYRAX_PORT=6060 python -m hyrax.listener
"""
import json
import logging
import os
import signal
import sys
import time
from typing import Any

from flask import Flask, Response, request

from hyrax import validator
from hyrax import converter
from hyrax import metrics as hyrax_metrics
from hyrax.exporter import get_tracer, shutdown as exporter_shutdown

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

# Metrics counters for the /health endpoint
_stats: dict[str, int] = {"received": 0, "accepted": 0, "dropped": 0, "errors": 0}

# Per-run start times for duration calculation (runId → start epoch ms)
_start_times: dict[str, float] = {}


@app.post("/")
def ingest() -> Response:
    """Main OpenLineage ingest endpoint. Compatible with OPENLINEAGE_URL."""
    _stats["received"] += 1

    # --- Parse ---
    try:
        event: dict[str, Any] = request.get_json(force=True, silent=False)
    except Exception:
        _stats["errors"] += 1
        return Response("Bad JSON\n", status=400, mimetype="text/plain")

    # --- Validate ---
    if not validator.is_valid(event):
        _stats["dropped"] += 1
        return Response("Event dropped (validation)\n", status=422, mimetype="text/plain")

    # --- Duration tracking ---
    run_id = event.get("run", {}).get("runId", "")
    event_type = event.get("eventType", "OTHER")
    duration_ms = None

    if event_type == "START":
        _start_times[run_id] = time.time() * 1000
    elif event_type in ("COMPLETE", "FAIL", "ABORT"):
        start = _start_times.pop(run_id, None)
        if start is not None:
            duration_ms = time.time() * 1000 - start

    # --- Convert (OpenLineage → OTel span) ---
    try:
        tracer = get_tracer()
        converter.convert(event, tracer)
    except Exception as exc:
        logger.exception("Conversion failed for run %s: %s", run_id, exc)
        _stats["errors"] += 1
        return Response("Conversion error\n", status=500, mimetype="text/plain")

    # --- Metrics ---
    try:
        hyrax_metrics.record(event, duration_ms=duration_ms)
    except Exception as exc:
        logger.warning("Metrics recording failed: %s", exc)

    _stats["accepted"] += 1
    return Response("", status=202)


@app.get("/health")
def health() -> Response:
    """Liveness probe. Returns stats for quick debugging."""
    body = {
        "status": "ok",
        "version": "0.1.0",
        "stats": _stats,
        "active_spans": len(converter._active_spans),
    }
    return Response(json.dumps(body), status=200, mimetype="application/json")


@app.get("/metrics-debug")
def metrics_debug() -> Response:
    """Returns current active span keys for debugging (disable in prod)."""
    if os.getenv("HYRAX_DEBUG_SPANS", "false").lower() != "true":
        return Response("Enable HYRAX_DEBUG_SPANS=true\n", status=403)
    return Response(
        json.dumps({"active_run_ids": list(converter._active_spans.keys())}),
        status=200,
        mimetype="application/json",
    )


def _handle_shutdown(signum, frame):
    logger.info("Shutting down — flushing spans…")
    exporter_shutdown()
    hyrax_metrics.shutdown()
    sys.exit(0)


def main():
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    port = int(os.getenv("HYRAX_PORT", "5050"))
    host = os.getenv("HYRAX_HOST", "0.0.0.0")
    debug = os.getenv("HYRAX_DEBUG", "false").lower() == "true"

    logger.info(
        "Hyrax listener starting on %s:%d  (OTLP → %s)",
        host,
        port,
        os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"),
    )
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()
