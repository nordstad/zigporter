from zigporter.commands.export import (
    _build_area_map,
    _build_entity_map,
    _build_state_map,
    _extract_entity_ids_from_automation,
    _match_automations_to_devices,
    build_export,
)


def test_build_area_map(area_registry_payload):
    result = _build_area_map(area_registry_payload)
    assert result == {"living_room": "Living Room", "kitchen": "Kitchen"}


def test_build_entity_map_filters_zha_only(entity_registry_payload):
    result = _build_entity_map(entity_registry_payload)
    # hue entity should be excluded
    assert "device-other" not in result
    assert "device-abc" in result
    assert "device-def" in result
    assert result["device-abc"][0]["entity_id"] == "climate.living_room_thermostat"


def test_build_state_map(states_payload):
    result = _build_state_map(states_payload)
    assert "climate.living_room_thermostat" in result
    assert result["climate.living_room_thermostat"]["state"] == "heat"


def test_extract_entity_ids_flat():
    config = {
        "action": [
            {
                "service": "climate.set_temperature",
                "target": {"entity_id": "climate.thermostat"},
            }
        ]
    }
    result = _extract_entity_ids_from_automation(config)
    assert "climate.thermostat" in result


def test_extract_entity_ids_list():
    config = {
        "action": [
            {
                "service": "homeassistant.turn_on",
                "entity_id": ["light.a", "light.b"],
            }
        ]
    }
    result = _extract_entity_ids_from_automation(config)
    assert "light.a" in result
    assert "light.b" in result


def test_extract_entity_ids_no_references():
    config = {"trigger": [{"platform": "time", "at": "07:00"}]}
    result = _extract_entity_ids_from_automation(config)
    assert result == []


def test_match_automations_to_devices(automation_configs_payload):
    entity_to_device = {"climate.living_room_thermostat": "device-abc"}
    result = _match_automations_to_devices(automation_configs_payload, entity_to_device)

    assert "device-abc" in result
    assert len(result["device-abc"]) == 1
    assert result["device-abc"][0].alias == "Morning Heat"
    assert "climate.living_room_thermostat" in result["device-abc"][0].entity_references


def test_match_automations_no_zha_entities(automation_configs_payload):
    # No ZHA entity references in device map
    result = _match_automations_to_devices(automation_configs_payload, {})
    assert result == {}


def test_build_export_device_count(
    zha_devices_payload,
    device_registry_payload,
    entity_registry_payload,
    area_registry_payload,
    states_payload,
    automation_configs_payload,
):
    export = build_export(
        zha_devices=zha_devices_payload,
        device_registry=device_registry_payload,
        entity_registry=entity_registry_payload,
        area_registry=area_registry_payload,
        states=states_payload,
        automation_configs=automation_configs_payload,
        ha_url="https://ha.test",
    )

    assert len(export.devices) == 2
    assert export.ha_url == "https://ha.test"


def test_build_export_device_names(
    zha_devices_payload,
    device_registry_payload,
    entity_registry_payload,
    area_registry_payload,
    states_payload,
    automation_configs_payload,
):
    export = build_export(
        zha_devices=zha_devices_payload,
        device_registry=device_registry_payload,
        entity_registry=entity_registry_payload,
        area_registry=area_registry_payload,
        states=states_payload,
        automation_configs=automation_configs_payload,
        ha_url="https://ha.test",
    )

    thermostat = next(d for d in export.devices if d.ieee == "00:11:22:33:44:55:66:77")
    assert thermostat.name == "Living Room Thermostat"
    assert thermostat.area_name == "Living Room"
    assert thermostat.manufacturer == "Danfoss"
    assert thermostat.quirk_applied is True


def test_build_export_entity_assigned_to_device(
    zha_devices_payload,
    device_registry_payload,
    entity_registry_payload,
    area_registry_payload,
    states_payload,
    automation_configs_payload,
):
    export = build_export(
        zha_devices=zha_devices_payload,
        device_registry=device_registry_payload,
        entity_registry=entity_registry_payload,
        area_registry=area_registry_payload,
        states=states_payload,
        automation_configs=automation_configs_payload,
        ha_url="https://ha.test",
    )

    thermostat = next(d for d in export.devices if d.ieee == "00:11:22:33:44:55:66:77")
    assert len(thermostat.entities) == 1
    assert thermostat.entities[0].entity_id == "climate.living_room_thermostat"
    assert thermostat.entities[0].state == "heat"


def test_build_export_automation_linked(
    zha_devices_payload,
    device_registry_payload,
    entity_registry_payload,
    area_registry_payload,
    states_payload,
    automation_configs_payload,
):
    export = build_export(
        zha_devices=zha_devices_payload,
        device_registry=device_registry_payload,
        entity_registry=entity_registry_payload,
        area_registry=area_registry_payload,
        states=states_payload,
        automation_configs=automation_configs_payload,
        ha_url="https://ha.test",
    )

    thermostat = next(d for d in export.devices if d.ieee == "00:11:22:33:44:55:66:77")
    assert len(thermostat.automations) == 1
    assert thermostat.automations[0].alias == "Morning Heat"


def test_build_export_empty_automations(
    zha_devices_payload,
    device_registry_payload,
    entity_registry_payload,
    area_registry_payload,
    states_payload,
):
    export = build_export(
        zha_devices=zha_devices_payload,
        device_registry=device_registry_payload,
        entity_registry=entity_registry_payload,
        area_registry=area_registry_payload,
        states=states_payload,
        automation_configs=[],
        ha_url="https://ha.test",
    )

    for device in export.devices:
        assert device.automations == []


def test_build_export_available_false_when_all_entities_offline(
    zha_devices_payload,
    device_registry_payload,
    entity_registry_payload,
    area_registry_payload,
    automation_configs_payload,
):
    # Make all entity states unavailable / unknown
    states = [
        {
            "entity_id": "climate.living_room_thermostat",
            "state": "unavailable",
            "attributes": {"friendly_name": "Living Room Thermostat"},
        },
        {
            "entity_id": "switch.plug",
            "state": "unknown",
            "attributes": {"friendly_name": "Kitchen Plug"},
        },
    ]
    export = build_export(
        zha_devices=zha_devices_payload,
        device_registry=device_registry_payload,
        entity_registry=entity_registry_payload,
        area_registry=area_registry_payload,
        states=states,
        automation_configs=automation_configs_payload,
        ha_url="https://ha.test",
    )

    for device in export.devices:
        assert device.available is False


def test_build_export_available_true_when_any_entity_online(
    zha_devices_payload,
    device_registry_payload,
    entity_registry_payload,
    area_registry_payload,
    automation_configs_payload,
):
    # Keep default states — thermostat is "heat" (online), plug is "on"
    export = build_export(
        zha_devices=zha_devices_payload,
        device_registry=device_registry_payload,
        entity_registry=entity_registry_payload,
        area_registry=area_registry_payload,
        states=[
            {
                "entity_id": "climate.living_room_thermostat",
                "state": "heat",
                "attributes": {"friendly_name": "Living Room Thermostat"},
            },
            {
                "entity_id": "switch.plug",
                "state": "on",
                "attributes": {"friendly_name": "Kitchen Plug"},
            },
        ],
        automation_configs=automation_configs_payload,
        ha_url="https://ha.test",
    )

    for device in export.devices:
        assert device.available is True


def test_build_export_available_none_when_no_enabled_entities(
    device_registry_payload,
    area_registry_payload,
    automation_configs_payload,
):
    # ZHA device with all entities disabled
    zha_devices = [
        {
            "ieee": "ff:ee:dd:cc:bb:aa:99:88",
            "device_reg_id": "device-disabled",
            "name": "Disabled Device",
            "user_given_name": None,
            "manufacturer": "IKEA",
            "model": "E1743",
            "device_type": "EndDevice",
            "quirk_applied": False,
            "quirk_class": None,
        }
    ]
    device_registry = device_registry_payload + [{"id": "device-disabled", "area_id": None}]
    entity_registry = [
        {
            "entity_id": "light.disabled",
            "platform": "zha",
            "device_id": "device-disabled",
            "unique_id": "ff:ee:dd:cc:bb:aa:99:88",
            "name": None,
            "device_class": None,
            "disabled_by": "user",
        }
    ]
    states = [
        {
            "entity_id": "light.disabled",
            "state": "unavailable",
            "attributes": {"friendly_name": "Disabled Light"},
        }
    ]
    export = build_export(
        zha_devices=zha_devices,
        device_registry=device_registry,
        entity_registry=entity_registry,
        area_registry=area_registry_payload,
        states=states,
        automation_configs=[],
        ha_url="https://ha.test",
    )

    disabled_device = next(d for d in export.devices if d.ieee == "ff:ee:dd:cc:bb:aa:99:88")
    assert disabled_device.available is None
