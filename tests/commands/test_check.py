from unittest.mock import AsyncMock, MagicMock, patch


from zigporter.commands.check import (
    _check_config,
    _check_ha_reachable,
    _check_z2m_running,
    _check_zha_active,
    check_command,
)
from zigporter.models import CheckStatus


# ---------------------------------------------------------------------------
# Individual check unit tests
# ---------------------------------------------------------------------------


async def test_check_config_all_present():
    result = await _check_config("https://ha.test", "token", "https://z2m.test")
    assert result.status == CheckStatus.OK


async def test_check_config_missing_values():
    result = await _check_config("", "", "")
    assert result.status == CheckStatus.FAILED
    assert "HA_URL" in result.message
    assert "HA_TOKEN" in result.message
    assert "Z2M_URL" in result.message


async def test_check_config_partial_missing():
    result = await _check_config("https://ha.test", "", "https://z2m.test")
    assert result.status == CheckStatus.FAILED
    assert "HA_TOKEN" in result.message
    assert "HA_URL" not in result.message


async def test_check_ha_reachable_skipped_when_no_url():
    result = await _check_ha_reachable("", "token", True)
    assert result.status == CheckStatus.SKIPPED


async def test_check_ha_reachable_success():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    with patch("zigporter.commands.check.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await _check_ha_reachable("https://ha.test", "token", True)

    assert result.status == CheckStatus.OK
    assert "ha.test" in result.message


async def test_check_ha_reachable_failure():
    with patch("zigporter.commands.check.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=RuntimeError("Connection refused"))
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await _check_ha_reachable("https://ha.test", "token", True)

    assert result.status == CheckStatus.FAILED
    assert "Connection refused" in result.message


async def test_check_zha_active_skipped_when_no_url():
    result = await _check_zha_active("", "token", True)
    assert result.status == CheckStatus.SKIPPED


async def test_check_zha_active_success():
    mock_ha_client = MagicMock()
    mock_ha_client.get_zha_devices = AsyncMock(
        return_value=[{"ieee": "0x1234"}, {"ieee": "0x5678"}]
    )

    with patch("zigporter.commands.check.HAClient", return_value=mock_ha_client):
        result = await _check_zha_active("https://ha.test", "token", True)

    assert result.status == CheckStatus.OK
    assert "2" in result.message


async def test_check_zha_active_no_devices_is_warning():
    mock_ha_client = MagicMock()
    mock_ha_client.get_zha_devices = AsyncMock(return_value=[])

    with patch("zigporter.commands.check.HAClient", return_value=mock_ha_client):
        result = await _check_zha_active("https://ha.test", "token", True)

    assert result.status == CheckStatus.WARNING
    assert result.blocking is False


async def test_check_zha_active_failure():
    mock_ha_client = MagicMock()
    mock_ha_client.get_zha_devices = AsyncMock(side_effect=RuntimeError("auth failed"))

    with patch("zigporter.commands.check.HAClient", return_value=mock_ha_client):
        result = await _check_zha_active("https://ha.test", "token", True)

    assert result.status == CheckStatus.FAILED


async def test_check_z2m_running_skipped_when_no_url():
    result = await _check_z2m_running("https://ha.test", "token", "", True)
    assert result.status == CheckStatus.SKIPPED


async def test_check_z2m_running_success():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json = MagicMock(return_value=[{"ieee_address": "0x1234"}])

    with patch("zigporter.commands.check.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await _check_z2m_running("https://ha.test", "token", "https://z2m.test", True)

    assert result.status == CheckStatus.OK


async def test_check_z2m_running_failure():
    with patch("zigporter.commands.check.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=RuntimeError("Connection refused"))
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await _check_z2m_running("https://ha.test", "token", "https://z2m.test", True)

    assert result.status == CheckStatus.FAILED


# ---------------------------------------------------------------------------
# check_command integration tests
# ---------------------------------------------------------------------------


def _all_ok_results():
    from zigporter.models import CheckResult

    return [
        CheckResult(name="Configuration", status=CheckStatus.OK, message="ok"),
        CheckResult(name="HA reachable", status=CheckStatus.OK, message="ok"),
        CheckResult(name="ZHA active", status=CheckStatus.OK, message="ok"),
        CheckResult(name="Z2M running", status=CheckStatus.OK, message="ok"),
    ]


def test_check_command_all_pass(mocker):
    mocker.patch(
        "zigporter.commands.check._run_checks",
        new=AsyncMock(return_value=_all_ok_results()),
    )
    confirm_mock = mocker.patch("zigporter.commands.check.questionary.confirm")

    result = check_command("https://ha.test", "token", True, "https://z2m.test")

    assert result is True
    confirm_mock.assert_not_called()


def _blocking_failure_results():
    from zigporter.models import CheckResult

    return [
        CheckResult(name="Configuration", status=CheckStatus.OK, message="ok"),
        CheckResult(
            name="HA reachable", status=CheckStatus.FAILED, message="unreachable", blocking=True
        ),
        CheckResult(name="ZHA active", status=CheckStatus.SKIPPED, message="skipped"),
        CheckResult(name="Z2M running", status=CheckStatus.SKIPPED, message="skipped"),
    ]


def test_check_command_blocking_failure_user_aborts(mocker):
    mocker.patch(
        "zigporter.commands.check._run_checks",
        new=AsyncMock(return_value=_blocking_failure_results()),
    )
    mocker.patch("zigporter.commands.check.sys.stdin.isatty", return_value=True)
    mocker.patch(
        "zigporter.commands.check.questionary.confirm",
        return_value=MagicMock(ask=MagicMock(return_value=False)),
    )

    result = check_command("https://ha.test", "token", True, "https://z2m.test")
    assert result is False


def test_check_command_blocking_failure_user_proceeds(mocker):
    mocker.patch(
        "zigporter.commands.check._run_checks",
        new=AsyncMock(return_value=_blocking_failure_results()),
    )
    mocker.patch("zigporter.commands.check.sys.stdin.isatty", return_value=True)
    mocker.patch(
        "zigporter.commands.check.questionary.confirm",
        return_value=MagicMock(ask=MagicMock(return_value=True)),
    )

    result = check_command("https://ha.test", "token", True, "https://z2m.test")
    assert result is True


def test_check_command_blocking_failure_non_tty(mocker):
    mocker.patch(
        "zigporter.commands.check._run_checks",
        new=AsyncMock(return_value=_blocking_failure_results()),
    )
    mocker.patch("zigporter.commands.check.sys.stdin.isatty", return_value=False)
    confirm_mock = mocker.patch("zigporter.commands.check.questionary.confirm")

    result = check_command("https://ha.test", "token", True, "https://z2m.test")

    assert result is False
    confirm_mock.assert_not_called()
