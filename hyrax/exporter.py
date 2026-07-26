"""
hyrax.exporter
--------------
OTLP span exporter wired to a configurable endpoint (default: SigNoz's
otel-collector on grpc://localhost:4317).

Uses BatchSpanProcessor for efficiency and retries with backoff on export
failure — a pipeline's own retry storm shouldn't become Hyrax's downtime.
"""
import logging
import os

from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

logger = logging.getLogger(__name__)

_provider: TracerProvider | None = None


def get_tracer_provider() -> TracerProvider:
    """Return the singleton TracerProvider, initialising it on first call."""
    global _provider
    if _provider is not None:
        return _provider

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    insecure = os.getenv("OTEL_EXPORTER_OTLP_INSECURE", "true").lower() == "true"

    resource = Resource.create(
        {
            SERVICE_NAME: "hyrax",
            SERVICE_VERSION: "0.1.0",
            "hyrax.version": "0.1.0",
        }
    )

    otlp_exporter = OTLPSpanExporter(
        endpoint=endpoint,
        insecure=insecure,
    )

    _provider = TracerProvider(resource=resource)
    _provider.add_span_processor(
        BatchSpanProcessor(
            otlp_exporter,
            max_export_batch_size=int(os.getenv("HYRAX_BATCH_SIZE", "64")),
            export_timeout_millis=int(os.getenv("HYRAX_EXPORT_TIMEOUT_MS", "10000")),
        )
    )

    # Optionally echo spans to stdout for local debugging
    if os.getenv("HYRAX_DEBUG_SPANS", "false").lower() == "true":
        _provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    logger.info("OTLP exporter initialised → %s (insecure=%s)", endpoint, insecure)
    return _provider


def get_tracer(name: str = "hyrax.converter"):
    """Get a tracer from the singleton provider."""
    return get_tracer_provider().get_tracer(name)


def shutdown() -> None:
    """Flush and shut down the provider gracefully (call on process exit)."""
    global _provider
    if _provider:
        _provider.shutdown()
        _provider = None
