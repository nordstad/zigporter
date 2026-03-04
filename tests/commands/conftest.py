"""Shared test fixtures for the commands test suite."""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_ha_snapshot_client():
    """A fully wired MagicMock for HAClient, suitable for snapshot-based rename tests."""
    client = MagicMock()
    client.get_entity_registry = AsyncMock(
        return_value=[
            {"entity_id": "light.kitchen_plug", "device_id": "dev1"},
            {"entity_id": "sensor.kitchen_plug_power", "device_id": "dev1"},
        ]
    )
    client.get_automation_configs = AsyncMock(
        return_value=[
            {"id": "a1", "alias": "Auto", "action": [{"entity_id": "light.kitchen_plug"}]}
        ]
    )
    client.get_scripts = AsyncMock(return_value=[])
    client.get_scenes = AsyncMock(return_value=[])
    client.get_panels = AsyncMock(return_value={})
    client.get_lovelace_config = AsyncMock(return_value=None)
    client.get_config_entries = AsyncMock(return_value=[])
    return client
