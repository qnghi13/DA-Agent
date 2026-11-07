"""Settings come from DA_AGENT_* environment variables, with defaults for everything optional."""

from pathlib import Path

import pytest

from da_agent.config import ConfigError, Settings

REQUIRED = {"DA_AGENT_DATABASE_URL": "postgresql://u:p@db/x", "DA_AGENT_DATA_DIR": "/srv/data"}


def test_required_settings_and_defaults() -> None:
    settings = Settings.from_env(REQUIRED)

    assert settings.database_url == "postgresql://u:p@db/x"
    assert settings.uploads_root == Path("/srv/data/uploads")
    assert settings.staging_root == Path("/srv/data/staging")
    assert settings.datasets_root == Path("/srv/data/datasets")
    assert settings.sandbox_image == "da-agent-sandbox:dev"
    assert settings.routes is None
    assert (settings.daily_budget_usd, settings.run_budget_usd) == (5.0, 0.25)


def test_optional_settings_are_read_and_checked() -> None:
    env = REQUIRED | {
        "DA_AGENT_ROUTES": "groq:openai/gpt-oss-20b",
        "DA_AGENT_DAILY_BUDGET_USD": "2.5",
        "DA_AGENT_RUN_MAX_TOKENS": "40000",
        "DA_AGENT_OTLP_ENDPOINT": "http://phoenix:6006/v1/traces",
    }

    settings = Settings.from_env(env)

    assert settings.routes == "groq:openai/gpt-oss-20b"
    assert settings.daily_budget_usd == 2.5
    assert settings.run_max_tokens == 40000
    assert settings.otlp_endpoint == "http://phoenix:6006/v1/traces"


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"DA_AGENT_DATA_DIR": "/srv"}, "DA_AGENT_DATABASE_URL"),
        (REQUIRED | {"DA_AGENT_DAILY_BUDGET_USD": "lots"}, "DA_AGENT_DAILY_BUDGET_USD"),
        (REQUIRED | {"DA_AGENT_RUN_BUDGET_USD": "-1"}, "DA_AGENT_RUN_BUDGET_USD"),
    ],
)
def test_bad_settings_name_the_variable(env: dict[str, str], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        Settings.from_env(env)


def test_the_database_password_is_not_shown() -> None:
    assert "p@" not in repr(Settings.from_env(REQUIRED))
