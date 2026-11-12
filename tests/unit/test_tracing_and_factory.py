"""Tracing setup and building routes from configuration."""

import json

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


def test_openrouter_points_at_openrouter_and_pins_the_provider() -> None:
    """A pinned provider keeps every request on the same weights, so eval runs compare."""
    seen: dict = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx2.Response(500, json={"error": {"message": "x"}})

    env = {"OPENROUTER_API_KEY": "or-secret", "OPENROUTER_PROVIDER": "deepseek"}
    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    route = build_routes("openrouter:deepseek/deepseek-v4.1-flash-1017", env, http_client=client)[0]

    with pytest.raises(TransientLLMError):
        route.adapter.complete(LLMRequest(messages=[Message(role="user", content="q")]), "m")

    assert seen["url"].startswith("https://openrouter.ai/api/v1/")
    assert seen["auth"] == "Bearer or-secret"
    assert seen["body"]["provider"] == {
        "order": ["deepseek"],
        "allow_fallbacks": False,
        "require_parameters": True,
    }


def test_openrouter_without_a_pinned_provider_still_requires_tool_support() -> None:
    seen: dict = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["body"] = json.loads(request.content)
        return httpx2.Response(500, json={"error": {"message": "x"}})

    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    route = build_routes("openrouter:m", {"OPENROUTER_API_KEY": "k"}, http_client=client)[0]

    with pytest.raises(TransientLLMError):
        route.adapter.complete(LLMRequest(messages=[Message(role="user", content="q")]), "m")

    assert seen["body"]["provider"] == {"require_parameters": True}


def test_openrouter_needs_its_key() -> None:
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        build_routes("openrouter:m", {})


def test_the_pinned_deepseek_model_carries_its_price() -> None:
    route = build_routes(
        "openrouter:deepseek/deepseek-v4.1-flash-1017", {"OPENROUTER_API_KEY": "k"}
    )[0]
    assert route.price.input_per_million > route.price.cached_input_per_million > 0
    assert route.price.output_per_million > 0


def _vllm_request_seen(env: dict[str, str]) -> dict:
    seen: dict = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx2.Response(500, json={"error": {"message": "x"}})

    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    route = build_routes("vllm:Qwen/Qwen3-14B", env, http_client=client)[0]
    with pytest.raises(TransientLLMError):
        route.adapter.complete(LLMRequest(messages=[Message(role="user", content="q")]), "m")
    return seen


def test_vllm_is_local_free_and_needs_no_key() -> None:
    route = build_routes("vllm:Qwen/Qwen3-14B", {})[0]
    assert route.adapter.provider == "vllm"
    assert route.price.input_per_million == 0
    assert route.adapter.timeout_seconds == build_routes("ollama:q", {})[0].adapter.timeout_seconds


def test_vllm_sends_a_fixed_seed_so_a_rerun_gives_the_same_answers() -> None:
    """The challenge re-runs the image and the two scores must agree."""
    seen = _vllm_request_seen({})
    assert seen["url"].startswith("http://localhost:8000/v1/")
    assert seen["body"]["seed"] == 0


def test_vllm_url_and_seed_come_from_the_environment() -> None:
    seen = _vllm_request_seen({"VLLM_BASE_URL": "http://gpu:9000/v1", "VLLM_SEED": "7"})
    assert seen["url"].startswith("http://gpu:9000/v1/")
    assert seen["body"]["seed"] == 7


def test_vllm_extra_body_turns_model_options_on_without_code_changes() -> None:
    """Qwen3 thinks by default; a run can turn it off through the chat template."""
    seen = _vllm_request_seen(
        {"VLLM_EXTRA_BODY": '{"chat_template_kwargs": {"enable_thinking": false}}'}
    )
    assert seen["body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert seen["body"]["seed"] == 0


def test_vllm_extra_body_must_be_a_json_object() -> None:
    with pytest.raises(ValueError, match="VLLM_EXTRA_BODY"):
        build_routes("vllm:m", {"VLLM_EXTRA_BODY": "[1]"})


def test_vllm_min_output_tokens_leaves_room_for_thinking() -> None:
    """Reasoning tokens count toward max_tokens, so a small cap would cut the answer off."""
    assert _vllm_request_seen({})["body"]["max_tokens"] == 2048
    seen = _vllm_request_seen({"VLLM_MIN_OUTPUT_TOKENS": "8192"})
    assert seen["body"]["max_tokens"] == 8192
