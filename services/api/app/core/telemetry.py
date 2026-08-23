import logging

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.core.config import settings

logger = logging.getLogger(__name__)


def setup_telemetry() -> None:
    """Configure a global tracer provider that exports to the OTel Collector.

    Every span created anywhere in this service (and the worker, which does
    the same setup) shares the same collector -> Prometheus/Grafana pipeline,
    so a single event can be traced end-to-end: API receipt -> queue publish
    -> worker consume -> DB write.
    """
    resource = Resource.create({SERVICE_NAME: settings.otel_service_name})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    logging.basicConfig(level=settings.log_level)
    logger.info("Telemetry configured, exporting to %s", settings.otel_exporter_otlp_endpoint)


def instrument_app(app) -> None:
    FastAPIInstrumentor.instrument_app(app)


tracer = trace.get_tracer(__name__)


def current_trace_id_hex() -> str | None:
    """Trace id of the current span, for cross-referencing an event_log row
    with Grafana. Not propagated across the RabbitMQ header today, so this
    reflects the API request's trace, not the worker's later processing trace."""
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx and ctx.is_valid else None
