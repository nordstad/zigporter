"""Tests for the stale command detection logic and helpers."""

import questionary

from zigporter.commands.stale import (
    _device_is_offline,
    _integration,
    _is_ha_core_device,
    _zha_ieee_from_identifiers,
    detect_offline_devices,
)
from zigporter.stale_state import StaleState


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
    from zigporter.stale_state import mark_ignored, mark_stale

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
