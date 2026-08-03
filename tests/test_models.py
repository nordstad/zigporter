from datetime import UTC, datetime

from zigporter.models import AutomationRef, ZHADevice, ZHAEntity, ZHAExport


def test_zha_entity_minimal():
    entity = ZHAEntity(
        entity_id="switch.test",
        name="Test Switch",
        platform="zha",
    )
    assert entity.entity_id == "switch.test"
    assert entity.disabled is False
    assert entity.attributes == {}


def test_zha_entity_full():
    entity = ZHAEntity(
        entity_id="climate.thermostat",
        name="Thermostat",
        name_by_user="My Thermostat",
        platform="zha",
        unique_id="00:11:22:33:44:55:66:77",
        device_class=None,
        disabled=True,
        state="heat",
        attributes={"temperature": 22.0},
    )
    assert entity.disabled is True
    assert entity.state == "heat"
    assert entity.attributes["temperature"] == 22.0


def test_zha_device_minimal():
    device = ZHADevice(
        device_id="abc123",
        ieee="00:11:22:33:44:55:66:77",
        name="My Device",
        device_type="EndDevice",
    )
    assert device.entities == []
    assert device.automations == []
    assert device.area_name is None


def test_zha_device_with_entities():
    entity = ZHAEntity(entity_id="switch.test", name="Test Switch", platform="zha")
    device = ZHADevice(
        device_id="abc123",
        ieee="00:11:22:33:44:55:66:77",
        name="My Device",
        device_type="Router",
        entities=[entity],
    )
    assert len(device.entities) == 1
    assert device.entities[0].entity_id == "switch.test"


def test_automation_ref():
    ref = AutomationRef(
        automation_id="automation.morning_heat",
        alias="Morning Heat",
        entity_references=["climate.thermostat"],
    )
    assert ref.automation_id == "automation.morning_heat"
    assert "climate.thermostat" in ref.entity_references


def test_zha_export_serialization():
    export = ZHAExport(
        exported_at=datetime(2026, 2, 23, 10, 0, 0, tzinfo=UTC),
        ha_url="https://ha.test",
        devices=[],
    )
    data = export.model_dump_json()
    assert "ha.test" in data
    assert "2026-02-23" in data


def test_zha_export_devices_list():
    device = ZHADevice(
        device_id="abc",
        ieee="00:11:22:33:44:55:66:77",
        name="Dev",
        device_type="EndDevice",
    )
    export = ZHAExport(
        exported_at=datetime.now(tz=UTC),
        ha_url="https://ha.test",
        devices=[device],
    )
    assert len(export.devices) == 1
