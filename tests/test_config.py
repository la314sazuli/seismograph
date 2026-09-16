from __future__ import annotations

from pathlib import Path

import pytest

from seismograph.config import ConfigError, load_config


def test_valid_environment_loads(env):
    config = load_config(env)
    assert config.source_channel_ids == (200000000000000011, 200000000000000012)
    assert config.analysis_days == 7
    assert config.retention_days == 30
    assert config.report_timezone == "UTC"
    assert config.llm_base_url == "https://llm.example.invalid/v1"


def test_primary_example_selects_sonar_without_changing_legacy_fallback(env):
    example = Path(__file__).resolve().parents[1] / ".env.example"
    values = dict(
        line.split("=", 1)
        for line in example.read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    assert values["LLM_PROCESSING_APPROVED"] == "false"
    configured = {
        **env,
        **{key: values[key] for key in ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_MODEL")},
    }
    config = load_config(configured)
    assert config.llm_provider == "sonar"
    assert config.llm_base_url == "https://api.perplexity.ai"
    assert config.llm_model == "sonar"
    env.pop("LLM_PROVIDER", None)
    assert load_config(env).llm_provider == "openai"


def test_trailing_slash_is_removed_from_the_base_url(env):
    env["LLM_BASE_URL"] = "https://llm.example.invalid/v1/"
    assert load_config(env).llm_base_url == "https://llm.example.invalid/v1"


@pytest.mark.parametrize(
    "missing",
    [
        "DISCORD_TOKEN",
        "DISCORD_GUILD_ID",
        "SOURCE_CHANNEL_IDS",
        "REPORT_CHANNEL_ID",
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "AUTHOR_HASH_SALT",
    ],
)
def test_missing_critical_configuration_fails_clearly(env, missing):
    del env[missing]
    with pytest.raises(ConfigError) as error:
        load_config(env)
    assert missing in str(error.value)


def test_all_missing_variables_are_reported_together(env):
    for key in ("DISCORD_TOKEN", "LLM_API_KEY", "LLM_MODEL"):
        env[key] = "  "
    with pytest.raises(ConfigError) as error:
        load_config(env)
    message = str(error.value)
    assert "DISCORD_TOKEN" in message and "LLM_API_KEY" in message and "LLM_MODEL" in message


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("SOURCE_CHANNEL_IDS", "not-a-number"),
        ("SOURCE_CHANNEL_IDS", " , "),
        ("DISCORD_GUILD_ID", "abc"),
        ("REPORT_CHANNEL_ID", "0"),
        ("ANALYSIS_DAYS", "0"),
        ("ANALYSIS_DAYS", "seven"),
        ("RETENTION_DAYS", "-3"),
        ("REPORT_TIMEZONE", "Mars/Olympus_Mons"),
        ("AUTHOR_HASH_SALT", "short"),
    ],
)
def test_unusable_values_fail_clearly(env, key, value):
    env[key] = value
    with pytest.raises(ConfigError) as error:
        load_config(env)
    assert key in str(error.value)


def test_optional_values_have_defaults(env):
    env.pop("REPORT_TIMEZONE", None)
    config = load_config(env)
    assert config.report_timezone == "UTC"
    assert config.database_path == "seismograph.db"


def test_channel_ids_accept_semicolons_and_spaces(env):
    env["SOURCE_CHANNEL_IDS"] = " 11 ; 12 , 13 "
    assert load_config(env).source_channel_ids == (11, 12, 13)


def test_config_is_immutable(env):
    config = load_config(env)
    with pytest.raises(AttributeError):
        config.discord_token = "other"  # type: ignore[misc]
