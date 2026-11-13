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


def test_the_public_url_defaults_to_the_local_compose_ui() -> None:
    assert Settings.from_env(REQUIRED).public_url == "http://127.0.0.1:8080"
    env = REQUIRED | {"DA_AGENT_PUBLIC_URL": "https://da.example/"}
    assert Settings.from_env(env).public_url == "https://da.example"


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"DA_AGENT_DATA_DIR": "/srv"}, "DA_AGENT_DATABASE_URL"),
        (REQUIRED | {"DA_AGENT_DAILY_BUDGET_USD": "lots"}, "DA_AGENT_DAILY_BUDGET_USD"),
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
