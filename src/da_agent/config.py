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
| DA_AGENT_TRACE_UI_URL | none | Trace viewer runs link to, such as http://127.0.0.1:6006 |
| DA_AGENT_RUNNER_URL | none | Runner service; unset runs sandboxes from the worker itself |
| DA_AGENT_RUNNER_TOKEN | none; required with a URL | Shared by worker and Runner, 32+ chars |
| DA_AGENT_SANDBOX_USER | 65534:65534 | uid:gid of sandboxes; never 0 |
| DA_AGENT_DATA_VOLUME | none | Runner only: named volume holding DA_AGENT_DATA_DIR |

Set DA_AGENT_TRACE_UI_URL only for operators: Phoenix shows every workspace's traces.

Provider keys (GROQ_API_KEY and others) are read by the model routes, and only by the worker.
The Runner service (python -m da_agent.runner, ADR 0024) reads only DA_AGENT_DATA_DIR,
DA_AGENT_SANDBOX_IMAGE, DA_AGENT_SANDBOX_USER, DA_AGENT_DATA_VOLUME, and DA_AGENT_RUNNER_TOKEN.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from da_agent.runner.runner import RunnerConfig

MIN_TOKEN_CHARS = 32
DEFAULT_SANDBOX_USER = "65534:65534"


class ConfigError(ValueError):
    """A setting is missing or invalid. The message names the variable."""


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is required")
    return value


def _http_url(env: Mapping[str, str], name: str) -> str | None:
    raw = env.get(name, "").strip()
    if not raw:
        return None
    if not raw.startswith(("http://", "https://")):
        raise ConfigError(f"{name} must be an http or https URL")
    return raw.rstrip("/")


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


def _token(env: Mapping[str, str], name: str) -> str:
    value = _required(env, name)
    if len(value) < MIN_TOKEN_CHARS:
        raise ConfigError(f"{name} must be at least {MIN_TOKEN_CHARS} characters")
    return value


def _sandbox_user(env: Mapping[str, str]) -> str:
    name = "DA_AGENT_SANDBOX_USER"
    value = env.get(name, "").strip() or DEFAULT_SANDBOX_USER
    match = re.fullmatch(r"(\d+):(\d+)", value)
    if match is None or int(match[1]) == 0 or int(match[2]) == 0:
        raise ConfigError(f"{name} must be a non-root uid:gid, such as 65534:65534")
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
    trace_ui_url: str | None = None
    runner_url: str | None = None
    runner_token: str | None = field(default=None, repr=False)
    sandbox_user: str = DEFAULT_SANDBOX_USER
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
            trace_ui_url=_http_url(env, "DA_AGENT_TRACE_UI_URL"),
            runner_url=(runner_url := _http_url(env, "DA_AGENT_RUNNER_URL")),
            runner_token=_token(env, "DA_AGENT_RUNNER_TOKEN") if runner_url else None,
            sandbox_user=_sandbox_user(env),
            schema=schema,
        )


@dataclass(frozen=True)
class RunnerSettings:
    """The Runner service's settings. It gets no database URL and no provider keys."""

    data_dir: Path
    token: str = field(repr=False)
    sandbox_image: str = "da-agent-sandbox:dev"
    sandbox_user: str = DEFAULT_SANDBOX_USER
    data_volume: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "RunnerSettings":
        volume = env.get("DA_AGENT_DATA_VOLUME", "").strip() or None
        if volume is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", volume):
            raise ConfigError("DA_AGENT_DATA_VOLUME must be a plain volume name")
        return cls(
            data_dir=Path(_required(env, "DA_AGENT_DATA_DIR")),
            token=_token(env, "DA_AGENT_RUNNER_TOKEN"),
            sandbox_image=env.get("DA_AGENT_SANDBOX_IMAGE", "").strip() or cls.sandbox_image,
            sandbox_user=_sandbox_user(env),
            data_volume=volume,
        )

    def runner_config(self) -> "RunnerConfig":
        from da_agent.runner.runner import RunnerConfig

        return RunnerConfig(
            image=self.sandbox_image,
            datasets_root=self.data_dir / "datasets",
            uploads_root=self.data_dir / "uploads",
            staging_root=self.data_dir / "staging",
            user=self.sandbox_user,
            data_volume=self.data_volume,
            data_root=self.data_dir if self.data_volume else None,
        )
