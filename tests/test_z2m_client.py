import json

import httpx
import pytest
import respx

from zigporter.utils import normalize_ieee
from zigporter.z2m_client import Z2MClient, _ieee_from_z2m_identifier


HA_URL = "https://ha.test"
Z2M_URL = "https://ha.test/45df7312_zigbee2mqtt"
TOKEN = "test-token"
SESSION = "test-session-abc"

INGRESS_SESSION_URL = f"{HA_URL}/api/hassio/ingress/session"
WS_URL = "wss://ha.test/api/websocket"


@pytest.fixture
def client() -> Z2MClient:
    return Z2MClient(ha_url=HA_URL, ha_token=TOKEN, z2m_url=Z2M_URL, verify_ssl=False)


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------


def test_normalize_ieee_colon_format():
    assert normalize_ieee("00:12:34:56:78:90:ab:cd") == "001234567890abcd"


def test_normalize_ieee_0x_format():
    assert normalize_ieee("0x001234567890abcd") == "001234567890abcd"


def test_ieee_from_z2m_identifier_standard():
    assert _ieee_from_z2m_identifier("zigbee2mqtt_0x001234567890abcd") == "001234567890abcd"


def test_ieee_from_z2m_identifier_non_z2m():
    assert _ieee_from_z2m_identifier("some_other_integration_abc") is None


def test_ieee_from_z2m_identifier_rejects_short_hex():
    assert _ieee_from_z2m_identifier("zigbee2mqtt_0xabc") is None


def test_ieee_from_z2m_identifier_rejects_non_hex_chars():
    assert _ieee_from_z2m_identifier("zigbee2mqtt_0x00112233445566zz") is None


# ---------------------------------------------------------------------------
# Ingress path: Bearer token accepted
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_devices_bearer_auth(client):
    """Bearer token accepted directly — no session exchange needed."""
    devices_payload = [
        {"ieee_address": "00:11:22:33:44:55:66:77", "friendly_name": "Living Room Thermostat"},
        {"ieee_address": "aa:bb:cc:dd:ee:ff:00:11", "friendly_name": "Kitchen Plug"},
    ]
    respx.get(f"{Z2M_URL}/api/devices").mock(return_value=httpx.Response(200, json=devices_payload))

    result = await client.get_devices()

    assert len(result) == 2
    assert result[0]["friendly_name"] == "Living Room Thermostat"


@respx.mock
async def test_get_devices_falls_back_to_session_on_html_response(client):
    """If the Bearer request returns non-JSON (e.g. HA login redirect), fall back to session."""
    respx.post(INGRESS_SESSION_URL).mock(
        return_value=httpx.Response(200, json={"data": {"session": SESSION}})
    )
    devices_payload = [{"ieee_address": "aa:bb", "friendly_name": "Plug"}]
    respx.get(f"{Z2M_URL}/api/devices").mock(
        side_effect=[
            httpx.Response(
                200, content=b"<html>Login</html>", headers={"content-type": "text/html"}
            ),
            httpx.Response(200, json=devices_payload),
        ]
    )

    result = await client.get_devices()

    assert result[0]["friendly_name"] == "Plug"


@respx.mock
async def test_get_devices_falls_back_to_session_on_401(client):
    """If Bearer token gets 401, fall back to ingress session cookie."""
    respx.post(INGRESS_SESSION_URL).mock(
        return_value=httpx.Response(200, json={"data": {"session": SESSION}})
    )
    devices_payload = [{"ieee_address": "aa:bb", "friendly_name": "Plug"}]
    respx.get(f"{Z2M_URL}/api/devices").mock(
        side_effect=[
            httpx.Response(401),
            httpx.Response(200, json=devices_payload),
        ]
    )

    result = await client.get_devices()

    assert result[0]["friendly_name"] == "Plug"


@respx.mock
async def test_session_refreshed_on_401(client):
    """After Bearer fails, session cookie is used. If that also 401s, session is refreshed."""
    session_route = respx.post(INGRESS_SESSION_URL).mock(
        side_effect=[
            httpx.Response(200, json={"data": {"session": "first-session"}}),
            httpx.Response(200, json={"data": {"session": "refreshed-session"}}),
        ]
    )
    respx.get(f"{Z2M_URL}/api/devices").mock(
        side_effect=[
            httpx.Response(401),  # Bearer rejected → triggers session fallback
            httpx.Response(401),  # First session expired → triggers session refresh
            httpx.Response(200, json=[{"ieee_address": "aa:bb", "friendly_name": "Plug"}]),
        ]
    )

    result = await client.get_devices()

    assert session_route.call_count == 2
    assert result[0]["friendly_name"] == "Plug"


# ---------------------------------------------------------------------------
# HA-registry fallback path
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_devices_falls_back_to_ha_registry_when_session_fails(client, respx_mock):
    """When ingress session returns 401, fall back to HA device registry."""
    # Bearer → HTML, session → 401
    respx.get(f"{Z2M_URL}/api/devices").mock(
        return_value=httpx.Response(200, content=b"<html/>", headers={"content-type": "text/html"})
    )
    respx.post(INGRESS_SESSION_URL).mock(return_value=httpx.Response(401))

    # HA WebSocket returns device registry with one Z2M device
    registry_entry = {
        "id": "dev1",
        "name": "Kitchen Plug",
        "name_by_user": None,
        "manufacturer": "IKEA",
        "model": "E1743",
        "identifiers": [["mqtt", "zigbee2mqtt_0xaabbccddeeff0011"]],
        "area_id": None,
    }

    from unittest.mock import AsyncMock, patch

    ws_messages = iter(
        [
            json.dumps({"type": "auth_required"}),
            json.dumps({"type": "auth_ok"}),
            json.dumps({"id": 1, "type": "result", "success": True, "result": [registry_entry]}),
        ]
    )

    mock_ws = AsyncMock()
    mock_ws.recv = AsyncMock(side_effect=ws_messages)
    mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
    mock_ws.__aexit__ = AsyncMock(return_value=False)

    with patch("websockets.connect", return_value=mock_ws):
        result = await client.get_devices()

    assert len(result) == 1
    assert result[0]["friendly_name"] == "Kitchen Plug"
    assert result[0]["ieee_address"] == "0xaabbccddeeff0011"
    assert result[0]["definition"]["vendor"] == "IKEA"


@respx.mock
async def test_get_devices_falls_back_to_ha_registry_on_http_error(client, respx_mock):
    """Non-RuntimeError HTTP errors (e.g. 503) also trigger the HA registry fallback."""
    # Bearer returns a 503 — httpx.HTTPStatusError should be caught and trigger fallback
    respx.get(f"{Z2M_URL}/api/devices").mock(
        side_effect=[
            httpx.Response(200, content=b"<html/>", headers={"content-type": "text/html"}),
            httpx.Response(503),
        ]
    )
    respx.post(INGRESS_SESSION_URL).mock(
        return_value=httpx.Response(200, json={"data": {"session": SESSION}})
    )

    registry_entry = {
        "id": "dev1",
        "name": "Porch Sensor",
        "name_by_user": None,
        "manufacturer": "Sonoff",
        "model": "SNZB-03",
        "identifiers": [["mqtt", "zigbee2mqtt_0x1122334455667788"]],
        "area_id": None,
    }

    from unittest.mock import AsyncMock, patch

    ws_messages = iter(
        [
            json.dumps({"type": "auth_required"}),
            json.dumps({"type": "auth_ok"}),
            json.dumps({"id": 1, "type": "result", "success": True, "result": [registry_entry]}),
        ]
    )
    mock_ws = AsyncMock()
    mock_ws.recv = AsyncMock(side_effect=ws_messages)
    mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
    mock_ws.__aexit__ = AsyncMock(return_value=False)

    with patch("websockets.connect", return_value=mock_ws):
        result = await client.get_devices()

    assert len(result) == 1
    assert result[0]["ieee_address"] == "0x1122334455667788"


# ---------------------------------------------------------------------------
# permit_join and rename
# ---------------------------------------------------------------------------


@respx.mock
async def test_enable_permit_join(client):
    permit_route = respx.post(f"{Z2M_URL}/api/permit_join").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    await client.enable_permit_join(seconds=120)

    assert permit_route.called
    body = json.loads(permit_route.calls[0].request.content)
    assert body == {"time": 120, "device": None}


@respx.mock
async def test_disable_permit_join(client):
    permit_route = respx.post(f"{Z2M_URL}/api/permit_join").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    await client.disable_permit_join()

    assert permit_route.called
    body = json.loads(permit_route.calls[0].request.content)
    assert body == {"time": 0, "device": None}


@respx.mock
async def test_rename_device(client):
    rename_route = respx.post(f"{Z2M_URL}/api/device").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    await client.rename_device("0xaabbccddeeff0011", "Kitchen Plug")

    assert rename_route.called


# ---------------------------------------------------------------------------
# get_device_by_ieee normalisation
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_device_by_ieee_found(client):
    respx.get(f"{Z2M_URL}/api/devices").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"ieee_address": "0x001234567890abcd", "friendly_name": "Thermostat"},
            ],
        )
    )

    # Should match regardless of input format
    result = await client.get_device_by_ieee("00:12:34:56:78:90:ab:cd")

    assert result is not None
    assert result["friendly_name"] == "Thermostat"


@respx.mock
async def test_get_device_by_ieee_not_found(client):
    respx.get(f"{Z2M_URL}/api/devices").mock(return_value=httpx.Response(200, json=[]))

    result = await client.get_device_by_ieee("ff:ff:ff:ff:ff:ff:ff:ff")

    assert result is None
