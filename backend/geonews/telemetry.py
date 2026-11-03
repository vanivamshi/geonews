import logging

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from geonews.config import get_settings

log = logging.getLogger("geonews")
tracer = trace.get_tracer("geonews")
_configured = False


def setup_telemetry(service: str) -> None:
    """OTLP traces (Langfuse/Grafana Tempo/Honeycomb all accept OTLP-HTTP) + stdlib logging."""
    global _configured
    if _configured:
        return
    _configured = True
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    settings = get_settings()
    provider = TracerProvider(resource=Resource.create({"service.name": service}))
    if settings.otel_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        headers = dict(
            kv.split("=", 1) for kv in settings.otel_headers.split(",") if "=" in kv
        )
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=f"{settings.otel_endpoint}/v1/traces", headers=headers)
            )
        )
    trace.set_tracer_provider(provider)


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None
