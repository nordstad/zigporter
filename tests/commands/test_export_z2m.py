"""Tests for the Z2M export command."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from zigporter.commands.export_z2m import build_z2m_export, run_z2m_export


@pytest.fixture
def z2m_devices():
    return [
        {
            "ieee_address": "0x0011223344556677",
            "friendly_name": "Kitchen Plug",
            "type": "Router",
            "manufacturer": "IKEA",
            "model_id": "E1603",
            "definition": {"vendor": "IKEA", "model": "E1603"},
            "power_source": "Mains (single phase)",
            "supported": True,
        },
        {
            "ieee_address": "0xaabbccddeeff0011",
            "friendly_name": "Living Room Sensor",
            "type": "EndDevice",
            "manufacturer": "Sonoff",
            "model_id": "SNZB-02",
            "definition": {"vendor": "Sonoff", "model": "SNZB-02"},
            "power_source": "Battery",
            "supported": True,
        },
    ]


@pytest.fixture
def device_registry():
    return [
        {
            "id": "dev-z2m-1",
            "name": "Kitchen Plug",
            "name_by_user": "My Kitchen Plug",
            "manufacturer": "IKEA",
            "model": "E1603",
            "area_id": "kitchen",
            "identifiers": [["mqtt", "zigbee2mqtt_0x0011223344556677"]],
            "config_entries": ["mqtt-1"],
        },
        {
            "id": "dev-z2m-2",
            "name": "Living Room Sensor",
            "name_by_user": None,
            "manufacturer": "Sonoff",
            "model": "SNZB-02",
            "area_id": "living_room",
            "identifiers": [["mqtt", "zigbee2mqtt_0xaabbccddeeff0011"]],
            "config_entries": ["mqtt-1"],
        },
        {
            "id": "dev-other",
            "name": "Hue Light",
            "name_by_user": None,
            "manufacturer": "Philips",
            "model": "LCA001",
            "area_id": "living_room",
            "identifiers": [["hue", "12345"]],
            "config_entries": ["hue-1"],
        },
    ]


@pytest.fixture
def entity_registry():
    return [
        {
            "entity_id": "switch.kitchen_plug",
            "platform": "mqtt",
            "device_id": "dev-z2m-1",
            "unique_id": "0x0011223344556677_switch",
            "name": None,
            "device_class": None,
            "disabled_by": None,
        },
        {
            "entity_id": "sensor.kitchen_plug_power",
            "platform": "mqtt",
            "device_id": "dev-z2m-1",
            "unique_id": "0x0011223344556677_power",
            "name": None,
            "device_class": "power",
            "disabled_by": None,
        },
        {
            "entity_id": "sensor.living_room_sensor_temperature",
            "platform": "mqtt",
            "device_id": "dev-z2m-2",
            "unique_id": "0xaabbccddeeff0011_temperature",
            "name": None,
            "device_class": "temperature",
            "disabled_by": None,
        },
        {
            "entity_id": "light.hue_light",
            "platform": "hue",
            "device_id": "dev-other",
            "unique_id": "hue-12345",
            "name": None,
            "device_class": None,
            "disabled_by": None,
        },
    ]


@pytest.fixture
def area_registry():
    return [
        {"area_id": "kitchen", "name": "Kitchen"},
        {"area_id": "living_room", "name": "Living Room"},
    ]


@pytest.fixture
def states():
    return [
        {
            "entity_id": "switch.kitchen_plug",
            "state": "on",
            "attributes": {"friendly_name": "Kitchen Plug"},
        },
        {
            "entity_id": "sensor.kitchen_plug_power",
            "state": "42.5",
            "attributes": {"friendly_name": "Kitchen Plug Power", "unit_of_measurement": "W"},
        },
        {
            "entity_id": "sensor.living_room_sensor_temperature",
            "state": "21.3",
            "attributes": {"friendly_name": "Living Room Sensor Temperature"},
        },
    ]


@pytest.fixture
def automation_configs():
    return [
        {
            "id": "auto_plug",
            "alias": "Auto Plug Off",
            "action": [
                {
                    "service": "switch.turn_off",
                    "target": {"entity_id": "switch.kitchen_plug"},
                }
            ],
        }
    ]


def test_build_z2m_export_basic(
    z2m_devices, device_registry, entity_registry, area_registry, states
):
    export = build_z2m_export(
        z2m_devices=z2m_devices,
        device_registry=device_registry,
        entity_registry=entity_registry,
        area_registry=area_registry,
        states=states,
        automation_configs=[],
        ha_url="http://ha.test:8123",
    )

    assert len(export.devices) == 2
    assert export.ha_url == "http://ha.test:8123"

    plug = next(d for d in export.devices if d.friendly_name == "Kitchen Plug")
    assert plug.device_id == "dev-z2m-1"
    assert plug.area_name == "Kitchen"
    assert plug.name_by_user == "My Kitchen Plug"
    assert len(plug.entities) == 2
    assert plug.available is True

    sensor = next(d for d in export.devices if d.friendly_name == "Living Room Sensor")
    assert sensor.device_id == "dev-z2m-2"
    assert sensor.area_name == "Living Room"
    assert len(sensor.entities) == 1


def test_build_z2m_export_with_automations(
    z2m_devices, device_registry, entity_registry, area_registry, states, automation_configs
):
    export = build_z2m_export(
        z2m_devices=z2m_devices,
        device_registry=device_registry,
        entity_registry=entity_registry,
        area_registry=area_registry,
        states=states,
        automation_configs=automation_configs,
        ha_url="http://ha.test:8123",
    )

    plug = next(d for d in export.devices if d.friendly_name == "Kitchen Plug")
    assert len(plug.automations) == 1
    assert plug.automations[0].alias == "Auto Plug Off"
    assert "switch.kitchen_plug" in plug.automations[0].entity_references

    sensor = next(d for d in export.devices if d.friendly_name == "Living Room Sensor")
    assert len(sensor.automations) == 0


def test_build_z2m_export_excludes_coordinator(
    device_registry, entity_registry, area_registry, states
):
    z2m_devices = [
        {
            "ieee_address": "0x0011223344556677",
            "friendly_name": "Coordinator",
            "type": "Coordinator",
            "manufacturer": "TI",
            "model_id": "CC2652",
            "definition": {"vendor": "TI", "model": "CC2652"},
            "power_source": "Mains",
            "supported": True,
        },
    ]

    export = build_z2m_export(
        z2m_devices=z2m_devices,
        device_registry=device_registry,
        entity_registry=entity_registry,
        area_registry=area_registry,
        states=states,
        automation_configs=[],
        ha_url="http://ha.test:8123",
    )

    assert len(export.devices) == 0


async def test_run_z2m_export_integration(mocker):
    mock_ha = MagicMock()
    mock_ha.get_device_registry = AsyncMock(
        return_value=[
            {
                "id": "dev-1",
                "name": "Test Device",
                "name_by_user": None,
                "area_id": None,
                "identifiers": [["mqtt", "zigbee2mqtt_0x0011223344556677"]],
            }
        ]
    )
    mock_ha.get_entity_registry = AsyncMock(
        return_value=[
            {
                "entity_id": "switch.test",
                "platform": "mqtt",
                "device_id": "dev-1",
                "unique_id": "0x0011223344556677_switch",
                "name": None,
                "device_class": None,
                "disabled_by": None,
            }
        ]
    )
    mock_ha.get_area_registry = AsyncMock(return_value=[])
    mock_ha.get_automation_configs = AsyncMock(return_value=[])
    mock_ha.get_states = AsyncMock(
        return_value=[
            {"entity_id": "switch.test", "state": "on", "attributes": {"friendly_name": "Test"}}
        ]
    )

    mock_z2m = MagicMock()
    mock_z2m.get_devices = AsyncMock(
        return_value=[
            {
                "ieee_address": "0x0011223344556677",
                "friendly_name": "Test Device",
                "type": "Router",
                "manufacturer": "IKEA",
                "model_id": "E1603",
                "definition": {"vendor": "IKEA", "model": "E1603"},
                "power_source": "Mains",
            }
        ]
    )

    mocker.patch("zigporter.commands.export_z2m.HAClient", return_value=mock_ha)
    mocker.patch("zigporter.commands.export_z2m.Z2MClient", return_value=mock_z2m)

    export = await run_z2m_export("http://ha.test", "token", True, "http://z2m.test", "zigbee2mqtt")

    assert len(export.devices) == 1
    assert export.devices[0].friendly_name == "Test Device"
    assert len(export.devices[0].entities) == 1
