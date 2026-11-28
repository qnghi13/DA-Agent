"""Settings come from DA_AGENT_* environment variables, with defaults for everything optional."""

from pathlib import Path

import pytest

from da_agent.config import ConfigError, RunnerSettings, Settings

REQUIRED = {"DA_AGENT_DATABASE_URL": "postgresql://u:p@db/x", "DA_AGENT_DATA_DIR": "/srv/data"}


def test_required_settings_and_defaults() -> None:
    settings = Settings.from_env(REQUIRED)

    assert settings.database_url == "postgresql://u:p@db/x"
    assert settings.uploads_root == Path("/srv/data/uploads")
    assert settings.staging_root == Path("/srv/data/staging")
    assert settings.datasets_root == Path("/srv/data/datasets")
    assert settings.exports_root == Path("/srv/data/exports")
    assert settings.sandbox_image == "da-agent-sandbox:dev"
    assert settings.routes is None
    assert (settings.daily_budget_usd, settings.run_budget_usd) == (5.0, 0.25)


def test_optional_settings_are_read_and_checked() -> None:
    env = REQUIRED | {
        "DA_AGENT_ROUTES": "groq:openai/gpt-oss-20b",
        "DA_AGENT_DAILY_BUDGET_USD": "2.5",
        "DA_AGENT_RUN_MAX_TOKENS": "40000",
        "DA_AGENT_OTLP_ENDPOINT": "http://phoenix:6006/v1/traces",
        "DA_AGENT_TRACE_UI_URL": "http://127.0.0.1:6006/",
    }

    settings = Settings.from_env(env)

    assert settings.routes == "groq:openai/gpt-oss-20b"
    assert settings.daily_budget_usd == 2.5
    assert settings.run_max_tokens == 40000
    assert settings.otlp_endpoint == "http://phoenix:6006/v1/traces"
    assert settings.trace_ui_url == "http://127.0.0.1:6006"
    assert Settings.from_env(REQUIRED).trace_ui_url is None


def test_the_dataset_version_limit_defaults_to_twenty_and_can_be_set() -> None:
    assert Settings.from_env(REQUIRED).max_dataset_versions == 20
    env = REQUIRED | {"DA_AGENT_MAX_DATASET_VERSIONS": "5"}
    assert Settings.from_env(env).max_dataset_versions == 5


def test_the_public_url_defaults_to_the_local_compose_ui() -> None:
    assert Settings.from_env(REQUIRED).public_url == "http://127.0.0.1:8080"
    env = REQUIRED | {"DA_AGENT_PUBLIC_URL": "https://da.example/"}
    assert Settings.from_env(env).public_url == "https://da.example"


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"DA_AGENT_DATA_DIR": "/srv"}, "DA_AGENT_DATABASE_URL"),
        (REQUIRED | {"DA_AGENT_DAILY_BUDGET_USD": "lots"}, "DA_AGENT_DAILY_BUDGET_USD"),
        (REQUIRED | {"DA_AGENT_MAX_DATASET_VERSIONS": "0"}, "DA_AGENT_MAX_DATASET_VERSIONS"),
        (REQUIRED | {"DA_AGENT_RUN_BUDGET_USD": "-1"}, "DA_AGENT_RUN_BUDGET_USD"),
        (REQUIRED | {"DA_AGENT_TRACE_UI_URL": "javascript:alert(1)"}, "DA_AGENT_TRACE_UI_URL"),
        (REQUIRED | {"DA_AGENT_PUBLIC_URL": "da.example"}, "DA_AGENT_PUBLIC_URL"),
    ],
)
def test_bad_settings_name_the_variable(env: dict[str, str], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        Settings.from_env(env)


def test_the_database_password_is_not_shown() -> None:
    assert "p@" not in repr(Settings.from_env(REQUIRED))


TOKEN = "r" * 32


def test_the_worker_reaches_a_runner_service_with_a_token() -> None:
    env = REQUIRED | {"DA_AGENT_RUNNER_URL": "http://runner:8100/", "DA_AGENT_RUNNER_TOKEN": TOKEN}

    settings = Settings.from_env(env)

    assert settings.runner_url == "http://runner:8100"
    assert settings.runner_token == TOKEN
    assert TOKEN not in repr(settings)
    assert Settings.from_env(REQUIRED).runner_url is None


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ({"DA_AGENT_RUNNER_URL": "http://runner:8100"}, "DA_AGENT_RUNNER_TOKEN"),
        (
            {"DA_AGENT_RUNNER_URL": "http://runner:8100", "DA_AGENT_RUNNER_TOKEN": "short"},
            "at least 32",
        ),
        ({"DA_AGENT_SANDBOX_USER": "root"}, "DA_AGENT_SANDBOX_USER"),
        ({"DA_AGENT_SANDBOX_USER": "0:0"}, "DA_AGENT_SANDBOX_USER"),
    ],
)
def test_bad_runner_settings_name_the_variable(extra: dict[str, str], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        Settings.from_env(REQUIRED | extra)


def test_the_sandbox_user_defaults_to_nobody() -> None:
    assert Settings.from_env(REQUIRED).sandbox_user == "65534:65534"
    env = REQUIRED | {"DA_AGENT_SANDBOX_USER": "10001:10001"}
    assert Settings.from_env(env).sandbox_user == "10001:10001"


def test_the_runner_service_needs_no_database_or_provider_keys() -> None:
    env = {
        "DA_AGENT_DATA_DIR": "/data",
        "DA_AGENT_RUNNER_TOKEN": TOKEN,
        "DA_AGENT_DATA_VOLUME": "da-agent_data",
        "DA_AGENT_SANDBOX_USER": "10001:10001",
    }

    settings = RunnerSettings.from_env(env)
    config = settings.runner_config()

    assert settings.token == TOKEN and TOKEN not in repr(settings)
    assert config.data_volume == "da-agent_data" and config.data_root == Path("/data")
    assert config.datasets_root == Path("/data/datasets")
    assert config.staging_root == Path("/data/staging")
    assert config.uploads_root == Path("/data/uploads")
    assert config.user == "10001:10001"


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"DA_AGENT_RUNNER_TOKEN": TOKEN}, "DA_AGENT_DATA_DIR"),
        ({"DA_AGENT_DATA_DIR": "/data"}, "DA_AGENT_RUNNER_TOKEN"),
        (
            {"DA_AGENT_DATA_DIR": "/d", "DA_AGENT_RUNNER_TOKEN": TOKEN}
            | {"DA_AGENT_DATA_VOLUME": "a,b"},
            "DA_AGENT_DATA_VOLUME",
        ),
    ],
)
def test_bad_runner_service_settings_name_the_variable(env: dict[str, str], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        RunnerSettings.from_env(env)


def test_model_call_limits_are_off_by_default() -> None:
    from da_agent.llm.breaker import BreakerPolicy

    settings = Settings.from_env(REQUIRED)

    assert settings.route_limits == {}
    assert settings.rate_limiter == "memory"
    assert settings.breaker_policy == BreakerPolicy()


def test_model_call_limits_are_read() -> None:
    from da_agent.llm.breaker import BreakerPolicy
    from da_agent.llm.ratelimit import RouteLimit

    env = REQUIRED | {
        "DA_AGENT_ROUTE_LIMITS": '{"groq:openai/gpt-oss-20b": {"rpm": 30, "tpm": 8000}}',
        "DA_AGENT_RATE_LIMITER": "postgres",
        "DA_AGENT_BREAKER_FAILURES": "3",
        "DA_AGENT_BREAKER_COOLDOWN_SECONDS": "12.5",
    }

    settings = Settings.from_env(env)

    assert settings.route_limits == {"groq:openai/gpt-oss-20b": RouteLimit(rpm=30, tpm=8000)}
    assert settings.rate_limiter == "postgres"
    assert settings.breaker_policy == BreakerPolicy(failures=3, cooldown_seconds=12.5)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("DA_AGENT_ROUTE_LIMITS", "{not json"),
        ("DA_AGENT_ROUTE_LIMITS", '["groq:m"]'),
        ("DA_AGENT_ROUTE_LIMITS", '{"groq:m": {"rps": 3}}'),
        ("DA_AGENT_ROUTE_LIMITS", '{"groq:m": {"rpm": 0}}'),
        ("DA_AGENT_ROUTE_LIMITS", '{"groq:m": {"tpm": "many"}}'),
        ("DA_AGENT_ROUTE_LIMITS", '{"groq": {"rpm": 3}}'),
        ("DA_AGENT_RATE_LIMITER", "redis"),
        ("DA_AGENT_BREAKER_FAILURES", "0"),
        ("DA_AGENT_BREAKER_COOLDOWN_SECONDS", "-1"),
        ("DA_AGENT_BREAKER_COOLDOWN_SECONDS", "nan"),
        ("DA_AGENT_BREAKER_COOLDOWN_SECONDS", "inf"),
        ("DA_AGENT_BREAKER_FAILURES", "inf"),
        ("DA_AGENT_BREAKER_FAILURES", "1e400"),
        ("DA_AGENT_BREAKER_FAILURES", "2.5"),
    ],
)
def test_bad_model_call_limits_name_the_variable(name: str, value: str) -> None:
    with pytest.raises(ConfigError, match=name):
        Settings.from_env(REQUIRED | {name: value})


def test_the_run_deadline_is_read() -> None:
    assert Settings.from_env(REQUIRED).run_max_seconds == 300.0
    assert Settings.from_env(REQUIRED | {"DA_AGENT_RUN_MAX_SECONDS": "90"}).run_max_seconds == 90.0


@pytest.mark.parametrize("value", ["0", "-5", "nan", "inf", "soon"])
def test_a_bad_run_deadline_names_the_variable(value: str) -> None:
    with pytest.raises(ConfigError, match="DA_AGENT_RUN_MAX_SECONDS"):
        Settings.from_env(REQUIRED | {"DA_AGENT_RUN_MAX_SECONDS": value})


def test_the_metrics_port_is_off_unless_set() -> None:
    assert Settings.from_env(REQUIRED).metrics_port is None
    assert Settings.from_env(REQUIRED | {"DA_AGENT_METRICS_PORT": "9100"}).metrics_port == 9100
    for bad in ("0", "70000", "http"):
        with pytest.raises(ConfigError, match="DA_AGENT_METRICS_PORT"):
            Settings.from_env(REQUIRED | {"DA_AGENT_METRICS_PORT": bad})


def test_ingest_routes_default_to_none_and_can_name_a_stronger_model() -> None:
    assert Settings.from_env(REQUIRED).ingest_routes is None
    env = REQUIRED | {"DA_AGENT_INGEST_ROUTES": " groq:openai/gpt-oss-120b "}
    assert Settings.from_env(env).ingest_routes == "groq:openai/gpt-oss-120b"
