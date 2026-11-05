"""Build LLM routes from a spec such as 'groq:model-a,gemini:model-b'.

A paid model must have an entry in PRICES, or the cost ledger and money budgets would treat it as
free. Models without an entry, such as local Ollama models, cost zero.
"""

from collections.abc import Mapping

import httpx2

from da_agent.llm.adapters.gemini import GeminiAdapter
from da_agent.llm.adapters.openai_compatible import OpenAICompatibleAdapter
from da_agent.llm.client import ModelPrice, Route
from da_agent.llm.types import LLMAdapter

# A small local GPU can take minutes on a long prompt, especially when loading the model.
LOCAL_TIMEOUT_SECONDS = 900.0

# USD per million tokens, from console.groq.com/docs/model/openai/gpt-oss-20b on 2026-11-06.
PRICES: dict[tuple[str, str], ModelPrice] = {
    ("groq", "openai/gpt-oss-20b"): ModelPrice(
        input_per_million=0.075, output_per_million=0.30, cached_input_per_million=0.037
    ),
}


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
    raise ValueError(f"unknown provider {provider!r}, expected groq, gemini, or ollama")


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
