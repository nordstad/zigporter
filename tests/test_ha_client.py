import json
import ssl

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


async def test_save_lovelace_config_without_url_path(client, mocker):
    mock_ws = _make_ws(mocker, None)
    mocker.patch("websockets.connect", return_value=mock_ws)
    await client.save_lovelace_config({"views": []})
    sent = json.loads(mock_ws.send.call_args_list[-1][0][0])
    assert sent["type"] == "lovelace/config/save"
    assert "url_path" not in sent


async def test_save_lovelace_config_with_url_path(client, mocker):
    mock_ws = _make_ws(mocker, None)
    mocker.patch("websockets.connect", return_value=mock_ws)
    await client.save_lovelace_config({"views": []}, url_path="mobile")
    sent = json.loads(mock_ws.send.call_args_list[-1][0][0])
    assert sent["type"] == "lovelace/config/save"
    assert sent["url_path"] == "mobile"


async def test_update_automation(client, mocker):
    mock_ws = _make_ws(mocker, None)
    mocker.patch("websockets.connect", return_value=mock_ws)
    await client.update_automation("auto1", {"alias": "Test"})
    sent = json.loads(mock_ws.send.call_args_list[-1][0][0])
    assert sent["type"] == "config/automation/update"
    assert sent["automation_id"] == "auto1"


async def test_update_script(client, mocker):
    mock_ws = _make_ws(mocker, None)
    mocker.patch("websockets.connect", return_value=mock_ws)
    await client.update_script("script1", {"alias": "Test"})
    sent = json.loads(mock_ws.send.call_args_list[-1][0][0])
    assert sent["type"] == "config/script/update"
    assert sent["script_id"] == "script1"


async def test_update_scene(client, mocker):
    mock_ws = _make_ws(mocker, None)
    mocker.patch("websockets.connect", return_value=mock_ws)
    await client.update_scene("scene1", {"name": "Evening"})
    sent = json.loads(mock_ws.send.call_args_list[-1][0][0])
    assert sent["type"] == "config/scene/update"
    assert sent["scene_id"] == "scene1"


# ---------------------------------------------------------------------------
# Missing coverage: _ssl_context (line 34), get_automation_configs failure (line 73),
# get_scripts/scenes/panels fallbacks, get_config_entries, update_config_entry_options,
# get_lovelace_config yaml_mode WS + REST fallback, get_entities_for_device,
# rename_device_name, get_all_ws_data non-automation failure
# ---------------------------------------------------------------------------


def test_ssl_context_verify_false(client):
    """Line 34-38: _ssl_context() returns an ssl.SSLContext when verify_ssl=False."""

    ctx = client._ssl_context()
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname is False


def test_ssl_context_verify_true():
    """Line 33: _ssl_context() returns True when verify_ssl=True."""
    c = HAClient(ha_url=HA_URL, token=TOKEN, verify_ssl=True)
    assert c._ssl_context() is True


async def test_get_automation_configs_returns_empty_on_failure(client, mocker):
    """Lines 73/138-139: RuntimeError → returns []."""
    mock_ws = _make_ws_fail(mocker)
    mocker.patch("websockets.connect", return_value=mock_ws)
    result = await client.get_automation_configs()
    assert result == []


async def test_get_config_entries_returns_list(client, mocker):
    """Lines 167-168: normal path returns list."""
    mock_ws = _make_ws(mocker, [{"entry_id": "ce1", "title": "Helper"}])
    mocker.patch("websockets.connect", return_value=mock_ws)
    result = await client.get_config_entries()
    assert result == [{"entry_id": "ce1", "title": "Helper"}]


async def test_get_config_entries_returns_empty_on_failure(client, mocker):
    """Lines 169-170: RuntimeError → returns []."""
    mock_ws = _make_ws_fail(mocker)
    mocker.patch("websockets.connect", return_value=mock_ws)
    result = await client.get_config_entries()
    assert result == []


async def test_get_config_entries_returns_empty_when_none(client, mocker):
    """Line 168: result is None → returns []."""
    mock_ws = _make_ws(mocker, None)
    mocker.patch("websockets.connect", return_value=mock_ws)
    result = await client.get_config_entries()
    assert result == []


async def test_get_z2m_config_entry_id_found(client, mocker):
    """Returns entry_id when a matching mqtt/zigbee2mqtt entry exists."""
    entries = [
        {"entry_id": "other-1", "domain": "zha", "title": "Zigbee Home Automation"},
        {"entry_id": "z2m-1", "domain": "mqtt", "title": "Zigbee2MQTT"},
    ]
    mock_ws = _make_ws(mocker, entries)
    mocker.patch("websockets.connect", return_value=mock_ws)
    result = await client.get_z2m_config_entry_id()
    assert result == "z2m-1"


async def test_get_z2m_config_entry_id_not_found(client, mocker):
    """Returns None when no matching entry exists."""
    entries = [
        {"entry_id": "other-1", "domain": "zha", "title": "Zigbee Home Automation"},
    ]
    mock_ws = _make_ws(mocker, entries)
    mocker.patch("websockets.connect", return_value=mock_ws)
    result = await client.get_z2m_config_entry_id()
    assert result is None


async def test_update_config_entry_options(client, mocker):
    """Lines 172-180: update_config_entry_options sends correct WS command."""
    mock_ws = _make_ws(mocker, None)
    mocker.patch("websockets.connect", return_value=mock_ws)
    await client.update_config_entry_options("ce1", {"entity_id": "switch.test"})
    sent = json.loads(mock_ws.send.call_args_list[-1][0][0])
    assert sent["type"] == "config_entries/update"
    assert sent["entry_id"] == "ce1"
    assert sent["options"] == {"entity_id": "switch.test"}


@respx.mock
async def test_get_lovelace_config_yaml_mode_ws_then_rest_fails(client, mocker):
    """Lines 200-201, 214-215: WS raises mode_not_storage → REST also fails → YAML_MODE."""
    from zigporter.ha_client import is_yaml_mode  # noqa: PLC0415

    messages = [
        json.dumps({"type": "auth_required"}),
        json.dumps({"type": "auth_ok"}),
        json.dumps(
            {
                "id": 1,
                "type": "result",
                "success": False,
                "error": {"code": "mode_not_storage"},
            }
        ),
    ]
    mock_ws = mocker.AsyncMock()
    mock_ws.recv = mocker.AsyncMock(side_effect=messages)
    mock_ws.__aenter__ = mocker.AsyncMock(return_value=mock_ws)
    mock_ws.__aexit__ = mocker.AsyncMock(return_value=False)
    mocker.patch("websockets.connect", return_value=mock_ws)
    respx.get(f"{HA_URL}/api/lovelace/config").mock(return_value=httpx.Response(400))

    result = await client.get_lovelace_config()
    assert is_yaml_mode(result)


async def test_get_lovelace_config_with_url_path(client, mocker):
    """Lines 192-193: url_path is passed in WS command."""
    mock_ws = _make_ws(mocker, {"views": [{"title": "Mobile"}]})
    mocker.patch("websockets.connect", return_value=mock_ws)
    result = await client.get_lovelace_config(url_path="mobile")
    assert result == {"views": [{"title": "Mobile"}]}
    sent = json.loads(mock_ws.send.call_args_list[-1][0][0])
    assert sent.get("url_path") == "mobile"


async def test_get_entities_for_device(client, mocker):
    """Lines 244-247: returns full entity dicts for matching device_id."""
    registry = [
        {"entity_id": "switch.plug", "device_id": "dev-abc", "name": "Plug"},
        {"entity_id": "sensor.temp", "device_id": "dev-abc", "name": "Temp"},
        {"entity_id": "light.other", "device_id": "dev-other", "name": "Other"},
    ]
    mock_ws = _make_ws(mocker, registry)
    mocker.patch("websockets.connect", return_value=mock_ws)
    result = await client.get_entities_for_device("dev-abc")
    assert len(result) == 2
    assert all(e["device_id"] == "dev-abc" for e in result)


async def test_rename_device_name(client, mocker):
    """Lines 249-257: sends correct WS command for device rename."""
    mock_ws = _make_ws(mocker, None)
    mocker.patch("websockets.connect", return_value=mock_ws)
    await client.rename_device_name("dev-abc", "New Name")
    sent = json.loads(mock_ws.send.call_args_list[-1][0][0])
    assert sent["type"] == "config/device_registry/update"
    assert sent["device_id"] == "dev-abc"
    assert sent["name_by_user"] == "New Name"


async def test_get_all_ws_data_zha_failure_returns_empty(client, mocker):
    """zha/devices failure returns empty list instead of raising (ZHA not installed)."""
    auth_msgs = [
        json.dumps({"type": "auth_required"}),
        json.dumps({"type": "auth_ok"}),
    ]
    results = [
        # zha_devices (id=1) fails
        json.dumps({"id": 1, "type": "result", "success": False, "error": {"code": "unknown"}}),
        # remaining commands succeed
        json.dumps({"id": 2, "type": "result", "success": True, "result": []}),
        json.dumps({"id": 3, "type": "result", "success": True, "result": []}),
        json.dumps({"id": 4, "type": "result", "success": True, "result": []}),
        json.dumps({"id": 5, "type": "result", "success": True, "result": []}),
    ]
    mock_ws = mocker.AsyncMock()
    mock_ws.recv = mocker.AsyncMock(side_effect=auth_msgs + results)
    mock_ws.__aenter__ = mocker.AsyncMock(return_value=mock_ws)
    mock_ws.__aexit__ = mocker.AsyncMock(return_value=False)
    mocker.patch("websockets.connect", return_value=mock_ws)

    data = await client.get_all_ws_data()
    assert data["zha_devices"] == []
    assert "entity_registry" in data


async def test_get_all_ws_data_non_automation_failure_raises(client, mocker):
    """Non-graceful command failure (e.g. entity_registry) raises RuntimeError."""
    auth_msgs = [
        json.dumps({"type": "auth_required"}),
        json.dumps({"type": "auth_ok"}),
    ]
    # zha_devices (id=1) succeeds, entity_registry (id=2) fails
    ok_result = json.dumps({"id": 1, "type": "result", "success": True, "result": []})
    fail_result = json.dumps(
        {"id": 2, "type": "result", "success": False, "error": {"code": "unknown"}}
    )
    mock_ws = mocker.AsyncMock()
    mock_ws.recv = mocker.AsyncMock(side_effect=auth_msgs + [ok_result, fail_result])
    mock_ws.__aenter__ = mocker.AsyncMock(return_value=mock_ws)
    mock_ws.__aexit__ = mocker.AsyncMock(return_value=False)
    mocker.patch("websockets.connect", return_value=mock_ws)

    with pytest.raises(RuntimeError, match="failed"):
        await client.get_all_ws_data()


async def test_ws_url_http_to_ws(client):
    """Lines 41-45: _ws_url converts http → ws, https → wss."""
    c_http = HAClient(ha_url="http://ha.test", token=TOKEN)
    assert c_http._ws_url == "ws://ha.test/api/websocket"
    c_https = HAClient(ha_url="https://ha.test", token=TOKEN)
    assert c_https._ws_url == "wss://ha.test/api/websocket"


async def test_ws_session_no_ssl_for_ws_scheme(mocker):
    """_ws_session passes ssl=None for ws:// URIs to avoid websockets incompatibility error."""
    c = HAClient(ha_url="http://ha.test", token=TOKEN, verify_ssl=False)
    mock_ws = mocker.AsyncMock()
    mock_ws.recv = mocker.AsyncMock(
        side_effect=[
            json.dumps({"type": "auth_required"}),
            json.dumps({"type": "auth_ok"}),
        ]
    )
    mock_ws.__aenter__ = mocker.AsyncMock(return_value=mock_ws)
    mock_ws.__aexit__ = mocker.AsyncMock(return_value=False)
    mock_connect = mocker.patch("websockets.connect", return_value=mock_ws)

    async with c._ws_session():
        pass

    _, kwargs = mock_connect.call_args
    assert kwargs.get("ssl") is None


async def test_ws_session_ssl_context_for_wss_scheme(mocker):
    """_ws_session passes an ssl.SSLContext for wss:// URIs."""
    c = HAClient(ha_url="https://ha.test", token=TOKEN, verify_ssl=True)
    mock_ws = mocker.AsyncMock()
    mock_ws.recv = mocker.AsyncMock(
        side_effect=[
            json.dumps({"type": "auth_required"}),
            json.dumps({"type": "auth_ok"}),
        ]
    )
    mock_ws.__aenter__ = mocker.AsyncMock(return_value=mock_ws)
    mock_ws.__aexit__ = mocker.AsyncMock(return_value=False)
    mock_connect = mocker.patch("websockets.connect", return_value=mock_ws)

    async with c._ws_session():
        pass

    _, kwargs = mock_connect.call_args
    # verify_ssl=True → _ssl_context() returns True (websockets uses default TLS)
    assert kwargs.get("ssl") is True


async def test_get_all_ws_data_unexpected_first_message(client, mocker):
    """Line 73: _ws_bulk_query raises when first message is not auth_required."""
    messages = [json.dumps({"type": "unexpected"})]
    mock_ws = mocker.AsyncMock()
    mock_ws.recv = mocker.AsyncMock(side_effect=messages)
    mock_ws.__aenter__ = mocker.AsyncMock(return_value=mock_ws)
    mock_ws.__aexit__ = mocker.AsyncMock(return_value=False)
    mocker.patch("websockets.connect", return_value=mock_ws)

    with pytest.raises(RuntimeError, match="auth_required"):
        await client.get_all_ws_data()


async def test_get_all_ws_data_auth_failure(client, mocker):
    """Line 78: _ws_bulk_query raises when auth_ok not received."""
    messages = [
        json.dumps({"type": "auth_required"}),
        json.dumps({"type": "auth_invalid", "message": "Bad token"}),
    ]
    mock_ws = mocker.AsyncMock()
    mock_ws.recv = mocker.AsyncMock(side_effect=messages)
    mock_ws.__aenter__ = mocker.AsyncMock(return_value=mock_ws)
    mock_ws.__aexit__ = mocker.AsyncMock(return_value=False)
    mocker.patch("websockets.connect", return_value=mock_ws)

    with pytest.raises(RuntimeError, match="authentication failed"):
        await client.get_all_ws_data()


async def test_get_area_registry(client, mocker):
    """Line 132: get_area_registry returns list of areas."""
    mock_ws = _make_ws(mocker, [{"area_id": "living_room", "name": "Living Room"}])
    mocker.patch("websockets.connect", return_value=mock_ws)
    result = await client.get_area_registry()
    assert result == [{"area_id": "living_room", "name": "Living Room"}]


@respx.mock
async def test_get_lovelace_config_rest_fallback_with_url_path(client, mocker):
    """Line 207: REST fallback includes url_path query param."""
    messages = [
        json.dumps({"type": "auth_required"}),
        json.dumps({"type": "auth_ok"}),
        json.dumps({"id": 1, "type": "result", "success": False, "error": {"code": "failed"}}),
    ]
    mock_ws = mocker.AsyncMock()
    mock_ws.recv = mocker.AsyncMock(side_effect=messages)
    mock_ws.__aenter__ = mocker.AsyncMock(return_value=mock_ws)
    mock_ws.__aexit__ = mocker.AsyncMock(return_value=False)
    mocker.patch("websockets.connect", return_value=mock_ws)

    lv_config = {"views": [{"title": "Mobile"}]}
    respx.get(f"{HA_URL}/api/lovelace/config").mock(
        return_value=httpx.Response(200, json=lv_config)
    )
    result = await client.get_lovelace_config(url_path="mobile")
    assert result == lv_config


@respx.mock
async def test_get_lovelace_config_strategy_returns_yaml_mode(client, mocker):
    """WS returns a strategy-based config → get_lovelace_config returns YAML_MODE."""
    from zigporter.ha_client import is_yaml_mode  # noqa: PLC0415

    mock_ws = _make_ws(mocker, {"strategy": {"type": "original-states"}})
    mocker.patch("websockets.connect", return_value=mock_ws)
    result = await client.get_lovelace_config()
    assert is_yaml_mode(result)


@respx.mock
async def test_get_lovelace_config_rest_fallback_strategy_returns_yaml_mode(client, mocker):
    """WS fails (non-yaml-mode error) → REST returns strategy config → YAML_MODE."""
    from zigporter.ha_client import is_yaml_mode  # noqa: PLC0415

    mock_ws = _make_ws_fail(mocker)
    mocker.patch("websockets.connect", return_value=mock_ws)
    respx.get(f"{HA_URL}/api/lovelace/config").mock(
        return_value=httpx.Response(200, json={"strategy": {"type": "grid"}})
    )
    result = await client.get_lovelace_config()
    assert is_yaml_mode(result)


async def test_delete_entity(client, mocker):
    mock_ws = _make_ws(mocker, None)
    mocker.patch("websockets.connect", return_value=mock_ws)
    await client.delete_entity("switch.plug")
    sent = json.loads(mock_ws.send.call_args_list[-1][0][0])
    assert sent["type"] == "config/entity_registry/remove"
    assert sent["entity_id"] == "switch.plug"


async def test_remove_device(client, mocker):
    mock_ws = _make_ws(mocker, None)
    mocker.patch("websockets.connect", return_value=mock_ws)
    await client.remove_device("dev-abc")
    sent = json.loads(mock_ws.send.call_args_list[-1][0][0])
    assert sent["type"] == "config/device_registry/remove"
    assert sent["device_id"] == "dev-abc"


async def test_reload_config_entry(client, mocker):
    mock_ws = _make_ws(mocker, None)
    mocker.patch("websockets.connect", return_value=mock_ws)
    await client.reload_config_entry("entry-123")
    sent = json.loads(mock_ws.send.call_args_list[-1][0][0])
    assert sent["type"] == "config_entries/reload"
    assert sent["entry_id"] == "entry-123"


async def test_get_z2m_device_id_skips_non_mqtt_platform(client, mocker):
    """Line 229: non-mqtt platform identifiers are skipped."""
    registry = [
        {
            "id": "dev-zigbee",
            "identifiers": [
                ["zha", "00:11:22:33:44:55:66:77"],
                ["mqtt", "zigbee2mqtt_0x0011223344556677"],
            ],
        }
    ]
    mock_ws = _make_ws(mocker, registry)
    mocker.patch("websockets.connect", return_value=mock_ws)
    result = await client.get_z2m_device_id("00:11:22:33:44:55:66:77")
    assert result == "dev-zigbee"


# ---------------------------------------------------------------------------
# ZHA pairing methods
# ---------------------------------------------------------------------------


async def test_enable_zha_permit_join(client, mocker):
    """enable_zha_permit_join calls the zha.permit service."""
    mock_ws = _make_ws(mocker, None)
    mocker.patch("websockets.connect", return_value=mock_ws)
    await client.enable_zha_permit_join(duration=120)
    sent = json.loads(mock_ws.send.call_args_list[-1][0][0])
    assert sent["type"] == "call_service"
    assert sent["domain"] == "zha"
    assert sent["service"] == "permit"
    assert sent["service_data"] == {"duration": 120}


async def test_enable_zha_permit_join_default_duration(client, mocker):
    """Default duration is 60 seconds."""
    mock_ws = _make_ws(mocker, None)
    mocker.patch("websockets.connect", return_value=mock_ws)
    await client.enable_zha_permit_join()
    sent = json.loads(mock_ws.send.call_args_list[-1][0][0])
    assert sent["service_data"] == {"duration": 60}


async def test_get_zha_device_id_found(client, mocker):
    """get_zha_device_id returns device ID when ZHA identifier matches."""
    registry = [
        {
            "id": "dev-zha-abc",
            "identifiers": [["zha", "00:11:22:33:44:55:66:77"]],
        },
        {
            "id": "dev-other",
            "identifiers": [["mqtt", "zigbee2mqtt_0xaabbccddeeff0011"]],
        },
    ]
    mock_ws = _make_ws(mocker, registry)
    mocker.patch("websockets.connect", return_value=mock_ws)
    result = await client.get_zha_device_id("00:11:22:33:44:55:66:77")
    assert result == "dev-zha-abc"


async def test_get_zha_device_id_not_found(client, mocker):
    """get_zha_device_id returns None when no ZHA device matches."""
    registry = [
        {
            "id": "dev-mqtt",
            "identifiers": [["mqtt", "zigbee2mqtt_0x0011223344556677"]],
        },
    ]
    mock_ws = _make_ws(mocker, registry)
    mocker.patch("websockets.connect", return_value=mock_ws)
    result = await client.get_zha_device_id("ff:ff:ff:ff:ff:ff:ff:ff")
    assert result is None


async def test_get_zha_device_id_normalizes_ieee(client, mocker):
    """get_zha_device_id normalizes both input IEEE and registry entries."""
    registry = [
        {
            "id": "dev-zha-1",
            "identifiers": [["zha", "00:11:22:33:44:55:66:77"]],
        },
    ]
    mock_ws = _make_ws(mocker, registry)
    mocker.patch("websockets.connect", return_value=mock_ws)
    # Input with 0x prefix, no colons
    result = await client.get_zha_device_id("0x0011223344556677")
    assert result == "dev-zha-1"
