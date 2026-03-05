"""Tests for the stale command detection logic and helpers."""

from unittest.mock import MagicMock

import pytest  # noqa: F401
import questionary

from zigporter.commands.stale import (
    _DONE,
    _device_is_offline,
    _do_remove_device,
    _handle_clear,
    _handle_ignore,
    _handle_mark_stale,
    _handle_remove,
    _handle_suppress,
    _integration,
    _is_ha_core_device,
    _show_device_detail,
    _zha_ieee_from_identifiers,
    detect_offline_devices,
    stale_command,
)
from zigporter.stale_state import StaleDeviceStatus, StaleState, mark_stale, mark_suppressed


# ---------------------------------------------------------------------------
# _is_ha_core_device
# ---------------------------------------------------------------------------


def test_is_ha_core_device_true():
    device = {"identifiers": [["homeassistant", "Home Assistant"]]}
    assert _is_ha_core_device(device) is True


def test_is_ha_core_device_false_for_zha():
    device = {"identifiers": [["zha", "00:11:22:33:44:55:66:77"]]}
    assert _is_ha_core_device(device) is False


def test_is_ha_core_device_false_for_empty():
    assert _is_ha_core_device({}) is False


# ---------------------------------------------------------------------------
# _integration
# ---------------------------------------------------------------------------


def test_integration_returns_first_platform():
    device = {"identifiers": [["zha", "00:11:22:33:44:55:66:77"]]}
    assert _integration(device) == "zha"


def test_integration_returns_mqtt():
    device = {"identifiers": [["mqtt", "zigbee2mqtt_abc"]]}
    assert _integration(device) == "mqtt"


def test_integration_returns_unknown_for_empty():
    assert _integration({}) == "unknown"


# ---------------------------------------------------------------------------
# _zha_ieee_from_identifiers
# ---------------------------------------------------------------------------


def test_zha_ieee_from_identifiers_returns_ieee():
    identifiers = [["zha", "00:11:22:33:44:55:66:77"]]
    assert _zha_ieee_from_identifiers(identifiers) == "00:11:22:33:44:55:66:77"


def test_zha_ieee_from_identifiers_returns_none_for_non_zha():
    identifiers = [["mqtt", "zigbee2mqtt_0x0011223344556677"]]
    assert _zha_ieee_from_identifiers(identifiers) is None


def test_zha_ieee_from_identifiers_returns_none_for_empty():
    assert _zha_ieee_from_identifiers([]) is None


# ---------------------------------------------------------------------------
# _device_is_offline
# ---------------------------------------------------------------------------


def test_device_is_offline_ghost_device():
    """Device with no entities is offline (ghost)."""
    device = {"id": "dev-1", "identifiers": [["zha", "abc"]]}
    assert _device_is_offline(device, [], {}) is True


def test_device_is_offline_all_unavailable():
    device = {"id": "dev-1"}
    entity_registry = [
        {"entity_id": "light.kitchen", "device_id": "dev-1", "disabled_by": None},
        {"entity_id": "sensor.kitchen", "device_id": "dev-1", "disabled_by": None},
    ]
    state_map = {"light.kitchen": "unavailable", "sensor.kitchen": "unknown"}
    assert _device_is_offline(device, entity_registry, state_map) is True


def test_device_is_offline_some_entities_online():
    device = {"id": "dev-1"}
    entity_registry = [
        {"entity_id": "light.kitchen", "device_id": "dev-1", "disabled_by": None},
        {"entity_id": "sensor.kitchen", "device_id": "dev-1", "disabled_by": None},
    ]
    state_map = {"light.kitchen": "on", "sensor.kitchen": "unavailable"}
    assert _device_is_offline(device, entity_registry, state_map) is False


def test_device_is_offline_all_entities_disabled():
    """Device with only disabled entities is not flagged offline."""
    device = {"id": "dev-1"}
    entity_registry = [
        {"entity_id": "light.kitchen", "device_id": "dev-1", "disabled_by": "user"},
    ]
    state_map = {"light.kitchen": "unavailable"}
    assert _device_is_offline(device, entity_registry, state_map) is False


def test_device_is_offline_missing_state_treated_as_unknown():
    """An entity with no state entry is treated as 'unknown' → offline."""
    device = {"id": "dev-1"}
    entity_registry = [
        {"entity_id": "light.kitchen", "device_id": "dev-1", "disabled_by": None},
    ]
    state_map: dict = {}
    assert _device_is_offline(device, entity_registry, state_map) is True


# ---------------------------------------------------------------------------
# detect_offline_devices
# ---------------------------------------------------------------------------


def _make_device(
    device_id: str,
    identifiers=None,
    area_id=None,
    name="Device",
    entry_type=None,
    via_device_id=None,
):
    d = {
        "id": device_id,
        "name": name,
        "name_by_user": None,
        "area_id": area_id,
        "identifiers": identifiers or [["zha", device_id]],
    }
    if entry_type is not None:
        d["entry_type"] = entry_type
    if via_device_id is not None:
        d["via_device_id"] = via_device_id
    return d


def test_detect_offline_devices_returns_offline_device():
    devices = [_make_device("dev-1", name="Kitchen Light")]
    entity_registry = [
        {"entity_id": "light.kitchen", "device_id": "dev-1", "disabled_by": None},
    ]
    area_registry = [{"area_id": "kitchen", "name": "Kitchen"}]
    states = [{"entity_id": "light.kitchen", "state": "unavailable"}]

    result = detect_offline_devices(devices, entity_registry, area_registry, states)
    assert len(result) == 1
    assert result[0]["device_id"] == "dev-1"
    assert result[0]["name"] == "Kitchen Light"


def test_detect_offline_devices_excludes_service_entry_type():
    """Integration hub devices (entry_type='service') must not be reported as offline."""
    devices = [
        _make_device(
            "dev-hub",
            identifiers=[["ikea", "dirigera_hub"]],
            name="IKEA Dirigera Hub",
            entry_type="service",
        ),
        _make_device("dev-1", name="Ceiling Bulb"),
    ]
    entity_registry = [
        # Hub has an unavailable firmware sensor — should not trigger a stale hit
        {"entity_id": "update.hub_firmware", "device_id": "dev-hub", "disabled_by": None},
        {"entity_id": "light.bulb", "device_id": "dev-1", "disabled_by": None},
    ]
    states = [
        {"entity_id": "update.hub_firmware", "state": "unavailable"},
        {"entity_id": "light.bulb", "state": "unavailable"},
    ]

    result = detect_offline_devices(devices, entity_registry, [], states)
    assert all(r["device_id"] != "dev-hub" for r in result), (
        "service entry_type device must be excluded"
    )
    assert any(r["device_id"] == "dev-1" for r in result)


def test_detect_offline_devices_excludes_ha_core():
    devices = [
        _make_device("dev-ha", identifiers=[["homeassistant", "core"]], name="Home Assistant"),
        _make_device("dev-1", name="Sensor"),
    ]
    entity_registry = [
        {"entity_id": "sensor.temp", "device_id": "dev-1", "disabled_by": None},
    ]
    states = [{"entity_id": "sensor.temp", "state": "unavailable"}]

    result = detect_offline_devices(devices, entity_registry, [], states)
    assert all(r["device_id"] != "dev-ha" for r in result)
    assert any(r["device_id"] == "dev-1" for r in result)


def test_detect_offline_devices_excludes_hub_with_active_children():
    """A gateway whose own entities are unavailable must not be flagged when its children are online."""
    devices = [
        _make_device("gw", identifiers=[["plejd", "gwy01"]], name="Plejd Gateway"),
        # Child device — online, connected via the gateway
        _make_device("light-1", name="Kitchen Light", via_device_id="gw"),
    ]
    entity_registry = [
        {"entity_id": "button.gw_identify", "device_id": "gw", "disabled_by": None},
        {"entity_id": "light.kitchen", "device_id": "light-1", "disabled_by": None},
    ]
    states = [
        {"entity_id": "button.gw_identify", "state": "unavailable"},
        {"entity_id": "light.kitchen", "state": "on"},
    ]

    result = detect_offline_devices(devices, entity_registry, [], states)
    assert all(r["device_id"] != "gw" for r in result), "hub with active children must be excluded"
    assert result == []


def test_detect_offline_devices_includes_hub_when_all_children_offline():
    """A gateway should still be flagged when ALL its children are also offline."""
    devices = [
        _make_device("gw", identifiers=[["plejd", "gwy01"]], name="Plejd Gateway"),
        _make_device("light-1", name="Kitchen Light", via_device_id="gw"),
    ]
    entity_registry = [
        {"entity_id": "button.gw_identify", "device_id": "gw", "disabled_by": None},
        {"entity_id": "light.kitchen", "device_id": "light-1", "disabled_by": None},
    ]
    states = [
        {"entity_id": "button.gw_identify", "state": "unavailable"},
        {"entity_id": "light.kitchen", "state": "unavailable"},
    ]

    result = detect_offline_devices(devices, entity_registry, [], states)
    device_ids = {r["device_id"] for r in result}
    assert "gw" in device_ids, "hub with all-offline children should be flagged"
    assert "light-1" in device_ids


def test_detect_offline_devices_excludes_online_device():
    devices = [_make_device("dev-1")]
    entity_registry = [
        {"entity_id": "light.lamp", "device_id": "dev-1", "disabled_by": None},
    ]
    states = [{"entity_id": "light.lamp", "state": "on"}]

    result = detect_offline_devices(devices, entity_registry, [], states)
    assert result == []


def test_detect_offline_devices_includes_ghost_device():
    """Devices with no entities at all should be flagged."""
    devices = [_make_device("dev-ghost", name="Ghost")]
    result = detect_offline_devices(devices, [], [], [])
    assert len(result) == 1
    assert result[0]["device_id"] == "dev-ghost"


def test_detect_offline_devices_area_name_resolved():
    devices = [_make_device("dev-1", area_id="living_room", name="Sensor")]
    entity_registry = [
        {"entity_id": "sensor.temp", "device_id": "dev-1", "disabled_by": None},
    ]
    area_registry = [{"area_id": "living_room", "name": "Living Room"}]
    states = [{"entity_id": "sensor.temp", "state": "unavailable"}]

    result = detect_offline_devices(devices, entity_registry, area_registry, states)
    assert result[0]["area_name"] == "Living Room"


def test_detect_offline_devices_state_map_populated():
    devices = [_make_device("dev-1", name="Sensor")]
    entity_registry = [
        {"entity_id": "sensor.temp", "device_id": "dev-1", "disabled_by": None},
        {"entity_id": "sensor.hum", "device_id": "dev-1", "disabled_by": None},
    ]
    states = [
        {"entity_id": "sensor.temp", "state": "unavailable"},
        {"entity_id": "sensor.hum", "state": "unknown"},
    ]

    result = detect_offline_devices(devices, entity_registry, [], states)
    assert result[0]["state_map"] == {"sensor.temp": "unavailable", "sensor.hum": "unknown"}


def test_detect_offline_devices_includes_identifiers():
    """The identifiers field must be forwarded so removal fallback can extract the ZHA IEEE."""
    devices = [_make_device("dev-1", identifiers=[["zha", "00:11:22:33:44:55:66:77"]])]
    result = detect_offline_devices(devices, [], [], [])
    assert result[0]["identifiers"] == [["zha", "00:11:22:33:44:55:66:77"]]


# ---------------------------------------------------------------------------
# _build_picker_choices grouping
# ---------------------------------------------------------------------------


def test_build_picker_choices_groups():
    from zigporter.commands.stale import _build_picker_choices
    from zigporter.stale_state import mark_ignored

    offline = [
        {
            "device_id": "new-1",
            "name": "New Device",
            "area_name": "",
            "integration": "zha",
            "entity_ids": [],
            "state_map": {},
        },
        {
            "device_id": "stale-1",
            "name": "Stale Device",
            "area_name": "",
            "integration": "zha",
            "entity_ids": [],
            "state_map": {},
        },
        {
            "device_id": "ignored-1",
            "name": "Ignored Device",
            "area_name": "",
            "integration": "zha",
            "entity_ids": [],
            "state_map": {},
        },
    ]
    state = StaleState()
    mark_stale(state, "stale-1", "Stale Device")
    mark_ignored(state, "ignored-1", "Ignored Device")

    choices = _build_picker_choices(offline, state)

    # Extract only Choice entries (not Separators) whose value is a device dict
    device_labels = [
        c.title for c in choices if isinstance(c, questionary.Choice) and isinstance(c.value, dict)
    ]
    # New device first, then stale, then ignored
    assert "New Device" in device_labels[0]
    assert "Stale Device" in device_labels[1]
    assert "Ignored Device" in device_labels[2]


# ---------------------------------------------------------------------------
# _do_remove_device
# ---------------------------------------------------------------------------


_DEVICE = {"device_id": "dev-1", "identifiers": [["zha", "00:11:22:33:44:55:66:77"]]}
_DEVICE_NON_ZHA = {"device_id": "dev-1", "identifiers": [["mqtt", "some_id"]]}


async def test_do_remove_device_success(mocker):
    mock_ha = mocker.AsyncMock()
    mock_ha.get_stale_check_data.return_value = {"device_registry": []}
    mocker.patch("zigporter.commands.stale.HAClient", return_value=mock_ha)
    mocker.patch("asyncio.sleep")

    result = await _do_remove_device("http://ha.test", "token", False, _DEVICE)

    mock_ha.remove_device.assert_called_once_with("dev-1")
    assert result is True


async def test_do_remove_device_still_in_registry(mocker):
    mock_ha = mocker.AsyncMock()
    mock_ha.get_stale_check_data.return_value = {"device_registry": [{"id": "dev-1"}]}
    mocker.patch("zigporter.commands.stale.HAClient", return_value=mock_ha)
    mocker.patch("asyncio.sleep")

    result = await _do_remove_device("http://ha.test", "token", False, _DEVICE)

    assert result is False


async def test_do_remove_device_unknown_command_zha_fallback(mocker):
    mock_ha = mocker.AsyncMock()
    mock_ha.remove_device.side_effect = RuntimeError("unknown_command")
    mock_ha.get_stale_check_data.return_value = {"device_registry": []}
    mocker.patch("zigporter.commands.stale.HAClient", return_value=mock_ha)
    mocker.patch("asyncio.sleep")

    result = await _do_remove_device("http://ha.test", "token", False, _DEVICE)

    mock_ha.remove_zha_device.assert_called_once_with("00:11:22:33:44:55:66:77")
    assert result is True


async def test_do_remove_device_unknown_command_no_zha_ieee(mocker):
    mock_ha = mocker.AsyncMock()
    mock_ha.remove_device.side_effect = RuntimeError("unknown_command")
    mocker.patch("zigporter.commands.stale.HAClient", return_value=mock_ha)

    result = await _do_remove_device("http://ha.test", "token", False, _DEVICE_NON_ZHA)

    mock_ha.remove_zha_device.assert_not_called()
    assert result is False


async def test_do_remove_device_other_error_reraises(mocker):
    mock_ha = mocker.AsyncMock()
    mock_ha.remove_device.side_effect = RuntimeError("server_error: boom")
    mocker.patch("zigporter.commands.stale.HAClient", return_value=mock_ha)

    with pytest.raises(RuntimeError, match="server_error"):
        await _do_remove_device("http://ha.test", "token", False, _DEVICE)


# ---------------------------------------------------------------------------
# _handle_remove
# ---------------------------------------------------------------------------


def _device_fixture():
    return {"device_id": "dev-1", "name": "Test Bulb", "identifiers": []}


def test_handle_remove_user_declines(mocker, tmp_path):
    mocker.patch("questionary.confirm", return_value=MagicMock(ask=MagicMock(return_value=False)))
    mock_run = mocker.patch("zigporter.commands.stale.asyncio.run")
    state = StaleState()
    removed_ids: set = set()

    _handle_remove(_device_fixture(), state, tmp_path / "s.json", removed_ids, "url", "tok", False)

    mock_run.assert_not_called()
    assert "dev-1" not in removed_ids


def test_handle_remove_success(mocker, tmp_path):
    mocker.patch("questionary.confirm", return_value=MagicMock(ask=MagicMock(return_value=True)))
    mocker.patch("zigporter.commands.stale.asyncio.run", return_value=True)
    state = StaleState()
    removed_ids: set = set()

    _handle_remove(_device_fixture(), state, tmp_path / "s.json", removed_ids, "url", "tok", False)

    assert "dev-1" in removed_ids


def test_handle_remove_still_present(mocker, tmp_path, capsys):
    mocker.patch("questionary.confirm", return_value=MagicMock(ask=MagicMock(return_value=True)))
    mocker.patch("zigporter.commands.stale.asyncio.run", return_value=False)
    mocker.patch("zigporter.commands.stale._do_remove_device")
    state = StaleState()
    removed_ids: set = set()

    _handle_remove(_device_fixture(), state, tmp_path / "s.json", removed_ids, "url", "tok", False)

    assert "dev-1" not in removed_ids


def test_handle_remove_exception(mocker, tmp_path, capsys):
    mocker.patch("questionary.confirm", return_value=MagicMock(ask=MagicMock(return_value=True)))
    mocker.patch("zigporter.commands.stale.asyncio.run", side_effect=RuntimeError("boom"))
    mocker.patch("zigporter.commands.stale._do_remove_device")
    state = StaleState()
    removed_ids: set = set()

    _handle_remove(_device_fixture(), state, tmp_path / "s.json", removed_ids, "url", "tok", False)

    assert "dev-1" not in removed_ids


# ---------------------------------------------------------------------------
# _handle_mark_stale / _handle_ignore / _handle_clear
# ---------------------------------------------------------------------------


def test_handle_mark_stale_with_note(mocker, tmp_path):
    mocker.patch(
        "questionary.text", return_value=MagicMock(ask=MagicMock(return_value="check later"))
    )
    state = StaleState()
    device = {"device_id": "dev-1", "name": "Bulb"}

    _handle_mark_stale(device, state, tmp_path / "s.json")

    assert state.devices["dev-1"].status == StaleDeviceStatus.STALE
    assert state.devices["dev-1"].note == "check later"


def test_handle_mark_stale_empty_note_stored_as_none(mocker, tmp_path):
    mocker.patch("questionary.text", return_value=MagicMock(ask=MagicMock(return_value="")))
    state = StaleState()
    device = {"device_id": "dev-1", "name": "Bulb"}

    _handle_mark_stale(device, state, tmp_path / "s.json")

    assert state.devices["dev-1"].note is None


def test_handle_ignore(tmp_path):
    state = StaleState()
    device = {"device_id": "dev-1", "name": "Bulb"}

    _handle_ignore(device, state, tmp_path / "s.json")

    assert state.devices["dev-1"].status == StaleDeviceStatus.IGNORED


def test_handle_clear(tmp_path):
    state = StaleState()
    mark_stale(state, "dev-1", "Bulb", note="old note")
    device = {"device_id": "dev-1", "name": "Bulb"}

    _handle_clear(device, state, tmp_path / "s.json")

    assert "dev-1" not in state.devices


# ---------------------------------------------------------------------------
# _handle_suppress
# ---------------------------------------------------------------------------


def test_handle_suppress_sets_suppressed_status(tmp_path):
    state = StaleState()
    device = {"device_id": "dev-1", "name": "Ghost Device"}
    removed_ids: set = set()

    _handle_suppress(device, state, tmp_path / "s.json", removed_ids)

    assert state.devices["dev-1"].status == StaleDeviceStatus.SUPPRESSED
    assert "dev-1" in removed_ids


def test_handle_suppress_adds_to_removed_ids(tmp_path):
    """Suppress must add the device to removed_ids so it vanishes from the current session."""
    state = StaleState()
    device = {"device_id": "dev-42", "name": "Phantom"}
    removed_ids: set = set()

    _handle_suppress(device, state, tmp_path / "s.json", removed_ids)

    assert "dev-42" in removed_ids


def test_handle_suppress_persists_to_disk(tmp_path):
    state = StaleState()
    device = {"device_id": "dev-1", "name": "Ghost"}
    removed_ids: set = set()
    state_path = tmp_path / "s.json"

    _handle_suppress(device, state, state_path, removed_ids)

    from zigporter.stale_state import load_stale_state

    loaded = load_stale_state(state_path)
    assert loaded.devices["dev-1"].status == StaleDeviceStatus.SUPPRESSED


def test_handle_suppress_clears_existing_note(tmp_path):
    state = StaleState()
    mark_stale(state, "dev-1", "Ghost", note="investigate")
    device = {"device_id": "dev-1", "name": "Ghost"}
    removed_ids: set = set()

    _handle_suppress(device, state, tmp_path / "s.json", removed_ids)

    assert state.devices["dev-1"].note is None


# ---------------------------------------------------------------------------
# _show_device_detail
# ---------------------------------------------------------------------------


def _detail_device(entity_ids=None, area_name="Kitchen"):
    ids = entity_ids or ["light.bulb"]
    return {
        "device_id": "dev-1",
        "name": "Test Bulb",
        "area_name": area_name,
        "integration": "zha",
        "entity_ids": ids,
        "state_map": {eid: "unavailable" for eid in ids},
        "identifiers": [],
    }


def test_show_device_detail_action_stale(mocker, tmp_path):
    mocker.patch("questionary.select", return_value=MagicMock(ask=MagicMock(return_value="stale")))
    mock_stale = mocker.patch("zigporter.commands.stale._handle_mark_stale")
    state = StaleState()

    _show_device_detail(_detail_device(), state, tmp_path / "s.json", set(), "url", "tok", False)

    mock_stale.assert_called_once()


def test_show_device_detail_action_ignore(mocker, tmp_path):
    mocker.patch("questionary.select", return_value=MagicMock(ask=MagicMock(return_value="ignore")))
    mock_ignore = mocker.patch("zigporter.commands.stale._handle_ignore")
    state = StaleState()

    _show_device_detail(_detail_device(), state, tmp_path / "s.json", set(), "url", "tok", False)

    mock_ignore.assert_called_once()


def test_show_device_detail_action_remove(mocker, tmp_path):
    mocker.patch("questionary.select", return_value=MagicMock(ask=MagicMock(return_value="remove")))
    mock_remove = mocker.patch("zigporter.commands.stale._handle_remove")
    state = StaleState()

    _show_device_detail(_detail_device(), state, tmp_path / "s.json", set(), "url", "tok", False)

    mock_remove.assert_called_once()


def test_show_device_detail_action_suppress(mocker, tmp_path):
    mocker.patch(
        "questionary.select", return_value=MagicMock(ask=MagicMock(return_value="suppress"))
    )
    mock_suppress = mocker.patch("zigporter.commands.stale._handle_suppress")
    state = StaleState()

    _show_device_detail(_detail_device(), state, tmp_path / "s.json", set(), "url", "tok", False)

    mock_suppress.assert_called_once()


def test_show_device_detail_suppress_choice_always_present(mocker, tmp_path):
    """'Suppress' must appear regardless of whether the device has an existing entry."""
    choices_seen = []

    def capture_select(question, choices, **kwargs):
        choices_seen.extend(c.value for c in choices if isinstance(c, questionary.Choice))
        return MagicMock(ask=MagicMock(return_value="back"))

    mocker.patch("questionary.select", side_effect=capture_select)
    state = StaleState()

    _show_device_detail(_detail_device(), state, tmp_path / "s.json", set(), "url", "tok", False)
    assert "suppress" in choices_seen


def test_show_device_detail_action_clear_only_when_entry_exists(mocker, tmp_path):
    """Clear option appears only when device already has a state entry."""
    choices_seen = []

    def capture_select(question, choices, **kwargs):
        choices_seen.extend(c.value for c in choices if isinstance(c, questionary.Choice))
        return MagicMock(ask=MagicMock(return_value="back"))

    mocker.patch("questionary.select", side_effect=capture_select)
    state = StaleState()

    # No entry → no "clear" choice
    _show_device_detail(_detail_device(), state, tmp_path / "s.json", set(), "url", "tok", False)
    assert "clear" not in choices_seen

    choices_seen.clear()
    mark_stale(state, "dev-1", "Test Bulb")

    # Entry present → "clear" choice added
    _show_device_detail(_detail_device(), state, tmp_path / "s.json", set(), "url", "tok", False)
    assert "clear" in choices_seen


def test_show_device_detail_action_back_is_noop(mocker, tmp_path):
    mocker.patch("questionary.select", return_value=MagicMock(ask=MagicMock(return_value="back")))
    mock_remove = mocker.patch("zigporter.commands.stale._handle_remove")
    mock_stale = mocker.patch("zigporter.commands.stale._handle_mark_stale")
    state = StaleState()

    _show_device_detail(_detail_device(), state, tmp_path / "s.json", set(), "url", "tok", False)

    mock_remove.assert_not_called()
    mock_stale.assert_not_called()


def test_show_device_detail_no_entities(mocker, tmp_path, capsys):
    mocker.patch("questionary.select", return_value=MagicMock(ask=MagicMock(return_value="back")))
    state = StaleState()
    device = _detail_device(entity_ids=[])
    device["area_name"] = ""

    _show_device_detail(device, state, tmp_path / "s.json", set(), "url", "tok", False)
    # No crash — "(no entities)" path exercised


def test_show_device_detail_truncates_entities(mocker, tmp_path):
    mocker.patch("questionary.select", return_value=MagicMock(ask=MagicMock(return_value="back")))
    state = StaleState()
    ids = [f"light.bulb_{i}" for i in range(8)]
    device = _detail_device(entity_ids=ids)

    _show_device_detail(device, state, tmp_path / "s.json", set(), "url", "tok", False)
    # No crash — truncation (> 5 entities) path exercised


def test_show_device_detail_shows_note(mocker, tmp_path):
    mocker.patch("questionary.select", return_value=MagicMock(ask=MagicMock(return_value="back")))
    state = StaleState()
    mark_stale(state, "dev-1", "Test Bulb", note="replace soon")

    _show_device_detail(_detail_device(), state, tmp_path / "s.json", set(), "url", "tok", False)
    # No crash — note display path exercised


# ---------------------------------------------------------------------------
# stale_command
# ---------------------------------------------------------------------------


def _offline_device():
    return {
        "device_id": "dev-1",
        "name": "Old Bulb",
        "area_name": "Bedroom",
        "integration": "zha",
        "entity_ids": ["light.old_bulb"],
        "state_map": {"light.old_bulb": "unavailable"},
        "identifiers": [],
    }


async def _fake_fetch_one(*_args):
    return [_offline_device()]


async def _fake_fetch_empty(*_args):
    return []


def test_stale_command_connection_error(mocker, tmp_path):
    mocker.patch(
        "zigporter.commands.stale._fetch_offline_devices",
        side_effect=RuntimeError("cannot connect"),
    )

    stale_command("url", "tok", False, state_path=tmp_path / "s.json")
    # No crash; error message printed via Rich (not captured in capsys easily)


def test_stale_command_no_offline_devices(mocker, tmp_path):
    mocker.patch(
        "zigporter.commands.stale._fetch_offline_devices",
        side_effect=_fake_fetch_empty,
    )

    stale_command("url", "tok", False, state_path=tmp_path / "s.json")
    # Exits early — no questionary interaction


def test_stale_command_user_selects_done(mocker, tmp_path):
    mocker.patch(
        "zigporter.commands.stale._fetch_offline_devices",
        side_effect=_fake_fetch_one,
    )
    mocker.patch("questionary.select", return_value=MagicMock(ask=MagicMock(return_value=_DONE)))

    stale_command("url", "tok", False, state_path=tmp_path / "s.json")
    # Exits loop cleanly on Done sentinel


def test_stale_command_all_devices_removed(mocker, tmp_path):
    mocker.patch(
        "zigporter.commands.stale._fetch_offline_devices",
        side_effect=_fake_fetch_one,
    )

    def fake_show_detail(device, state, state_path, removed_ids, *_args):
        removed_ids.add(device["device_id"])

    mocker.patch("zigporter.commands.stale._show_device_detail", side_effect=fake_show_detail)
    mocker.patch(
        "questionary.select", return_value=MagicMock(ask=MagicMock(return_value=_offline_device()))
    )

    stale_command("url", "tok", False, state_path=tmp_path / "s.json")
    # Loop exits via "All offline devices have been handled." branch


def test_stale_command_records_first_seen(mocker, tmp_path):
    mocker.patch(
        "zigporter.commands.stale._fetch_offline_devices",
        side_effect=_fake_fetch_one,
    )
    mocker.patch("questionary.select", return_value=MagicMock(ask=MagicMock(return_value=_DONE)))
    state_path = tmp_path / "s.json"

    stale_command("url", "tok", False, state_path=state_path)

    from zigporter.stale_state import load_stale_state

    state = load_stale_state(state_path)
    assert "dev-1" in state.devices


def test_stale_command_prunes_resolved_entries(mocker, tmp_path):
    """Entries in stale.json whose device is no longer offline should be pruned."""
    mocker.patch(
        "zigporter.commands.stale._fetch_offline_devices",
        side_effect=_fake_fetch_one,
    )
    mocker.patch("questionary.select", return_value=MagicMock(ask=MagicMock(return_value=_DONE)))
    state_path = tmp_path / "s.json"

    # Pre-populate state with a device that is NOT in the offline list
    from zigporter.stale_state import load_stale_state, save_stale_state

    pre_state = StaleState()
    mark_stale(pre_state, "ghost-id", "Ghost Device")
    save_stale_state(pre_state, state_path)

    stale_command("url", "tok", False, state_path=state_path)

    state = load_stale_state(state_path)
    assert "ghost-id" not in state.devices


def test_stale_command_suppressed_devices_hidden_from_picker(mocker, tmp_path):
    """Suppressed devices must not appear in the picker and must not cause 'all handled' early."""
    mocker.patch(
        "zigporter.commands.stale._fetch_offline_devices",
        side_effect=_fake_fetch_one,
    )
    mocker.patch("questionary.select", return_value=MagicMock(ask=MagicMock(return_value=_DONE)))
    state_path = tmp_path / "s.json"

    # Pre-suppress dev-1
    from zigporter.stale_state import save_stale_state

    pre_state = StaleState()
    mark_suppressed(pre_state, "dev-1", "Old Bulb")
    save_stale_state(pre_state, state_path)

    stale_command("url", "tok", False, state_path=state_path)
    # questionary.select should NOT have been called (visible list is empty → handled message)
    # The command completes without error


def test_stale_command_suppressed_count_does_not_call_picker(mocker, tmp_path):
    """When all offline devices are suppressed, the picker is never shown."""
    mocker.patch(
        "zigporter.commands.stale._fetch_offline_devices",
        side_effect=_fake_fetch_one,
    )
    mock_select = mocker.patch(
        "questionary.select", return_value=MagicMock(ask=MagicMock(return_value=_DONE))
    )
    state_path = tmp_path / "s.json"

    pre_state = StaleState()
    mark_suppressed(pre_state, "dev-1", "Old Bulb")
    from zigporter.stale_state import save_stale_state

    save_stale_state(pre_state, state_path)

    stale_command("url", "tok", False, state_path=state_path)

    mock_select.assert_not_called()
