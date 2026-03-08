"""Tests for main.py CLI entry points — all external calls are mocked."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from zigporter.main import (
    _confirm_backup_once,
    _get_config,
    _get_z2m_config,
    _resolve_or_fetch_export,
    app,
)


runner = CliRunner()


# ---------------------------------------------------------------------------
# _get_config helpers
# ---------------------------------------------------------------------------


def test_get_config_success(mocker):
    mocker.patch("zigporter.main._ensure_config")
    mocker.patch("zigporter.main.load_config", return_value=("https://ha.test", "token", True))
    url, tok, ssl = _get_config()
    assert url == "https://ha.test"


def test_get_config_exits_on_error(mocker):
    import typer

    mocker.patch("zigporter.main._ensure_config")
    mocker.patch("zigporter.main.load_config", side_effect=ValueError("HA_URL missing"))
    mocker.patch("zigporter.main.console")
    with pytest.raises(typer.Exit):
        _get_config()


def test_get_z2m_config_success(mocker):
    mocker.patch("zigporter.main.load_z2m_config", return_value=("https://z2m.test", "zigbee2mqtt"))
    url, topic = _get_z2m_config()
    assert url == "https://z2m.test"


def test_get_z2m_config_exits_on_error(mocker):
    import typer

    mocker.patch("zigporter.main.load_z2m_config", side_effect=ValueError("Z2M_URL missing"))
    mocker.patch("zigporter.main.console")
    with pytest.raises(typer.Exit):
        _get_z2m_config()


def test_get_config_optional_returns_empty_on_error(mocker):
    mocker.patch("zigporter.main.load_config", side_effect=ValueError("missing"))
    url, tok, ssl = _get_config(optional=True)
    assert url == ""
    assert tok == ""
    assert ssl is True


def test_get_z2m_config_optional_returns_defaults_on_error(mocker):
    mocker.patch("zigporter.main.load_z2m_config", side_effect=ValueError("missing"))
    url, topic = _get_z2m_config(optional=True)
    assert url == ""
    assert topic == "zigbee2mqtt"


# ---------------------------------------------------------------------------
# _resolve_or_fetch_export
# ---------------------------------------------------------------------------


def test_resolve_or_fetch_export_explicit_path():
    explicit = Path("/some/explicit/export.json")
    result = _resolve_or_fetch_export(explicit, "https://ha.test", "token", True)
    assert result == explicit


def test_resolve_or_fetch_export_no_file_user_declines(tmp_path, mocker):
    import typer

    mocker.patch("zigporter.main.default_export_path", return_value=tmp_path / "export.json")
    mocker.patch("zigporter.main.console")
    mocker.patch("questionary.confirm", return_value=MagicMock(ask=MagicMock(return_value=False)))

    with pytest.raises(typer.Exit):
        _resolve_or_fetch_export(None, "https://ha.test", "token", True)


def test_resolve_or_fetch_export_existing_file_use_existing(tmp_path, mocker):
    export_file = tmp_path / "export.json"
    export_file.write_text('{"exported_at": "2026-01-01", "devices": []}')

    mocker.patch("zigporter.main.default_export_path", return_value=export_file)
    mocker.patch("zigporter.main.console")
    mocker.patch(
        "questionary.select",
        return_value=MagicMock(ask=MagicMock(return_value="use")),
    )

    result = _resolve_or_fetch_export(None, "https://ha.test", "token", True)
    assert result == export_file


def test_resolve_or_fetch_export_existing_file_none_choice_uses_existing(tmp_path, mocker):
    """None choice (Ctrl-C) falls back to using existing file."""
    export_file = tmp_path / "export.json"
    export_file.write_text('{"exported_at": "2026-01-01", "devices": []}')

    mocker.patch("zigporter.main.default_export_path", return_value=export_file)
    mocker.patch("zigporter.main.console")
    mocker.patch(
        "questionary.select",
        return_value=MagicMock(ask=MagicMock(return_value=None)),
    )

    result = _resolve_or_fetch_export(None, "https://ha.test", "token", True)
    assert result == export_file


def test_resolve_or_fetch_export_bad_json_handled(tmp_path, mocker):
    """Corrupt export JSON is handled gracefully (falls back to use existing)."""
    export_file = tmp_path / "export.json"
    export_file.write_text("not-valid-json")

    mocker.patch("zigporter.main.default_export_path", return_value=export_file)
    mocker.patch("zigporter.main.console")
    mocker.patch(
        "questionary.select",
        return_value=MagicMock(ask=MagicMock(return_value="use")),
    )

    result = _resolve_or_fetch_export(None, "https://ha.test", "token", True)
    assert result == export_file


# ---------------------------------------------------------------------------
# _confirm_backup_once
# ---------------------------------------------------------------------------


def test_confirm_backup_once_skips_when_marker_exists(tmp_path, mocker):
    marker = tmp_path / ".backup-confirmed"
    marker.write_text("already-confirmed\n")
    mocker.patch("zigporter.main.backup_confirmed_path", return_value=marker)
    confirm = mocker.patch("zigporter.main.questionary.confirm")

    _confirm_backup_once()

    confirm.assert_not_called()


def test_confirm_backup_once_declined_exits(tmp_path, mocker):
    import typer

    marker = tmp_path / ".backup-confirmed"
    mocker.patch("zigporter.main.backup_confirmed_path", return_value=marker)
    mocker.patch("zigporter.main.console")
    mocker.patch(
        "zigporter.main.questionary.confirm",
        return_value=MagicMock(ask=MagicMock(return_value=False)),
    )

    with pytest.raises(typer.Exit):
        _confirm_backup_once()

    assert not marker.exists()


def test_confirm_backup_once_confirmed_writes_marker(tmp_path, mocker):
    marker = tmp_path / ".backup-confirmed"
    mocker.patch("zigporter.main.backup_confirmed_path", return_value=marker)
    mocker.patch(
        "zigporter.main.questionary.confirm",
        return_value=MagicMock(ask=MagicMock(return_value=True)),
    )
    mocker.patch("zigporter.main.console")

    _confirm_backup_once()

    assert marker.exists()
    assert marker.read_text().strip()


def test_confirm_backup_once_marker_write_fails(tmp_path, mocker):
    """OSError writing the marker prints a warning but does not raise."""
    marker = tmp_path / ".backup-confirmed"
    mocker.patch("zigporter.main.backup_confirmed_path", return_value=marker)
    mocker.patch(
        "zigporter.main.questionary.confirm",
        return_value=MagicMock(ask=MagicMock(return_value=True)),
    )
    mock_console = mocker.patch("zigporter.main.console")
    mocker.patch("pathlib.Path.write_text", side_effect=OSError("read-only filesystem"))

    _confirm_backup_once()  # must not raise

    warning_calls = [str(c) for c in mock_console.print.call_args_list]
    assert any("Warning" in c for c in warning_calls)


# ---------------------------------------------------------------------------
# CLI commands via CliRunner
# ---------------------------------------------------------------------------


def test_version_flag():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0


def test_help_flag():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "zigporter" in result.output.lower()


def test_setup_command_success(mocker):
    mocker.patch("zigporter.main.setup_command", return_value=True)
    result = runner.invoke(app, ["setup"])
    assert result.exit_code == 0


def test_setup_command_cancelled(mocker):
    mocker.patch("zigporter.main.setup_command", return_value=False)
    result = runner.invoke(app, ["setup"])
    assert result.exit_code == 1


def test_check_command_passes(mocker):
    mocker.patch("zigporter.main._ensure_config")
    mocker.patch("zigporter.main.load_config", return_value=("https://ha.test", "tok", True))
    mocker.patch("zigporter.main.load_z2m_config", return_value=("https://z2m.test", "zigbee2mqtt"))
    mocker.patch("zigporter.main.check_command", return_value=True)
    result = runner.invoke(app, ["check"])
    assert result.exit_code == 0


def test_check_command_fails(mocker):
    mocker.patch("zigporter.main._ensure_config")
    mocker.patch("zigporter.main.load_config", return_value=("https://ha.test", "tok", True))
    mocker.patch("zigporter.main.load_z2m_config", return_value=("https://z2m.test", "zigbee2mqtt"))
    mocker.patch("zigporter.main.check_command", return_value=False)
    result = runner.invoke(app, ["check"])
    assert result.exit_code == 1


def test_export_command(mocker, tmp_path):
    mocker.patch("zigporter.main._ensure_config")
    mocker.patch("zigporter.main.load_config", return_value=("https://ha.test", "tok", True))
    mock_export = mocker.patch("zigporter.main.export_command")
    output = tmp_path / "out.json"
    result = runner.invoke(app, ["export", "--output", str(output)])
    assert result.exit_code == 0
    mock_export.assert_called_once()


def test_list_z2m_command(mocker):
    mocker.patch("zigporter.main._ensure_config")
    mocker.patch("zigporter.main.load_config", return_value=("https://ha.test", "tok", True))
    mocker.patch("zigporter.main.load_z2m_config", return_value=("https://z2m.test", "zigbee2mqtt"))
    mock_list = mocker.patch("zigporter.main.list_z2m_command")
    result = runner.invoke(app, ["list-z2m"])
    assert result.exit_code == 0
    mock_list.assert_called_once()


def test_inspect_command(mocker):
    mocker.patch("zigporter.main._ensure_config")
    mocker.patch("zigporter.main.load_config", return_value=("https://ha.test", "tok", True))
    mock_inspect = mocker.patch("zigporter.main.inspect_command")
    result = runner.invoke(app, ["inspect"])
    assert result.exit_code == 0
    mock_inspect.assert_called_once()


def test_migrate_command_requires_backup_confirmation(mocker, tmp_path):
    export_path = tmp_path / "export.json"
    state_path = tmp_path / "state.json"
    mocker.patch("zigporter.main._get_config", return_value=("https://ha.test", "tok", True))
    mocker.patch("zigporter.main._get_z2m_config", return_value=("https://z2m.test", "zigbee2mqtt"))
    mocker.patch("zigporter.main._resolve_or_fetch_export", return_value=export_path)
    mocker.patch("zigporter.main.default_state_path", return_value=state_path)
    mock_confirm = mocker.patch("zigporter.main._confirm_backup_once")
    mock_migrate = mocker.patch("zigporter.main.migrate_command")

    result = runner.invoke(app, ["migrate", "--skip-checks"])

    assert result.exit_code == 0
    mock_confirm.assert_called_once()
    mock_migrate.assert_called_once()


def test_migrate_status_skips_backup_confirmation(mocker, tmp_path):
    export_path = tmp_path / "export.json"
    state_path = tmp_path / "state.json"
    mocker.patch("zigporter.main._get_config", return_value=("https://ha.test", "tok", True))
    mocker.patch("zigporter.main._get_z2m_config", return_value=("https://z2m.test", "zigbee2mqtt"))
    mocker.patch("zigporter.main._resolve_or_fetch_export", return_value=export_path)
    mocker.patch("zigporter.main.default_state_path", return_value=state_path)
    mock_confirm = mocker.patch("zigporter.main._confirm_backup_once")
    mock_migrate = mocker.patch("zigporter.main.migrate_command")

    result = runner.invoke(app, ["migrate", "--status"])

    assert result.exit_code == 0
    mock_confirm.assert_not_called()
    mock_migrate.assert_called_once()


def test_ensure_config_no_env_runs_setup(tmp_path, mocker):
    """_ensure_config triggers setup wizard when no .env exists."""
    mocker.patch("zigporter.config.config_dir", return_value=tmp_path)
    mock_setup = mocker.patch("zigporter.main.setup_command", return_value=True)

    from zigporter.main import _ensure_config

    _ensure_config()

    mock_setup.assert_called_once()


def test_network_map_command_via_cli(mocker):
    """Invoking `network-map` through the CLI hits the lazy import inside the command."""
    mocker.patch("zigporter.main._get_config", return_value=("https://ha.test", "tok", True))
    mocker.patch("zigporter.main._get_z2m_config", return_value=("", "zigbee2mqtt"))
    mock_nm = mocker.patch("zigporter.commands.network_map.network_map_command")
    result = runner.invoke(app, ["network-map"])
    assert result.exit_code == 0
    mock_nm.assert_called_once()


def test_ensure_config_with_existing_env_skips_setup(tmp_path, mocker):
    env = tmp_path / ".env"
    env.write_text("HA_URL=https://ha.test\n")
    mocker.patch("zigporter.config.config_dir", return_value=tmp_path)
    mock_setup = mocker.patch("zigporter.main.setup_command")

    from zigporter.main import _ensure_config

    _ensure_config()

    mock_setup.assert_not_called()
