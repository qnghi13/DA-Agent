"""Settings for the API, the worker, and the admin commands, read from DA_AGENT_* variables.

| Variable | Default | Meaning |
|---|---|---|
| DA_AGENT_DATABASE_URL | required | Postgres URL |
| DA_AGENT_DATA_DIR | required | Holds uploads/, staging/, and datasets/ |
| DA_AGENT_SANDBOX_IMAGE | da-agent-sandbox:dev | Image the Runner starts |
| DA_AGENT_ROUTES | none; the worker needs it | Model routes, such as groq:openai/gpt-oss-20b |
| DA_AGENT_DAILY_BUDGET_USD | 5.0 | Model spending per workspace per day, unless it has its own |
| DA_AGENT_RUN_BUDGET_USD | 0.25 | Model spending per job |
| DA_AGENT_RUN_MAX_TOKENS | 60000 | Tokens per job |
| DA_AGENT_OTLP_ENDPOINT | none | Where traces go, such as http://phoenix:6006/v1/traces |

Provider keys (GROQ_API_KEY and others) are read by the model routes, and only by the worker.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(ValueError):
    """A setting is missing or invalid. The message names the variable."""


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is required")
    return value


def _number(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as error:
        raise ConfigError(f"{name} must be a number, not {raw!r}") from error
    if value < 0:
        raise ConfigError(f"{name} must not be negative")
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    data_dir: Path
    sandbox_image: str = "da-agent-sandbox:dev"
    routes: str | None = None
    daily_budget_usd: float = 5.0
    run_budget_usd: float = 0.25
    run_max_tokens: int = 60_000
    otlp_endpoint: str | None = None
    # For tests: confine every query to one Postgres schema.
    schema: str | None = None

    @property
    def uploads_root(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def staging_root(self) -> Path:
        return self.data_dir / "staging"

    @property
    def datasets_root(self) -> Path:
        return self.data_dir / "datasets"

    @classmethod
    def from_env(cls, env: Mapping[str, str], *, schema: str | None = None) -> "Settings":
        return cls(
            database_url=_required(env, "DA_AGENT_DATABASE_URL"),
            data_dir=Path(_required(env, "DA_AGENT_DATA_DIR")),
            sandbox_image=env.get("DA_AGENT_SANDBOX_IMAGE", "").strip() or cls.sandbox_image,
            routes=env.get("DA_AGENT_ROUTES", "").strip() or None,
            daily_budget_usd=_number(env, "DA_AGENT_DAILY_BUDGET_USD", cls.daily_budget_usd),
            run_budget_usd=_number(env, "DA_AGENT_RUN_BUDGET_USD", cls.run_budget_usd),
            run_max_tokens=int(_number(env, "DA_AGENT_RUN_MAX_TOKENS", cls.run_max_tokens)),
            otlp_endpoint=env.get("DA_AGENT_OTLP_ENDPOINT", "").strip() or None,
            schema=schema,
        )
