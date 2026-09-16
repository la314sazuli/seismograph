"""Environment-based configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigError(Exception):
    """Raised when configuration is missing or unusable."""


@dataclass(frozen=True)
class Config:
    discord_token: str
    guild_id: int
    source_channel_ids: tuple[int, ...]
    report_channel_id: int
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    report_timezone: str
    analysis_days: int
    retention_days: int
    author_hash_salt: str
    database_path: str
    max_messages: int = 20_000
    max_llm_requests: int = 200
    report_weekday: int = 0
    report_hour: int = 9


def _int_list(raw: str, name: str) -> tuple[int, ...]:
    values = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            values.append(_int(part, name, 1))
        except ValueError as exc:
            raise ConfigError(f"{name} contains a non-numeric id: {part!r}") from exc
    if not values:
        raise ConfigError(f"{name} must list at least one channel id")
    return tuple(dict.fromkeys(values))


def _int(raw: str, name: str, minimum: int) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def load_config(env: dict[str, str] | None = None) -> Config:
    """Build a Config from the environment, raising ConfigError on the first problem.

    All missing required variables are reported together so operators can fix
    them in one pass instead of discovering them one restart at a time.
    """
    env = dict(os.environ if env is None else env)

    required = (
        "DISCORD_TOKEN",
        "DISCORD_GUILD_ID",
        "SOURCE_CHANNEL_IDS",
        "REPORT_CHANNEL_ID",
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "AUTHOR_HASH_SALT",
    )
    missing = [key for key in required if not env.get(key, "").strip()]
    if missing:
        raise ConfigError("Missing required environment variables: " + ", ".join(missing))

    timezone = env.get("REPORT_TIMEZONE", "UTC").strip() or "UTC"
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"REPORT_TIMEZONE is not a known timezone: {timezone!r}") from exc

    salt = env["AUTHOR_HASH_SALT"].strip()
    if len(salt) < 16:
        raise ConfigError("AUTHOR_HASH_SALT must be at least 16 characters")

    source_ids = _int_list(env["SOURCE_CHANNEL_IDS"], "SOURCE_CHANNEL_IDS")
    report_id = _int(env["REPORT_CHANNEL_ID"].strip(), "REPORT_CHANNEL_ID", 1)
    if report_id in source_ids:
        raise ConfigError("REPORT_CHANNEL_ID must not be a source channel")
    url = urlsplit(env["LLM_BASE_URL"])
    if not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ConfigError("LLM_BASE_URL must be a base URL without credentials or query parameters")
    if url.scheme != "https" and not (
        url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        raise ConfigError("LLM_BASE_URL requires HTTPS except for a local model")
    weekday = _int(env.get("REPORT_WEEKDAY", "0"), "REPORT_WEEKDAY", 0)
    hour = _int(env.get("REPORT_HOUR", "9"), "REPORT_HOUR", 0)
    if weekday > 6 or hour > 23:
        raise ConfigError("REPORT_WEEKDAY must be 0-6; REPORT_HOUR must be 0-23")
    days = _int(env.get("ANALYSIS_DAYS", "7"), "ANALYSIS_DAYS", 1)
    retention = _int(env.get("RETENTION_DAYS", "30"), "RETENTION_DAYS", 1)
    if retention < days + 7:
        raise ConfigError("RETENTION_DAYS must cover ANALYSIS_DAYS plus 7 days for catch-up")
    return Config(
        discord_token=env["DISCORD_TOKEN"].strip(),
        guild_id=_int(env["DISCORD_GUILD_ID"].strip(), "DISCORD_GUILD_ID", 1),
        source_channel_ids=source_ids,
        report_channel_id=report_id,
        llm_api_key=env["LLM_API_KEY"].strip(),
        llm_base_url=env["LLM_BASE_URL"].strip().rstrip("/"),
        llm_model=env["LLM_MODEL"].strip(),
        report_timezone=timezone,
        analysis_days=days,
        retention_days=retention,
        author_hash_salt=salt,
        database_path=env.get("DATABASE_PATH", "seismograph.db").strip() or "seismograph.db",
        max_messages=_int(env.get("MAX_MESSAGES", "20000"), "MAX_MESSAGES", 1),
        max_llm_requests=_int(env.get("MAX_LLM_REQUESTS", "200"), "MAX_LLM_REQUESTS", 1),
        report_weekday=weekday,
        report_hour=hour,
    )
