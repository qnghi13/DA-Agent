"""Tracing setup and building routes from configuration."""

import httpx2
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from da_agent.llm.factory import build_routes
from da_agent.llm.types import LLMRequest, Message, TransientLLMError
from da_agent.telemetry.tracing import configure_tracing

ENV = {"GROQ_API_KEY": "groq-secret", "GEMINI_API_KEY": "gemini-secret"}


def test_configure_tracing_exports_spans_with_the_service_name() -> None:
    exporter = InMemorySpanExporter()

    provider = configure_tracing("http://unused", service_name="svc", exporter=exporter)
    with provider.get_tracer("t").start_as_current_span("work"):
        pass
    provider.force_flush()

    span = exporter.get_finished_spans()[0]
    assert span.name == "work"
    assert span.resource.attributes["service.name"] == "svc"
    provider.shutdown()


def test_build_routes_keeps_order_and_models() -> None:
    routes = build_routes("groq:llama-x,gemini:gemini-y", ENV)

    assert [(route.adapter.provider, route.model) for route in routes] == [
        ("groq", "llama-x"),
        ("gemini", "gemini-y"),
    ]


def test_models_may_contain_slashes_and_colons() -> None:
    assert build_routes("groq:org/model:tag", ENV)[0].model == "org/model:tag"


def test_ollama_needs_no_key() -> None:
    assert build_routes("ollama:qwen", {})[0].adapter.provider == "ollama"


def test_local_models_get_a_longer_timeout_than_hosted_ones() -> None:
    local = build_routes("ollama:qwen", {})[0].adapter
    hosted = build_routes("groq:m", ENV)[0].adapter

    assert local.timeout_seconds >= 600
    assert hosted.timeout_seconds == 60


def test_missing_key_names_the_variable_and_never_a_value() -> None:
    with pytest.raises(ValueError, match="GEMINI_API_KEY") as caught:
        build_routes("groq:a,gemini:b", {"GROQ_API_KEY": "groq-secret"})

    assert "groq-secret" not in str(caught.value)


@pytest.mark.parametrize("spec", ["", "groq", "groq:", "nobody:model"])
def test_bad_specs_are_rejected(spec: str) -> None:
    with pytest.raises(ValueError):
        build_routes(spec, ENV)


def test_groq_adapter_points_at_groq() -> None:
    seen = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        return httpx2.Response(500, json={"error": {"message": "x"}})

    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    route = build_routes("groq:m", ENV, http_client=client)[0]

    with pytest.raises(TransientLLMError):
        route.adapter.complete(LLMRequest(messages=[Message(role="user", content="q")]), "m")

    assert seen["url"].startswith("https://api.groq.com/openai/v1/")
    assert seen["auth"] == "Bearer groq-secret"


def test_paid_groq_models_carry_their_price() -> None:
    route = build_routes("groq:openai/gpt-oss-20b", ENV)[0]
    assert route.price.input_per_million > 0
    assert route.price.output_per_million > 0


def test_unpriced_models_stay_free() -> None:
    assert build_routes("ollama:qwen", {})[0].price.input_per_million == 0
    assert build_routes("groq:unknown-model", ENV)[0].price.output_per_million == 0
