"""Tests for setup command utility functions. Interactive prompts are mocked."""

from unittest.mock import AsyncMock, patch

import httpx
import respx

from zigporter.commands.setup import (
    _mask_token,
    _read_current,
    _ssl_context,
    _test_connections,
    _write_env,
    run_setup,
)


# ---------------------------------------------------------------------------
# _mask_token
# ---------------------------------------------------------------------------


def test_mask_token_long():
    token = "abcdefghij1234"
    result = _mask_token(token)
    assert result.endswith("1234")
    assert "•" in result


def test_mask_token_short():
    result = _mask_token("ab")
    assert result == "ab"


def test_mask_token_empty():
    assert _mask_token("") == ""


def test_mask_token_exactly_four_chars():
    result = _mask_token("1234")
    assert result == "1234"


def test_mask_token_hides_prefix():
    token = "supersecrettoken1234"
    result = _mask_token(token)
    assert "supersecret" not in result
    assert result.endswith("1234")


# ---------------------------------------------------------------------------
# _ssl_context
# ---------------------------------------------------------------------------


def test_ssl_context_verify_true():
    result = _ssl_context(True)
    assert result is True


def test_ssl_context_verify_false():
    import ssl

    result = _ssl_context(False)
    assert isinstance(result, ssl.SSLContext)
    assert result.check_hostname is False


# ---------------------------------------------------------------------------
# _read_current
# ---------------------------------------------------------------------------


def test_read_current_missing_file(tmp_path):
    result = _read_current(tmp_path / "nonexistent.env")
    assert result == {}


def test_read_current_existing_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("HA_URL=https://ha.test\nHA_TOKEN=mytoken\n")
    result = _read_current(env_file)
    assert result["HA_URL"] == "https://ha.test"
    assert result["HA_TOKEN"] == "mytoken"


# ---------------------------------------------------------------------------
# _write_env
# ---------------------------------------------------------------------------


def test_write_env_default_topic(tmp_path):
    path = tmp_path / ".env"
    _write_env(path, "https://ha.test", "mytoken", True, "https://z2m.test", "zigbee2mqtt")
    content = path.read_text()
    assert "HA_URL=https://ha.test" in content
    assert "HA_TOKEN=mytoken" in content
    assert "HA_VERIFY_SSL=true" in content
    assert "Z2M_URL=https://z2m.test" in content
    assert "Z2M_MQTT_TOPIC" not in content


def test_write_env_custom_topic(tmp_path):
    path = tmp_path / ".env"
    _write_env(path, "https://ha.test", "tok", False, "https://z2m.test", "custom/topic")
    content = path.read_text()
    assert "Z2M_MQTT_TOPIC=custom/topic" in content
    assert "HA_VERIFY_SSL=false" in content


# ---------------------------------------------------------------------------
# _test_connections
# ---------------------------------------------------------------------------


@respx.mock
async def test_test_connections_both_reachable():
    respx.get("https://ha.test/api/").mock(return_value=httpx.Response(200))
    respx.get("https://z2m.test/api/devices").mock(return_value=httpx.Response(200))

    with patch("zigporter.commands.setup.console"):
        await _test_connections("https://ha.test", "token", True, "https://z2m.test")


@respx.mock
async def test_test_connections_ha_server_error():
    respx.get("https://ha.test/api/").mock(return_value=httpx.Response(500))
    respx.get("https://z2m.test/api/devices").mock(return_value=httpx.Response(200))

    with patch("zigporter.commands.setup.console"):
        # Should not raise — just prints warning
        await _test_connections("https://ha.test", "token", True, "https://z2m.test")


@respx.mock
async def test_test_connections_ha_unreachable():
    respx.get("https://ha.test/api/").mock(side_effect=httpx.ConnectError("unreachable"))
    respx.get("https://z2m.test/api/devices").mock(return_value=httpx.Response(200))

    with patch("zigporter.commands.setup.console"):
        await _test_connections("https://ha.test", "token", True, "https://z2m.test")


@respx.mock
async def test_test_connections_z2m_unreachable():
    respx.get("https://ha.test/api/").mock(return_value=httpx.Response(200))
    respx.get("https://z2m.test/api/devices").mock(side_effect=httpx.ConnectError("unreachable"))

    with patch("zigporter.commands.setup.console"):
        await _test_connections("https://ha.test", "token", True, "https://z2m.test")


# ---------------------------------------------------------------------------
# run_setup — full wizard (questionary mocked)
# ---------------------------------------------------------------------------


async def test_run_setup_happy_path(tmp_path, mocker):
    env_path = tmp_path / ".env"

    mocker.patch("zigporter.commands.setup.config_dir", return_value=tmp_path)

    mocker.patch(
        "questionary.text",
        side_effect=[
            mocker.MagicMock(unsafe_ask_async=AsyncMock(return_value="https://ha.test")),
            mocker.MagicMock(unsafe_ask_async=AsyncMock(return_value="https://z2m.test")),
            mocker.MagicMock(unsafe_ask_async=AsyncMock(return_value="zigbee2mqtt")),
        ],
    )
    mocker.patch(
        "questionary.password",
        return_value=mocker.MagicMock(unsafe_ask_async=AsyncMock(return_value="mytoken")),
    )
    mocker.patch(
        "questionary.confirm",
        return_value=mocker.MagicMock(unsafe_ask_async=AsyncMock(return_value=True)),
    )
    mocker.patch("zigporter.commands.setup._test_connections", new=AsyncMock())
    mocker.patch("zigporter.commands.setup.console")

    result = await run_setup()

    assert result is True
    assert env_path.exists()
    content = env_path.read_text()
    assert "HA_URL=https://ha.test" in content
    assert "HA_TOKEN=mytoken" in content


async def test_run_setup_cancelled_on_ha_url(tmp_path, mocker):
    mocker.patch("zigporter.commands.setup.config_dir", return_value=tmp_path)
    mocker.patch(
        "questionary.text",
        return_value=mocker.MagicMock(unsafe_ask_async=AsyncMock(return_value=None)),
    )
    mocker.patch("zigporter.commands.setup.console")

    result = await run_setup()
    assert result is False


async def test_run_setup_cancelled_on_token(tmp_path, mocker):
    mocker.patch("zigporter.commands.setup.config_dir", return_value=tmp_path)
    mocker.patch(
        "questionary.text",
        return_value=mocker.MagicMock(unsafe_ask_async=AsyncMock(return_value="https://ha.test")),
    )
    mocker.patch(
        "questionary.password",
        return_value=mocker.MagicMock(unsafe_ask_async=AsyncMock(return_value=None)),
    )
    mocker.patch("zigporter.commands.setup.console")

    result = await run_setup()
    assert result is False


async def test_run_setup_empty_token_no_existing(tmp_path, mocker):
    """Empty token with no existing token → returns False."""
    mocker.patch("zigporter.commands.setup.config_dir", return_value=tmp_path)
    mocker.patch(
        "questionary.text",
        return_value=mocker.MagicMock(unsafe_ask_async=AsyncMock(return_value="https://ha.test")),
    )
    mocker.patch(
        "questionary.password",
        return_value=mocker.MagicMock(unsafe_ask_async=AsyncMock(return_value="")),
    )
    mocker.patch("zigporter.commands.setup.console")

    result = await run_setup()
    assert result is False


async def test_run_setup_keeps_existing_token(tmp_path, mocker):
    """Empty token input keeps the existing token from config."""
    env_path = tmp_path / ".env"
    env_path.write_text("HA_URL=https://old.test\nHA_TOKEN=existingtoken\n")

    mocker.patch("zigporter.commands.setup.config_dir", return_value=tmp_path)

    mocker.patch(
        "questionary.text",
        side_effect=[
            mocker.MagicMock(unsafe_ask_async=AsyncMock(return_value="https://ha.test")),
            mocker.MagicMock(unsafe_ask_async=AsyncMock(return_value="https://z2m.test")),
            mocker.MagicMock(unsafe_ask_async=AsyncMock(return_value="zigbee2mqtt")),
        ],
    )
    mocker.patch(
        "questionary.password",
        return_value=mocker.MagicMock(unsafe_ask_async=AsyncMock(return_value="")),
    )
    mocker.patch(
        "questionary.confirm",
        return_value=mocker.MagicMock(unsafe_ask_async=AsyncMock(return_value=True)),
    )
    mocker.patch("zigporter.commands.setup._test_connections", new=AsyncMock())
    mocker.patch("zigporter.commands.setup.console")

    result = await run_setup()

    assert result is True
    content = env_path.read_text()
    assert "HA_TOKEN=existingtoken" in content
