"""Build LLM routes from a spec such as 'groq:model-a,openrouter:org/model-b'.

A paid model must have an entry in PRICES, or the cost ledger and money budgets would treat it as
free. Models without an entry, such as local Ollama models, cost zero.
"""

import json
from collections.abc import Mapping
from typing import Any

import httpx2

from da_agent.llm.adapters.gemini import GeminiAdapter
from da_agent.llm.adapters.openai_compatible import OpenAICompatibleAdapter
from da_agent.llm.client import ModelPrice, Route
from da_agent.llm.types import LLMAdapter

# A small local GPU can take minutes on a long prompt, especially when loading the model.
LOCAL_TIMEOUT_SECONDS = 900.0

# USD per million tokens. Groq from console.groq.com/docs/model/openai/gpt-oss-20b on 2026-11-06.
# OpenRouter from its endpoints API on 2026-11-07, for the DeepSeek provider: other providers of the
# same model charge differently, so pin OPENROUTER_PROVIDER=deepseek when using this price.
PRICES: dict[tuple[str, str], ModelPrice] = {
    ("groq", "openai/gpt-oss-20b"): ModelPrice(
        input_per_million=0.075, output_per_million=0.30, cached_input_per_million=0.037
    ),
    ("openrouter", "deepseek/deepseek-v4.1-flash-1017"): ModelPrice(
        input_per_million=0.15, output_per_million=0.60, cached_input_per_million=0.003
    ),
}


def _openrouter_routing(env: Mapping[str, str]) -> dict[str, object]:
    """Only providers that support every request field, such as tools. With OPENROUTER_PROVIDER
    set, only that provider, so weights and prices stay the same across eval runs."""
    routing: dict[str, object] = {"require_parameters": True}
    pinned = env.get("OPENROUTER_PROVIDER")
    if pinned:
        routing = {"order": [pinned], "allow_fallbacks": False, **routing}
    return {"provider": routing}


def _vllm_extra_body(env: Mapping[str, str]) -> dict[str, Any]:
    """The seed, then any options in VLLM_EXTRA_BODY, such as chat_template_kwargs to turn a
    model's thinking off. A fixed seed makes sampling repeatable, so a re-run of the same image
    gives the same answers; the server must also run one fixed configuration."""
    body: dict[str, Any] = {"seed": int(env.get("VLLM_SEED", "0"))}
    raw = env.get("VLLM_EXTRA_BODY")
    if raw:
        extra = json.loads(raw)
        if not isinstance(extra, dict):
            raise ValueError("VLLM_EXTRA_BODY must be a JSON object")
        body.update(extra)
    return body


def _require_key(env: Mapping[str, str], name: str) -> str:
    key = env.get(name)
    if not key:
        raise ValueError(f"{name} is not set. Add it to .env and run with: uv run --env-file .env")
    return key


def _build_adapter(
    provider: str, env: Mapping[str, str], http_client: httpx2.Client | None
) -> LLMAdapter:
    if provider == "groq":
        return OpenAICompatibleAdapter(
            provider="groq",
            base_url="https://api.groq.com/openai/v1",
            api_key=_require_key(env, "GROQ_API_KEY"),
            http_client=http_client,
        )
    if provider == "openrouter":
        return OpenAICompatibleAdapter(
            provider="openrouter",
            base_url="https://openrouter.ai/api/v1",
            api_key=_require_key(env, "OPENROUTER_API_KEY"),
            http_client=http_client,
            extra_body=_openrouter_routing(env),
        )
    if provider == "gemini":
        return GeminiAdapter(api_key=_require_key(env, "GEMINI_API_KEY"))
    if provider == "ollama":
        return OpenAICompatibleAdapter(
            provider="ollama",
            base_url=env.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            api_key="ollama",
            timeout=LOCAL_TIMEOUT_SECONDS,
            http_client=http_client,
        )
    if provider == "vllm":
        return OpenAICompatibleAdapter(
            provider="vllm",
            base_url=env.get("VLLM_BASE_URL", "http://localhost:8000/v1"),
            api_key=env.get("VLLM_API_KEY", "EMPTY"),
            timeout=LOCAL_TIMEOUT_SECONDS,
            http_client=http_client,
            extra_body=_vllm_extra_body(env),
            # Reasoning tokens count toward max_tokens, so thinking models need more room.
            min_output_tokens=int(env.get("VLLM_MIN_OUTPUT_TOKENS", "0")),
        )
    raise ValueError(
        f"unknown provider {provider!r}, expected groq, openrouter, gemini, ollama, or vllm"
    )


def build_routes(
    spec: str, env: Mapping[str, str], http_client: httpx2.Client | None = None
) -> list[Route]:
    routes = []
    for entry in spec.split(","):
        provider, _, model = entry.strip().partition(":")
        if not provider or not model:
            raise ValueError(f"route {entry!r} must look like provider:model")
        price = PRICES.get((provider, model), ModelPrice())
        routes.append(Route(_build_adapter(provider, env, http_client), model, price))
    return routes
