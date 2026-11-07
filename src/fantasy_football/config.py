"""Validated environment configuration with redacted ESPN credentials."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import os
from pathlib import Path

from dotenv import dotenv_values


class SettingsError(ValueError):
    """Raised when configuration is missing or structurally invalid."""


@dataclass(frozen=True, slots=True)
class EspnCredentials:
    """A complete private-league credential pair hidden from representations."""

    espn_s2: str = field(repr=False)
    swid: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated process settings required by the repository foundation."""

    league_id: int
    season: int
    log_level: str
    snapshot_db_path: Path = Path("outputs/fantasy_football.sqlite3")
    credentials: EspnCredentials | None = field(default=None, repr=False)
    manual_rule_config_path: Path | None = field(default=None, repr=False)


_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


def _required_positive_int(values: Mapping[str, str], key: str) -> int:
    raw = values.get(key, "").strip()
    if not raw:
        raise SettingsError(f"Missing required configuration key: {key}")
    try:
        value = int(raw)
    except ValueError as exc:
        raise SettingsError(f"Configuration key {key} must be an integer") from exc
    if value <= 0:
        raise SettingsError(f"Configuration key {key} must be greater than zero")
    return value


def _credentials(values: Mapping[str, str]) -> EspnCredentials | None:
    espn_s2 = values.get("ESPN_S2", "").strip()
    swid = values.get("ESPN_SWID", "").strip()
    if bool(espn_s2) != bool(swid):
        missing = "ESPN_SWID" if espn_s2 else "ESPN_S2"
        raise SettingsError(
            "Private-league credentials must be supplied as a complete pair; "
            f"missing {missing}"
        )
    if not espn_s2:
        return None
    return EspnCredentials(espn_s2=espn_s2, swid=swid)


def _snapshot_db_path(values: Mapping[str, str]) -> Path:
    raw = values.get("SNAPSHOT_DB_PATH", "outputs/fantasy_football.sqlite3").strip()
    if not raw:
        raise SettingsError("SNAPSHOT_DB_PATH must not be empty")
    return Path(raw)


def _manual_rule_config_path(
    values: Mapping[str, str], *, base_directory: Path
) -> Path | None:
    raw = values.get("FANTASY_FOOTBALL_RULE_CONFIG_PATH", "").strip()
    if not raw:
        return None
    configured = Path(raw).expanduser()
    if not configured.is_absolute():
        configured = base_directory / configured
    return configured.resolve(strict=False)


def load_settings(
    *,
    env_file: str | Path | None = ".env",
    environ: Mapping[str, str] | None = None,
    base_directory: str | Path | None = None,
) -> Settings:
    """Load and validate settings, with process environment taking precedence.

    ``env_file=None`` disables dotenv loading. An explicitly named dotenv file
    must exist; this prevents silently running with an unintended configuration.
    """

    file_values: dict[str, str] = {}
    if env_file is not None:
        path = Path(env_file)
        if not path.is_file():
            raise SettingsError(f"Configuration file does not exist: {path}")
        file_values = {
            key: value
            for key, value in dotenv_values(path).items()
            if value is not None
        }

    process_values = os.environ if environ is None else environ
    values = {**file_values, **process_values}
    configuration_base = (
        Path.cwd() if base_directory is None else Path(base_directory).expanduser()
    ).resolve(strict=False)
    log_level = values.get("LOG_LEVEL", "INFO").strip().upper()
    if log_level not in _LOG_LEVELS:
        allowed = ", ".join(sorted(_LOG_LEVELS))
        raise SettingsError(f"LOG_LEVEL must be one of: {allowed}")

    return Settings(
        league_id=_required_positive_int(values, "ESPN_LEAGUE_ID"),
        season=_required_positive_int(values, "ESPN_SEASON"),
        log_level=log_level,
        snapshot_db_path=_snapshot_db_path(values),
        credentials=_credentials(values),
        manual_rule_config_path=_manual_rule_config_path(
            values, base_directory=configuration_base
        ),
    )
