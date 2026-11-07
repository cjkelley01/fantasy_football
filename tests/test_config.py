from __future__ import annotations

from pathlib import Path

import pytest

from fantasy_football.config import SettingsError, load_settings


VALID_ENV = {
    "ESPN_LEAGUE_ID": "123456",
    "ESPN_SEASON": "2026",
    "LOG_LEVEL": "INFO",
}


def test_load_settings_without_credentials() -> None:
    settings = load_settings(env_file=None, environ=VALID_ENV)

    assert settings.league_id == 123456
    assert settings.season == 2026
    assert settings.log_level == "INFO"
    assert settings.credentials is None
    assert settings.snapshot_db_path == Path("outputs/fantasy_football.sqlite3")


def test_snapshot_database_path_is_configurable() -> None:
    settings = load_settings(env_file=None, environ={**VALID_ENV, "SNAPSHOT_DB_PATH": "private/state.sqlite3"})
    assert settings.snapshot_db_path == Path("private/state.sqlite3")


def test_empty_snapshot_database_path_is_rejected() -> None:
    with pytest.raises(SettingsError, match="SNAPSHOT_DB_PATH"):
        load_settings(env_file=None, environ={**VALID_ENV, "SNAPSHOT_DB_PATH": "  "})


def test_process_environment_overrides_dotenv(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ESPN_LEAGUE_ID=111\nESPN_SEASON=2025\nLOG_LEVEL=WARNING\n",
        encoding="utf-8",
    )

    settings = load_settings(
        env_file=env_file,
        environ={"ESPN_LEAGUE_ID": "222", "ESPN_SEASON": "2026"},
    )

    assert settings.league_id == 222
    assert settings.season == 2026
    assert settings.log_level == "WARNING"


def test_manual_rule_path_loads_from_dotenv_relative_to_configuration_base(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ESPN_LEAGUE_ID=111\nESPN_SEASON=2026\n"
        "FANTASY_FOOTBALL_RULE_CONFIG_PATH=config/rules.yaml\n",
        encoding="utf-8",
    )

    settings = load_settings(
        env_file=env_file, environ={}, base_directory=tmp_path
    )

    assert settings.manual_rule_config_path == (tmp_path / "config/rules.yaml").resolve()


def test_manual_rule_path_loads_from_process_environment(tmp_path: Path) -> None:
    configured = tmp_path / "process-rules.yaml"
    settings = load_settings(
        env_file=None,
        environ={
            **VALID_ENV,
            "FANTASY_FOOTBALL_RULE_CONFIG_PATH": str(configured),
        },
    )

    assert settings.manual_rule_config_path == configured.resolve()


def test_process_manual_rule_path_overrides_dotenv(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ESPN_LEAGUE_ID=111\nESPN_SEASON=2026\n"
        "FANTASY_FOOTBALL_RULE_CONFIG_PATH=config/from-file.yaml\n",
        encoding="utf-8",
    )

    settings = load_settings(
        env_file=env_file,
        environ={"FANTASY_FOOTBALL_RULE_CONFIG_PATH": "config/from-process.yaml"},
        base_directory=tmp_path,
    )

    assert settings.manual_rule_config_path == (
        tmp_path / "config/from-process.yaml"
    ).resolve()


def test_manual_rule_path_is_optional() -> None:
    settings = load_settings(env_file=None, environ=VALID_ENV)

    assert settings.manual_rule_config_path is None


@pytest.mark.parametrize("missing_key", ["ESPN_LEAGUE_ID", "ESPN_SEASON"])
def test_missing_required_value_fails(missing_key: str) -> None:
    values = {key: value for key, value in VALID_ENV.items() if key != missing_key}

    with pytest.raises(SettingsError, match=missing_key):
        load_settings(env_file=None, environ=values)


def test_partial_credentials_fail() -> None:
    values = {**VALID_ENV, "ESPN_S2": "secret-cookie"}

    with pytest.raises(SettingsError, match="missing ESPN_SWID"):
        load_settings(env_file=None, environ=values)


def test_credentials_are_redacted_from_representations() -> None:
    values = {
        **VALID_ENV,
        "ESPN_S2": "secret-s2",
        "ESPN_SWID": "{secret-swid}",
    }

    settings = load_settings(env_file=None, environ=values)

    assert settings.credentials is not None
    assert "secret-s2" not in repr(settings)
    assert "secret-s2" not in repr(settings.credentials)
    assert "secret-swid" not in repr(settings)
    assert "secret-swid" not in repr(settings.credentials)


def test_invalid_log_level_fails() -> None:
    with pytest.raises(SettingsError, match="LOG_LEVEL"):
        load_settings(env_file=None, environ={**VALID_ENV, "LOG_LEVEL": "verbose"})


def test_explicit_missing_env_file_fails(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="does not exist"):
        load_settings(env_file=tmp_path / "missing.env", environ={})
