"""OpenTelemetry setup. Spans go to any OTLP endpoint, such as Phoenix."""

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter


def configure_tracing(
    endpoint: str,
    service_name: str = "da-agent",
    exporter: SpanExporter | None = None,
    set_global: bool = False,
) -> TracerProvider:
    """Build a provider that exports to endpoint. Call shutdown() on it before exiting."""
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(exporter or OTLPSpanExporter(endpoint=endpoint)))
    if set_global:
        trace.set_tracer_provider(provider)
    return provider
