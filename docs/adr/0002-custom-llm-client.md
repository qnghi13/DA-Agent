# 0002: Custom LLMClient

**Status:** Accepted, 2026-11-05

**Context:** The project moves between free tiers, paid Claude, and self-hosted models. It must
control caching, retries, fallback, and cost per run.

**Decision:** Nodes call our own `LLMClient`. Each provider uses its native SDK, with one shared
adapter for OpenAI-compatible endpoints (Groq, vLLM, Ollama, DeepSeek).

**Consequences:**
- Provider features such as prompt caching stay reachable.
- Cost records and budget stops live in one place.
- We maintain the adapters and a price table.

**Rejected:**
- LangChain model wrappers: LangGraph does not require them, and they add a layer between us and
  provider features.
- LiteLLM: convenient, but hides the retry and cost logic this project exists to teach.
