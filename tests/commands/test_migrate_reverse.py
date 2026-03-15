"""Tests for the reverse migration wizard (Z2M -> ZHA)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zigporter.commands.migrate_reverse import (
    pick_device,
    show_status,
    step_pair_with_zha,
    step_reconcile_entity_ids,
    step_remove_from_z2m,
    step_rename_and_area,
    step_validate,
)
from zigporter.migration_state import DeviceStatus, MigrationState, load_state
from zigporter.models import Z2MDevice, Z2MExport, ZHAEntity


@pytest.fixture
def sample_z2m_device() -> Z2MDevice:
    return Z2MDevice(
        device_id="dev-z2m-1",
        ieee="0x0011223344556677",
        friendly_name="Kitchen Plug",
        name_by_user="My Kitchen Plug",
        manufacturer="IKEA",
        model="E1603",
        area_id="kitchen",
        area_name="Kitchen",
        device_type="Router",
        entities=[
            ZHAEntity(
                entity_id="switch.kitchen_plug",
                name="Kitchen Plug",
                platform="mqtt",
                state="on",
                attributes={"friendly_name": "Kitchen Plug"},
            )
        ],
    )


@pytest.fixture
def sample_z2m_export(sample_z2m_device) -> Z2MExport:
    from datetime import datetime, timezone

    return Z2MExport(
        exported_at=datetime.now(tz=timezone.utc),
        ha_url="http://ha.test:8123",
        devices=[sample_z2m_device],
    )


@pytest.fixture
def mock_ha_client():
    client = MagicMock()
    client.get_zha_devices = AsyncMock(return_value=[])
    client.enable_zha_permit_join = AsyncMock(return_value=None)
    client.get_zha_device_id = AsyncMock(return_value="zha-device-id")
    client.get_device_registry = AsyncMock(
        return_value=[
            {
                "id": "zha-device-id",
                "config_entries": ["zha-entry"],
                "identifiers": [["zha", "00:11:22:33:44:55:66:77"]],
            }
        ]
    )
    client.get_states = AsyncMock(
        return_value=[
            {
                "entity_id": "switch.kitchen_plug",
                "state": "on",
                "attributes": {"friendly_name": "Kitchen Plug"},
            }
        ]
    )
    client.get_entity_registry = AsyncMock(
        return_value=[
            {
                "entity_id": "switch.kitchen_plug",
                "device_id": "zha-device-id",
                "disabled_by": None,
            }
        ]
    )
    client.rename_device_name = AsyncMock(return_value=None)
    client.update_device_area = AsyncMock(return_value=None)
    client.rename_entity_id = AsyncMock(return_value=None)
    client.delete_entity = AsyncMock(return_value=None)
    client.reload_config_entry = AsyncMock(return_value=None)
    client.get_area_registry = AsyncMock(
        return_value=[
            {"area_id": "kitchen", "name": "Kitchen"},
            {"area_id": "living_room", "name": "Living Room"},
        ]
    )
    client.get_panels = AsyncMock(return_value={})
    client.get_lovelace_config = AsyncMock(return_value=None)
    client.get_entities_for_device = AsyncMock(return_value=[{"entity_id": "switch.kitchen_plug"}])
    client.get_scripts = AsyncMock(return_value=[])
    client.get_scenes = AsyncMock(return_value=[])
    client.get_config_entries = AsyncMock(return_value=[])
    return client


@pytest.fixture
def mock_z2m_client():
    client = MagicMock()
    client.remove_device = AsyncMock(return_value=None)
    client.get_devices = AsyncMock(return_value=[])
    return client


# ---------------------------------------------------------------------------
# Step 1: Remove from Z2M
# ---------------------------------------------------------------------------


async def test_step_remove_from_z2m_confirms_removal(sample_z2m_device, mock_z2m_client):
    with patch("questionary.confirm") as mock_confirm:
        mock_confirm.return_value.unsafe_ask_async = AsyncMock(return_value=True)
        result = await step_remove_from_z2m(sample_z2m_device, mock_z2m_client)

    assert result is True
    mock_z2m_client.remove_device.assert_called_once_with(
        sample_z2m_device.friendly_name, force=True
    )


async def test_step_remove_from_z2m_aborts_on_cancel(sample_z2m_device, mock_z2m_client):
    with patch("questionary.confirm") as mock_confirm:
        mock_confirm.return_value.unsafe_ask_async = AsyncMock(return_value=False)
        result = await step_remove_from_z2m(sample_z2m_device, mock_z2m_client)

    assert result is False
    mock_z2m_client.remove_device.assert_not_called()


async def test_step_remove_from_z2m_falls_back_on_error(sample_z2m_device, mock_z2m_client):
    mock_z2m_client.remove_device = AsyncMock(side_effect=RuntimeError("MQTT unavailable"))

    with patch("questionary.confirm") as mock_confirm:
        mock_confirm.return_value.unsafe_ask_async = AsyncMock(return_value=True)
        with patch("questionary.press_any_key_to_continue") as mock_press:
            mock_press.return_value.unsafe_ask_async = AsyncMock(return_value=None)
            result = await step_remove_from_z2m(sample_z2m_device, mock_z2m_client)

    assert result is True


# ---------------------------------------------------------------------------
# Step 3: Pair with ZHA
# ---------------------------------------------------------------------------


async def test_step_pair_with_zha_device_found(sample_z2m_device, mock_ha_client):
    target_device = {
        "ieee": "00:11:22:33:44:55:66:77",
        "name": "Kitchen Plug",
        "user_given_name": "My Kitchen Plug",
        "device_type": "Router",
    }
    # First poll returns empty, second returns the device
    mock_ha_client.get_zha_devices = AsyncMock(side_effect=[[], [target_device]])

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await step_pair_with_zha(sample_z2m_device, mock_ha_client, timeout=10)

    assert result is not None
    assert result["ieee"] == "00:11:22:33:44:55:66:77"


async def test_step_pair_with_zha_timeout(sample_z2m_device, mock_ha_client):
    mock_ha_client.get_zha_devices = AsyncMock(return_value=[])

    with (
        patch("asyncio.sleep", new_callable=AsyncMock),
        patch("questionary.select") as mock_select,
    ):
        mock_select.return_value.unsafe_ask_async = AsyncMock(return_value="fail")
        result = await step_pair_with_zha(sample_z2m_device, mock_ha_client, timeout=4)

    assert result is None


async def test_step_pair_with_zha_permit_join_failure(sample_z2m_device, mock_ha_client):
    mock_ha_client.enable_zha_permit_join = AsyncMock(side_effect=RuntimeError("ZHA unavailable"))

    result = await step_pair_with_zha(sample_z2m_device, mock_ha_client, timeout=10)

    assert result is None


async def test_step_pair_with_zha_detects_unexpected_joiner(sample_z2m_device, mock_ha_client):
    unexpected = {
        "ieee": "ff:ff:ff:ff:ff:ff:ff:ff",
        "name": "Wrong Device",
        "user_given_name": None,
        "device_type": "EndDevice",
    }
    target_device = {
        "ieee": "00:11:22:33:44:55:66:77",
        "name": "Kitchen Plug",
        "user_given_name": "My Kitchen Plug",
        "device_type": "Router",
    }
    mock_ha_client.get_zha_devices = AsyncMock(
        side_effect=[
            [unexpected],
            [unexpected, target_device],
        ]
    )

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await step_pair_with_zha(sample_z2m_device, mock_ha_client, timeout=10)

    assert result is not None
    assert result["ieee"] == "00:11:22:33:44:55:66:77"


# ---------------------------------------------------------------------------
# Step 4: Rename & area
# ---------------------------------------------------------------------------


async def test_step_rename_and_area_applies_rename(sample_z2m_device, mock_ha_client):
    zha_device = {"name": "0x0011223344556677", "user_given_name": None}

    with (
        patch("asyncio.sleep", new_callable=AsyncMock),
        patch("questionary.confirm") as mock_confirm,
        patch("questionary.select") as mock_select,
    ):
        mock_confirm.return_value.unsafe_ask_async = AsyncMock(return_value=True)
        mock_select.return_value.unsafe_ask_async = AsyncMock(return_value="kitchen")
        result = await step_rename_and_area(sample_z2m_device, zha_device, mock_ha_client)

    assert result is True
    mock_ha_client.rename_device_name.assert_called_once_with("zha-device-id", "My Kitchen Plug")


async def test_step_rename_and_area_already_named(sample_z2m_device, mock_ha_client):
    zha_device = {"name": "My Kitchen Plug", "user_given_name": "My Kitchen Plug"}

    with (
        patch("asyncio.sleep", new_callable=AsyncMock),
        patch("questionary.select") as mock_select,
    ):
        mock_select.return_value.unsafe_ask_async = AsyncMock(return_value="kitchen")
        result = await step_rename_and_area(sample_z2m_device, zha_device, mock_ha_client)

    assert result is True
    mock_ha_client.rename_device_name.assert_not_called()


# ---------------------------------------------------------------------------
# Step 5: Reconcile entity IDs
# ---------------------------------------------------------------------------


async def test_step_reconcile_no_conflicts(sample_z2m_device, mock_ha_client):
    mock_ha_client.get_entity_registry = AsyncMock(
        return_value=[
            {
                "entity_id": "switch.kitchen_plug",
                "device_id": "zha-device-id",
                "disabled_by": None,
            }
        ]
    )

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await step_reconcile_entity_ids(sample_z2m_device, mock_ha_client)

    mock_ha_client.delete_entity.assert_not_called()
    mock_ha_client.rename_entity_id.assert_not_called()


async def test_step_reconcile_resolves_suffix_conflict(sample_z2m_device, mock_ha_client):
    mock_ha_client.get_entity_registry = AsyncMock(
        return_value=[
            # Stale MQTT entity occupying the original ID
            {
                "entity_id": "switch.kitchen_plug",
                "device_id": "old-mqtt-device",
                "disabled_by": None,
            },
            # New ZHA entity with _2 suffix
            {
                "entity_id": "switch.kitchen_plug_2",
                "device_id": "zha-device-id",
                "disabled_by": None,
            },
        ]
    )

    with (
        patch("asyncio.sleep", new_callable=AsyncMock),
        patch("questionary.confirm") as mock_confirm,
    ):
        mock_confirm.return_value.unsafe_ask_async = AsyncMock(return_value=True)
        await step_reconcile_entity_ids(sample_z2m_device, mock_ha_client)

    mock_ha_client.delete_entity.assert_called_once_with("switch.kitchen_plug")
    mock_ha_client.rename_entity_id.assert_called_once_with(
        "switch.kitchen_plug_2", "switch.kitchen_plug"
    )


async def test_step_reconcile_skipped_when_no_zha_device(sample_z2m_device, mock_ha_client):
    mock_ha_client.get_zha_device_id = AsyncMock(return_value=None)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await step_reconcile_entity_ids(sample_z2m_device, mock_ha_client)

    mock_ha_client.get_entity_registry.assert_not_called()


# ---------------------------------------------------------------------------
# Step 7: Validate
# ---------------------------------------------------------------------------


async def test_step_validate_all_ok(sample_z2m_device, mock_ha_client):
    mock_ha_client.get_states = AsyncMock(
        return_value=[
            {"entity_id": "switch.kitchen_plug", "state": "on"},
        ]
    )

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await step_validate(sample_z2m_device, mock_ha_client)

    assert result is True


async def test_step_validate_unknown_state_accept(sample_z2m_device, mock_ha_client):
    mock_ha_client.get_states = AsyncMock(
        return_value=[
            {"entity_id": "switch.kitchen_plug", "state": "unavailable"},
        ]
    )

    with (
        patch("asyncio.sleep", new_callable=AsyncMock),
        patch("questionary.select") as mock_select,
    ):
        mock_select.return_value.unsafe_ask_async = AsyncMock(return_value="accept")
        result = await step_validate(sample_z2m_device, mock_ha_client)

    assert result is True


async def test_step_validate_fails(sample_z2m_device, mock_ha_client):
    mock_ha_client.get_states = AsyncMock(
        return_value=[
            {"entity_id": "switch.kitchen_plug", "state": "unavailable"},
        ]
    )

    with (
        patch("asyncio.sleep", new_callable=AsyncMock),
        patch("questionary.select") as mock_select,
    ):
        mock_select.return_value.unsafe_ask_async = AsyncMock(return_value="fail")
        result = await step_validate(sample_z2m_device, mock_ha_client)

    assert result is False


async def test_step_validate_skipped_when_no_zha_device(sample_z2m_device, mock_ha_client):
    mock_ha_client.get_zha_device_id = AsyncMock(return_value=None)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await step_validate(sample_z2m_device, mock_ha_client)

    assert result is True


# ---------------------------------------------------------------------------
# Status and picker
# ---------------------------------------------------------------------------


def test_show_status(sample_z2m_export, capsys):
    from zigporter.migration_state import DeviceState

    state = MigrationState(
        zha_export="z2m-export.json",
        devices={
            "0011223344556677": DeviceState(
                ieee="0011223344556677",
                name="Kitchen Plug",
                status=DeviceStatus.PENDING,
            )
        },
    )
    show_status(sample_z2m_export, state)


def test_pick_device_all_migrated(sample_z2m_export, capsys):
    from zigporter.migration_state import DeviceState

    state = MigrationState(
        zha_export="z2m-export.json",
        devices={
            "0011223344556677": DeviceState(
                ieee="0011223344556677",
                name="Kitchen Plug",
                status=DeviceStatus.MIGRATED,
            )
        },
    )
    result = pick_device(sample_z2m_export, state)
    assert result is None


# ---------------------------------------------------------------------------
# State file helpers
# ---------------------------------------------------------------------------


def test_load_state_for_reverse(tmp_path):
    state_path = tmp_path / "reverse-state.json"
    export_path = tmp_path / "z2m-export.json"
    devices = [{"ieee": "0011223344556677", "name": "Kitchen Plug"}]

    state = load_state(state_path, export_path, devices)

    assert "0011223344556677" in state.devices
    assert state.devices["0011223344556677"].status == DeviceStatus.PENDING


def test_mark_migrated_reverse():
    from zigporter.migration_state import DeviceState, mark_migrated_reverse

    state = MigrationState(
        zha_export="z2m-export.json",
        devices={
            "0011223344556677": DeviceState(
                ieee="0011223344556677",
                name="Kitchen Plug",
                status=DeviceStatus.IN_PROGRESS,
            )
        },
    )
    mark_migrated_reverse(state, "0011223344556677", "Kitchen Plug ZHA")
    dev = state.devices["0011223344556677"]
    assert dev.status == DeviceStatus.MIGRATED
    assert dev.zha_device_name == "Kitchen Plug ZHA"
    assert dev.migrated_at is not None
