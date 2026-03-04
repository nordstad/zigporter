import pytest

import zigporter.config


HA_URL = "https://ha.test"


@pytest.fixture(autouse=True)
def reset_env_loaded():
    """Reset the _load_env sentinel before each test for isolation."""
    zigporter.config._env_loaded = False
    yield
    zigporter.config._env_loaded = False


HA_TOKEN = "test-token-abc123"


@pytest.fixture
def ha_url() -> str:
    return HA_URL


@pytest.fixture
def ha_token() -> str:
    return HA_TOKEN


@pytest.fixture
def zha_devices_payload() -> list[dict]:
    return [
        {
            "ieee": "00:11:22:33:44:55:66:77",
            "device_reg_id": "device-abc",
            "name": "Thermostat",
            "user_given_name": "Living Room Thermostat",
            "manufacturer": "Danfoss",
            "model": "eTRV0100",
            "device_type": "EndDevice",
            "quirk_applied": True,
            "quirk_class": "DanfosseTRV",
        },
        {
            "ieee": "aa:bb:cc:dd:ee:ff:00:11",
            "device_reg_id": "device-def",
            "name": "Plug",
            "user_given_name": None,
            "manufacturer": "IKEA",
            "model": "E1603",
            "device_type": "Router",
            "quirk_applied": False,
            "quirk_class": None,
        },
    ]


@pytest.fixture
def area_registry_payload() -> list[dict]:
    return [
        {"area_id": "living_room", "name": "Living Room"},
        {"area_id": "kitchen", "name": "Kitchen"},
    ]


@pytest.fixture
def device_registry_payload() -> list[dict]:
    return [
        {"id": "device-abc", "area_id": "living_room"},
        {"id": "device-def", "area_id": "kitchen"},
    ]


@pytest.fixture
def entity_registry_payload() -> list[dict]:
    return [
        {
            "entity_id": "climate.living_room_thermostat",
            "platform": "zha",
            "device_id": "device-abc",
            "unique_id": "00:11:22:33:44:55:66:77",
            "name": None,
            "device_class": None,
            "disabled_by": None,
        },
        {
            "entity_id": "switch.plug",
            "platform": "zha",
            "device_id": "device-def",
            "unique_id": "aa:bb:cc:dd:ee:ff:00:11",
            "name": "Kitchen Plug",
            "device_class": None,
            "disabled_by": None,
        },
        {
            "entity_id": "light.non_zha",
            "platform": "hue",
            "device_id": "device-other",
            "unique_id": "hue-1",
            "name": None,
            "device_class": None,
            "disabled_by": None,
        },
    ]


@pytest.fixture
def states_payload() -> list[dict]:
    return [
        {
            "entity_id": "climate.living_room_thermostat",
            "state": "heat",
            "attributes": {
                "friendly_name": "Living Room Thermostat",
                "current_temperature": 21.5,
                "temperature": 22.0,
            },
        },
        {
            "entity_id": "switch.plug",
            "state": "on",
            "attributes": {"friendly_name": "Kitchen Plug"},
        },
        {
            "entity_id": "automation.morning_heat",
            "state": "on",
            "attributes": {"friendly_name": "Morning Heat"},
        },
    ]


@pytest.fixture
def automation_configs_payload() -> list[dict]:
    return [
        {
            "id": "morning_heat",
            "alias": "Morning Heat",
            "trigger": [{"platform": "time", "at": "07:00"}],
            "action": [
                {
                    "service": "climate.set_temperature",
                    "target": {"entity_id": "climate.living_room_thermostat"},
                    "data": {"temperature": 22},
                }
            ],
        }
    ]
