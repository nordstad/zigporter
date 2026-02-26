import json

import httpx
import pytest
import respx  # used by @respx.mock decorator

from zigporter.ha_client import HAClient


HA_URL = "https://ha.test"
TOKEN = "test-token"


@pytest.fixture
def client() -> HAClient:
    return HAClient(ha_url=HA_URL, token=TOKEN, verify_ssl=False)


async def test_get_zha_devices(client, zha_devices_payload, mocker):
    messages = [
        json.dumps({"type": "auth_required"}),
        json.dumps({"type": "auth_ok"}),
        json.dumps({"id": 1, "type": "result", "success": True, "result": zha_devices_payload}),
    ]
    mock_ws = mocker.AsyncMock()
    mock_ws.recv = mocker.AsyncMock(side_effect=messages)
    mock_ws.__aenter__ = mocker.AsyncMock(return_value=mock_ws)
    mock_ws.__aexit__ = mocker.AsyncMock(return_value=False)
    mocker.patch("websockets.connect", return_value=mock_ws)

    result = await client.get_zha_devices()
    assert len(result) == 2
    assert result[0]["ieee"] == "00:11:22:33:44:55:66:77"


@respx.mock
async def test_get_states(client, states_payload):
    respx.get(f"{HA_URL}/api/states").mock(return_value=httpx.Response(200, json=states_payload))
    result = await client.get_states()
    assert any(s["entity_id"] == "climate.living_room_thermostat" for s in result)


async def test_get_zha_devices_ws_command_failure(client, mocker):
    messages = [
        json.dumps({"type": "auth_required"}),
        json.dumps({"type": "auth_ok"}),
        json.dumps(
            {"id": 1, "type": "result", "success": False, "error": {"code": "unknown_command"}}
        ),
    ]
    mock_ws = mocker.AsyncMock()
    mock_ws.recv = mocker.AsyncMock(side_effect=messages)
    mock_ws.__aenter__ = mocker.AsyncMock(return_value=mock_ws)
    mock_ws.__aexit__ = mocker.AsyncMock(return_value=False)
    mocker.patch("websockets.connect", return_value=mock_ws)

    with pytest.raises(RuntimeError, match="command failed"):
        await client.get_zha_devices()


@respx.mock
async def test_get_states_http_error(client):
    respx.get(f"{HA_URL}/api/states").mock(return_value=httpx.Response(403))
    with pytest.raises(httpx.HTTPStatusError):
        await client.get_states()


async def test_ws_command_auth_failure(client, mocker):
    messages = [
        json.dumps({"type": "auth_required"}),
        json.dumps({"type": "auth_invalid", "message": "Invalid token"}),
    ]

    mock_ws = mocker.AsyncMock()
    mock_ws.recv = mocker.AsyncMock(side_effect=messages)
    mock_ws.__aenter__ = mocker.AsyncMock(return_value=mock_ws)
    mock_ws.__aexit__ = mocker.AsyncMock(return_value=False)

    mocker.patch("websockets.connect", return_value=mock_ws)

    with pytest.raises(RuntimeError, match="authentication failed"):
        await client.get_entity_registry()


async def test_ws_command_unexpected_first_message(client, mocker):
    messages = [json.dumps({"type": "unexpected"})]

    mock_ws = mocker.AsyncMock()
    mock_ws.recv = mocker.AsyncMock(side_effect=messages)
    mock_ws.__aenter__ = mocker.AsyncMock(return_value=mock_ws)
    mock_ws.__aexit__ = mocker.AsyncMock(return_value=False)

    mocker.patch("websockets.connect", return_value=mock_ws)

    with pytest.raises(RuntimeError, match="auth_required"):
        await client.get_entity_registry()


def _make_ws(mocker, result):
    """Helper: build a mock WS that returns auth handshake + one result message."""
    messages = [
        json.dumps({"type": "auth_required"}),
        json.dumps({"type": "auth_ok"}),
        json.dumps({"id": 1, "type": "result", "success": True, "result": result}),
    ]
    mock_ws = mocker.AsyncMock()
    mock_ws.recv = mocker.AsyncMock(side_effect=messages)
    mock_ws.__aenter__ = mocker.AsyncMock(return_value=mock_ws)
    mock_ws.__aexit__ = mocker.AsyncMock(return_value=False)
    return mock_ws


def _make_ws_fail(mocker):
    """Helper: build a mock WS that returns a failed result."""
    messages = [
        json.dumps({"type": "auth_required"}),
        json.dumps({"type": "auth_ok"}),
        json.dumps({"id": 1, "type": "result", "success": False, "error": {"code": "failed"}}),
    ]
    mock_ws = mocker.AsyncMock()
    mock_ws.recv = mocker.AsyncMock(side_effect=messages)
    mock_ws.__aenter__ = mocker.AsyncMock(return_value=mock_ws)
    mock_ws.__aexit__ = mocker.AsyncMock(return_value=False)
    return mock_ws


async def test_get_scripts_returns_list(client, mocker):
    mock_ws = _make_ws(mocker, [{"id": "s1"}])
    mocker.patch("websockets.connect", return_value=mock_ws)
    result = await client.get_scripts()
    assert result == [{"id": "s1"}]


async def test_get_scripts_returns_empty_on_failure(client, mocker):
    mock_ws = _make_ws_fail(mocker)
    mocker.patch("websockets.connect", return_value=mock_ws)
    result = await client.get_scripts()
    assert result == []


async def test_get_scenes_returns_list(client, mocker):
    mock_ws = _make_ws(mocker, [{"id": "scene1"}])
    mocker.patch("websockets.connect", return_value=mock_ws)
    result = await client.get_scenes()
    assert result == [{"id": "scene1"}]


async def test_get_scenes_returns_empty_on_failure(client, mocker):
    mock_ws = _make_ws_fail(mocker)
    mocker.patch("websockets.connect", return_value=mock_ws)
    result = await client.get_scenes()
    assert result == []


async def test_get_panels_returns_dict(client, mocker):
    mock_ws = _make_ws(mocker, {"lovelace": {"title": "Home"}})
    mocker.patch("websockets.connect", return_value=mock_ws)
    result = await client.get_panels()
    assert "lovelace" in result


async def test_get_panels_returns_empty_on_failure(client, mocker):
    mock_ws = _make_ws_fail(mocker)
    mocker.patch("websockets.connect", return_value=mock_ws)
    result = await client.get_panels()
    assert result == {}


@respx.mock
async def test_get_lovelace_config_ws_success(client, mocker):
    mock_ws = _make_ws(mocker, {"views": []})
    mocker.patch("websockets.connect", return_value=mock_ws)
    result = await client.get_lovelace_config()
    assert result == {"views": []}


@respx.mock
async def test_get_lovelace_config_rest_fallback(client, mocker):
    """WS fails → falls back to REST."""
    mock_ws = _make_ws_fail(mocker)
    mocker.patch("websockets.connect", return_value=mock_ws)
    respx.get(f"{HA_URL}/api/lovelace/config").mock(
        return_value=httpx.Response(200, json={"views": [{"title": "Home"}]})
    )
    result = await client.get_lovelace_config()
    assert result == {"views": [{"title": "Home"}]}


@respx.mock
async def test_get_lovelace_config_returns_none_when_both_fail(client, mocker):
    mock_ws = _make_ws_fail(mocker)
    mocker.patch("websockets.connect", return_value=mock_ws)
    respx.get(f"{HA_URL}/api/lovelace/config").mock(return_value=httpx.Response(404))
    result = await client.get_lovelace_config()
    assert result is None


async def test_get_z2m_device_id_found(client, mocker):
    registry = [
        {
            "id": "dev-z2m-abc",
            "identifiers": [["mqtt", "zigbee2mqtt_0x0011223344556677"]],
        },
        {
            "id": "dev-other",
            "identifiers": [["zha", "00:11:22:33:44:55:66:77"]],
        },
    ]
    mock_ws = _make_ws(mocker, registry)
    mocker.patch("websockets.connect", return_value=mock_ws)
    result = await client.get_z2m_device_id("00:11:22:33:44:55:66:77")
    assert result == "dev-z2m-abc"


async def test_get_z2m_device_id_not_found(client, mocker):
    mock_ws = _make_ws(mocker, [])
    mocker.patch("websockets.connect", return_value=mock_ws)
    result = await client.get_z2m_device_id("00:11:22:33:44:55:66:77")
    assert result is None


async def test_get_entity_ids_for_device(client, mocker):
    registry = [
        {"entity_id": "switch.plug", "device_id": "dev-abc"},
        {"entity_id": "sensor.temp", "device_id": "dev-abc"},
        {"entity_id": "light.other", "device_id": "dev-other"},
    ]
    mock_ws = _make_ws(mocker, registry)
    mocker.patch("websockets.connect", return_value=mock_ws)
    result = await client.get_entity_ids_for_device("dev-abc")
    assert set(result) == {"switch.plug", "sensor.temp"}


async def test_remove_zha_device(client, mocker):
    mock_ws = _make_ws(mocker, None)
    mocker.patch("websockets.connect", return_value=mock_ws)
    # Should not raise
    await client.remove_zha_device("00:11:22:33:44:55:66:77")
    assert mock_ws.send.called


async def test_update_device_area(client, mocker):
    mock_ws = _make_ws(mocker, None)
    mocker.patch("websockets.connect", return_value=mock_ws)
    await client.update_device_area("dev-abc", "living_room")
    assert mock_ws.send.called


async def test_rename_entity_id(client, mocker):
    mock_ws = _make_ws(mocker, None)
    mocker.patch("websockets.connect", return_value=mock_ws)
    await client.rename_entity_id("switch.old_name", "switch.new_name")
    assert mock_ws.send.called


async def test_call_service(client, mocker):
    mock_ws = _make_ws(mocker, None)
    mocker.patch("websockets.connect", return_value=mock_ws)
    await client.call_service("light", "turn_on", {"entity_id": "light.test"})
    assert mock_ws.send.called


async def test_get_all_ws_data(client, mocker):
    """get_all_ws_data fetches 5 commands on one connection."""
    auth_msgs = [
        json.dumps({"type": "auth_required"}),
        json.dumps({"type": "auth_ok"}),
    ]
    result_msgs = [
        json.dumps({"id": i, "type": "result", "success": True, "result": []}) for i in range(1, 6)
    ]
    mock_ws = mocker.AsyncMock()
    mock_ws.recv = mocker.AsyncMock(side_effect=auth_msgs + result_msgs)
    mock_ws.__aenter__ = mocker.AsyncMock(return_value=mock_ws)
    mock_ws.__aexit__ = mocker.AsyncMock(return_value=False)
    mocker.patch("websockets.connect", return_value=mock_ws)

    result = await client.get_all_ws_data()
    assert set(result.keys()) == {
        "zha_devices",
        "entity_registry",
        "device_registry",
        "area_registry",
        "automation_configs",
    }


async def test_get_all_ws_data_automation_failure_returns_empty(client, mocker):
    """If automation_configs command fails, returns [] instead of raising."""
    auth_msgs = [
        json.dumps({"type": "auth_required"}),
        json.dumps({"type": "auth_ok"}),
    ]
    ok_result = json.dumps({"id": 1, "type": "result", "success": True, "result": []})
    fail_result = json.dumps(
        {"id": 5, "type": "result", "success": False, "error": {"code": "unknown"}}
    )
    result_msgs = [ok_result, ok_result, ok_result, ok_result, fail_result]
    mock_ws = mocker.AsyncMock()
    mock_ws.recv = mocker.AsyncMock(side_effect=auth_msgs + result_msgs)
    mock_ws.__aenter__ = mocker.AsyncMock(return_value=mock_ws)
    mock_ws.__aexit__ = mocker.AsyncMock(return_value=False)
    mocker.patch("websockets.connect", return_value=mock_ws)

    result = await client.get_all_ws_data()
    assert result["automation_configs"] == []
