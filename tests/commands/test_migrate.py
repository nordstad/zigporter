from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zigporter.commands.migrate import (
    step_reconcile_entity_ids,
    step_remove_from_zha,
    step_rename,
    step_show_test_checklist,
    step_validate,
)
from zigporter.migration_state import load_state, mark_migrated
from zigporter.models import AutomationRef, ZHADevice, ZHAEntity


@pytest.fixture
def sample_device() -> ZHADevice:
    return ZHADevice(
        device_id="device-abc",
        ieee="00:11:22:33:44:55:66:77",
        name="Kitchen Plug",
        manufacturer="IKEA",
        model="E1603",
        area_id="kitchen",
        area_name="Kitchen",
        device_type="Router",
        entities=[
            ZHAEntity(
                entity_id="switch.kitchen_plug",
                name="Kitchen Plug",
                platform="zha",
                state="on",
                attributes={"friendly_name": "Kitchen Plug"},
            )
        ],
    )


@pytest.fixture
def mock_ha_client():
    client = MagicMock()
    client.remove_zha_device = AsyncMock(return_value=None)
    client.get_device_registry = AsyncMock(return_value=[])
    client.get_states = AsyncMock(
        return_value=[
            {
                "entity_id": "switch.kitchen_plug",
                "state": "on",
                "attributes": {"friendly_name": "Kitchen Plug"},
            }
        ]
    )
    client._ws_command = AsyncMock(return_value=None)
    client.update_device_area = AsyncMock(return_value=None)
    client.get_z2m_device_id = AsyncMock(return_value="z2m-device-id")
    client.rename_entity_id = AsyncMock(return_value=None)
    client.get_entity_registry = AsyncMock(
        return_value=[
            {"entity_id": "switch.kitchen_plug", "device_id": "z2m-device-id", "disabled_by": None}
        ]
    )
    client.get_panels = AsyncMock(return_value={})
    client.get_lovelace_config = AsyncMock(return_value=None)
    return client


@pytest.fixture
def mock_z2m_client():
    client = MagicMock()
    client.rename_device = AsyncMock(return_value=None)
    client.enable_permit_join = AsyncMock(return_value=None)
    client.disable_permit_join = AsyncMock(return_value=None)
    client.get_device_by_ieee = AsyncMock(return_value=None)
    return client


async def test_step_remove_from_zha_confirms_removal(sample_device, mock_ha_client):
    # Device is gone from registry on first poll
    mock_ha_client.get_device_registry = AsyncMock(return_value=[])

    with patch("questionary.confirm") as mock_confirm:
        mock_confirm.return_value.unsafe_ask_async = AsyncMock(return_value=True)
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await step_remove_from_zha(sample_device, mock_ha_client)

    assert result is True
    mock_ha_client.remove_zha_device.assert_called_once_with(sample_device.ieee)


async def test_step_remove_from_zha_aborts_on_cancel(sample_device, mock_ha_client):
    with patch("questionary.confirm") as mock_confirm:
        mock_confirm.return_value.unsafe_ask_async = AsyncMock(return_value=False)
        result = await step_remove_from_zha(sample_device, mock_ha_client)

    assert result is False
    mock_ha_client.remove_zha_device.assert_not_called()


async def test_step_remove_from_zha_warns_if_still_present(sample_device, mock_ha_client):
    # Device still in registry after all retries
    mock_ha_client.get_device_registry = AsyncMock(return_value=[{"id": "device-abc"}])

    with patch("questionary.confirm") as mock_confirm:
        mock_confirm.return_value.unsafe_ask_async = AsyncMock(return_value=True)
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await step_remove_from_zha(sample_device, mock_ha_client)

    # Still returns True (non-blocking warning)
    assert result is True


async def test_step_remove_from_zha_falls_back_to_manual_on_error(sample_device, mock_ha_client):
    # API call fails — should fall back to manual prompt
    mock_ha_client.remove_zha_device = AsyncMock(side_effect=RuntimeError("ZHA unavailable"))
    mock_ha_client.get_device_registry = AsyncMock(return_value=[])

    with patch("questionary.confirm") as mock_confirm:
        mock_confirm.return_value.unsafe_ask_async = AsyncMock(return_value=True)
        with patch("questionary.press_any_key_to_continue") as mock_press:
            mock_press.return_value.unsafe_ask_async = AsyncMock(return_value=None)
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await step_remove_from_zha(sample_device, mock_ha_client)

    assert result is True
    mock_press.assert_called_once()


async def test_step_rename_skips_when_names_match(sample_device, mock_z2m_client, mock_ha_client):
    z2m_device = {"friendly_name": "Kitchen Plug"}

    result = await step_rename(sample_device, z2m_device, mock_z2m_client, mock_ha_client)

    assert result is True
    mock_z2m_client.rename_device.assert_not_called()


async def test_step_rename_applies_rename_on_confirm(
    sample_device, mock_z2m_client, mock_ha_client
):
    z2m_device = {"friendly_name": "0xaabbccddeeff0011"}

    with patch("questionary.confirm") as mock_confirm:
        mock_confirm.return_value.unsafe_ask_async = AsyncMock(return_value=True)
        result = await step_rename(sample_device, z2m_device, mock_z2m_client, mock_ha_client)

    assert result is True
    mock_z2m_client.rename_device.assert_called_once_with("0xaabbccddeeff0011", "Kitchen Plug")


async def test_step_rename_skips_on_cancel(sample_device, mock_z2m_client, mock_ha_client):
    z2m_device = {"friendly_name": "0xaabbccddeeff0011"}

    with patch("questionary.confirm") as mock_confirm:
        mock_confirm.return_value.unsafe_ask_async = AsyncMock(return_value=False)
        result = await step_rename(sample_device, z2m_device, mock_z2m_client, mock_ha_client)

    assert result is False
    mock_z2m_client.rename_device.assert_not_called()


async def test_step_validate_all_entities_live(sample_device, mock_ha_client):
    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await step_validate(sample_device, mock_ha_client, retries=1)

    assert result is True


async def test_step_validate_entity_unavailable(sample_device, mock_ha_client):
    mock_ha_client.get_states = AsyncMock(
        return_value=[
            {
                "entity_id": "switch.kitchen_plug",
                "state": "unavailable",
                "attributes": {},
            }
        ]
    )

    with patch("questionary.select") as mock_select:
        mock_select.return_value.unsafe_ask_async = AsyncMock(return_value="fail")
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await step_validate(sample_device, mock_ha_client, retries=1)

    assert result is False


async def test_step_validate_entity_missing(sample_device, mock_ha_client):
    mock_ha_client.get_states = AsyncMock(return_value=[])

    with patch("questionary.select") as mock_select:
        mock_select.return_value.unsafe_ask_async = AsyncMock(return_value="fail")
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await step_validate(sample_device, mock_ha_client, retries=1)

    assert result is False


async def test_step_validate_entity_missing_accept(sample_device, mock_ha_client):
    mock_ha_client.get_states = AsyncMock(return_value=[])

    with patch("questionary.select") as mock_select:
        mock_select.return_value.unsafe_ask_async = AsyncMock(return_value="accept")
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await step_validate(sample_device, mock_ha_client, retries=1)

    assert result is True


async def test_step_validate_filters_stale_ieee_entities(sample_device, mock_ha_client):
    """Stale IEEE-named entity in registry is ignored when friendly-named entities exist."""
    mock_ha_client.get_entity_registry = AsyncMock(
        return_value=[
            {"entity_id": "switch.kitchen_plug", "device_id": "z2m-device-id", "disabled_by": None},
            {
                "entity_id": "sensor.0xa4c1383a6b41b02e_linkquality",
                "device_id": "z2m-device-id",
                "disabled_by": None,
            },
        ]
    )
    mock_ha_client.get_states = AsyncMock(
        return_value=[{"entity_id": "switch.kitchen_plug", "state": "on", "attributes": {}}]
    )

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await step_validate(sample_device, mock_ha_client, retries=1)

    # Should succeed: stale IEEE entity is filtered out, friendly-named entity is "on"
    assert result is True


async def test_step_validate_skips_disabled_entities(sample_device, mock_ha_client):
    """Entities disabled by the integration (e.g. linkquality) are excluded from validation."""
    mock_ha_client.get_entity_registry = AsyncMock(
        return_value=[
            {"entity_id": "switch.kitchen_plug", "device_id": "z2m-device-id", "disabled_by": None},
            {
                "entity_id": "sensor.kitchen_plug_linkquality",
                "device_id": "z2m-device-id",
                "disabled_by": "integration",
            },
        ]
    )

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await step_validate(sample_device, mock_ha_client, retries=1)

    # Disabled linkquality entity is excluded; only the enabled switch is checked and it is "on"
    assert result is True


async def test_step_validate_unknown_state_prompts_with_context(sample_device, mock_ha_client):
    """When entities exist but have unknown state, prompt reflects it's a timing issue."""
    mock_ha_client.get_states = AsyncMock(
        return_value=[{"entity_id": "switch.kitchen_plug", "state": "unknown", "attributes": {}}]
    )

    with patch("questionary.select") as mock_select:
        mock_select.return_value.unsafe_ask_async = AsyncMock(return_value="accept")
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await step_validate(sample_device, mock_ha_client, retries=1)

    # The prompt should reflect "haven't reported state yet" context
    call_args = mock_select.call_args
    assert "reported" in call_args[0][0] or "reported" in call_args[1].get("message", "")
    assert result is True


async def test_step_validate_no_entities(mock_ha_client):
    mock_ha_client.get_entity_registry = AsyncMock(return_value=[])
    device = ZHADevice(
        device_id="dev",
        ieee="00:11:22:33:44:55:66:77",
        name="Empty Device",
        device_type="EndDevice",
        entities=[],
    )
    with patch("questionary.select") as mock_select:
        mock_select.return_value.unsafe_ask_async = AsyncMock(return_value="accept")
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await step_validate(device, mock_ha_client, retries=1)
    assert result is True


async def test_step_show_test_checklist_prints_all_types(sample_device, mock_ha_client):
    """Automations, scripts, and scenes referencing old entity IDs are shown."""
    sample_device.entities = [
        ZHAEntity(
            entity_id="switch.kitchen_plug",
            name="Kitchen Plug",
            platform="zha",
            state="on",
            attributes={},
        )
    ]
    sample_device.automations = [
        AutomationRef(
            automation_id="automation.morning",
            alias="Morning routine",
            entity_references=["switch.kitchen_plug"],
        )
    ]
    mock_ha_client.get_scripts = AsyncMock(
        return_value=[
            {
                "id": "turn_on_kitchen",
                "alias": "Turn on kitchen",
                "sequence": [{"service": "switch.turn_on", "entity_id": "switch.kitchen_plug"}],
            }
        ]
    )
    mock_ha_client.get_scenes = AsyncMock(
        return_value=[
            {
                "id": "kitchen_evening",
                "name": "Kitchen evening",
                "entities": {"switch.kitchen_plug": {"state": "on"}},
            }
        ]
    )

    # Should not raise; output is printed to console
    await step_show_test_checklist(sample_device, mock_ha_client)
    mock_ha_client.get_scripts.assert_called_once()
    mock_ha_client.get_scenes.assert_called_once()


async def test_step_show_test_checklist_silent_when_nothing_matches(sample_device, mock_ha_client):
    """No output when no automations/scripts/scenes reference the device entities."""
    sample_device.entities = [
        ZHAEntity(
            entity_id="switch.kitchen_plug",
            name="Kitchen Plug",
            platform="zha",
            state="on",
            attributes={},
        )
    ]
    sample_device.automations = []
    mock_ha_client.get_scripts = AsyncMock(return_value=[])
    mock_ha_client.get_scenes = AsyncMock(
        return_value=[
            {
                "id": "other_scene",
                "name": "Other scene",
                "entities": {"light.living_room": {"state": "on"}},
            }
        ]
    )

    # Should return without printing checklist
    await step_show_test_checklist(sample_device, mock_ha_client)


def test_show_status_renders(tmp_path):
    from zigporter.commands.migrate import show_status
    from zigporter.models import ZHAExport

    export = ZHAExport(
        exported_at=datetime.now(tz=timezone.utc),
        ha_url="https://ha.test",
        devices=[
            ZHADevice(
                device_id="abc",
                ieee="00:11:22:33:44:55:66:77",
                name="Kitchen Plug",
                device_type="Router",
            )
        ],
    )
    state = load_state(
        tmp_path / "s.json",
        tmp_path / "e.json",
        [{"ieee": "00:11:22:33:44:55:66:77", "name": "Kitchen Plug"}],
    )
    mark_migrated(state, "00:11:22:33:44:55:66:77", "Kitchen Plug")

    # Should not raise
    show_status(export, state)


# ---------------------------------------------------------------------------
# step_reconcile_entity_ids
# ---------------------------------------------------------------------------

_KONTOR_DEVICE = ZHADevice(
    device_id="device-abc",
    ieee="00:12:4b:00:2a:53:33:ab",
    name="Kontor Temp Sensor",
    device_type="EndDevice",
    entities=[
        ZHAEntity(
            entity_id="sensor.kontor_temp_sensor_temperature",
            name="Temperature",
            platform="zha",
        ),
        ZHAEntity(
            entity_id="sensor.kontor_temp_sensor_humidity",
            name="Humidity",
            platform="zha",
        ),
    ],
)


async def test_step_reconcile_renames_hex_entities(mock_ha_client):
    """Hex-named Z2M entities are matched by domain+feature to ZHA names and renamed."""
    mock_ha_client.get_entity_registry = AsyncMock(
        return_value=[
            {
                "entity_id": "sensor.0x00124b002a5333ab_temperature",
                "device_id": "z2m-device-id",
                "disabled_by": None,
            },
            {
                "entity_id": "sensor.0x00124b002a5333ab_humidity",
                "device_id": "z2m-device-id",
                "disabled_by": None,
            },
        ]
    )

    with patch("questionary.confirm") as mock_confirm:
        mock_confirm.return_value.unsafe_ask_async = AsyncMock(return_value=True)
        await step_reconcile_entity_ids(_KONTOR_DEVICE, mock_ha_client)

    assert mock_ha_client.rename_entity_id.call_count == 2
    mock_ha_client.rename_entity_id.assert_any_call(
        "sensor.0x00124b002a5333ab_temperature",
        "sensor.kontor_temp_sensor_temperature",
    )
    mock_ha_client.rename_entity_id.assert_any_call(
        "sensor.0x00124b002a5333ab_humidity",
        "sensor.kontor_temp_sensor_humidity",
    )


async def test_step_reconcile_skips_if_already_named(mock_ha_client):
    """When Z2M entities already use friendly names, no prompt is shown."""
    mock_ha_client.get_entity_registry = AsyncMock(
        return_value=[
            {
                "entity_id": "sensor.kontor_temp_sensor_temperature",
                "device_id": "z2m-device-id",
                "disabled_by": None,
            }
        ]
    )

    with patch("questionary.confirm") as mock_confirm:
        await step_reconcile_entity_ids(_KONTOR_DEVICE, mock_ha_client)
        mock_confirm.assert_not_called()

    mock_ha_client.rename_entity_id.assert_not_called()


async def test_step_reconcile_skips_on_cancel(mock_ha_client):
    """When the user declines the prompt, rename_entity_id is not called."""
    mock_ha_client.get_entity_registry = AsyncMock(
        return_value=[
            {
                "entity_id": "sensor.0x00124b002a5333ab_temperature",
                "device_id": "z2m-device-id",
                "disabled_by": None,
            }
        ]
    )

    with patch("questionary.confirm") as mock_confirm:
        mock_confirm.return_value.unsafe_ask_async = AsyncMock(return_value=False)
        await step_reconcile_entity_ids(_KONTOR_DEVICE, mock_ha_client)

    mock_ha_client.rename_entity_id.assert_not_called()


async def test_step_reconcile_no_op_when_target_equals_current(mock_ha_client):
    """When the ZHA entity_id matches the Z2M entity_id exactly, no rename is needed."""
    device_with_ieee_entity = ZHADevice(
        device_id="device-abc",
        ieee="00:12:4b:00:2a:53:33:ab",
        name="Kontor Temp Sensor",
        device_type="EndDevice",
        entities=[
            ZHAEntity(
                entity_id="sensor.0x00124b002a5333ab_temperature",
                name="Temperature",
                platform="zha",
            ),
        ],
    )
    mock_ha_client.get_entity_registry = AsyncMock(
        return_value=[
            {
                "entity_id": "sensor.0x00124b002a5333ab_temperature",
                "device_id": "z2m-device-id",
                "disabled_by": None,
            }
        ]
    )

    with patch("questionary.confirm") as mock_confirm:
        await step_reconcile_entity_ids(device_with_ieee_entity, mock_ha_client)
        mock_confirm.assert_not_called()

    mock_ha_client.rename_entity_id.assert_not_called()


async def test_step_reconcile_skips_conflicting_target(mock_ha_client):
    """A stale entity already occupying the target ID blocks the rename and shows a warning."""
    mock_ha_client.get_entity_registry = AsyncMock(
        return_value=[
            # Z2M entity — still IEEE-named
            {
                "entity_id": "sensor.0x00124b002a5333ab_temperature",
                "device_id": "z2m-device-id",
                "disabled_by": None,
            },
            # Stale entity from a different device already using the target ID
            {
                "entity_id": "sensor.kontor_temp_sensor_temperature",
                "device_id": "other-stale-device-id",
                "disabled_by": None,
            },
        ]
    )

    with patch("questionary.confirm") as mock_confirm:
        await step_reconcile_entity_ids(_KONTOR_DEVICE, mock_ha_client)
        mock_confirm.assert_not_called()  # no rename to confirm — all blocked

    mock_ha_client.rename_entity_id.assert_not_called()


async def test_step_reconcile_partial_conflict(mock_ha_client):
    """When only some targets are blocked, unblocked renames still proceed."""
    mock_ha_client.get_entity_registry = AsyncMock(
        return_value=[
            # Both Z2M entities still IEEE-named
            {
                "entity_id": "sensor.0x00124b002a5333ab_temperature",
                "device_id": "z2m-device-id",
                "disabled_by": None,
            },
            {
                "entity_id": "sensor.0x00124b002a5333ab_humidity",
                "device_id": "z2m-device-id",
                "disabled_by": None,
            },
            # Stale entity blocks only the temperature rename
            {
                "entity_id": "sensor.kontor_temp_sensor_temperature",
                "device_id": "other-stale-device-id",
                "disabled_by": None,
            },
        ]
    )

    with patch("questionary.confirm") as mock_confirm:
        mock_confirm.return_value.unsafe_ask_async = AsyncMock(return_value=True)
        await step_reconcile_entity_ids(_KONTOR_DEVICE, mock_ha_client)

    # Only the humidity rename (unblocked) should go through
    mock_ha_client.rename_entity_id.assert_called_once_with(
        "sensor.0x00124b002a5333ab_humidity",
        "sensor.kontor_temp_sensor_humidity",
    )


async def test_step_reconcile_skips_when_ha_already_renamed(mock_ha_client):
    """When HA auto-renames entities between the first fetch and applying, skip them gracefully."""
    mock_ha_client.get_entity_registry = AsyncMock(
        side_effect=[
            # First call: entity still has IEEE-hex name → rename proposal is built
            [
                {
                    "entity_id": "sensor.0x00124b002a5333ab_temperature",
                    "device_id": "z2m-device-id",
                    "disabled_by": None,
                }
            ],
            # Second call (re-fetch before applying): HA already renamed it
            [
                {
                    "entity_id": "sensor.kontor_temp_sensor_temperature",
                    "device_id": "z2m-device-id",
                    "disabled_by": None,
                }
            ],
        ]
    )

    with patch("questionary.confirm") as mock_confirm:
        mock_confirm.return_value.unsafe_ask_async = AsyncMock(return_value=True)
        await step_reconcile_entity_ids(_KONTOR_DEVICE, mock_ha_client)

    mock_ha_client.rename_entity_id.assert_not_called()


async def test_step_reconcile_no_zha_match(mock_ha_client):
    """A hex-named entity with no matching ZHA feature is silently skipped."""
    mock_ha_client.get_entity_registry = AsyncMock(
        return_value=[
            {
                # feature "linkquality" has no ZHA counterpart in _KONTOR_DEVICE
                "entity_id": "sensor.0x00124b002a5333ab_linkquality",
                "device_id": "z2m-device-id",
                "disabled_by": None,
            }
        ]
    )

    with patch("questionary.confirm") as mock_confirm:
        await step_reconcile_entity_ids(_KONTOR_DEVICE, mock_ha_client)
        mock_confirm.assert_not_called()

    mock_ha_client.rename_entity_id.assert_not_called()


# ---------------------------------------------------------------------------
# step_show_inspect_summary
# ---------------------------------------------------------------------------


async def test_step_show_inspect_summary_skips_when_no_z2m_device(sample_device, mock_ha_client):
    """When Z2M device is not found in HA, entity registry is never queried."""
    from zigporter.commands.migrate import step_show_inspect_summary  # noqa: PLC0415

    mock_ha_client.get_z2m_device_id = AsyncMock(return_value=None)

    await step_show_inspect_summary(sample_device, mock_ha_client)

    mock_ha_client.get_entity_registry.assert_not_called()


async def test_step_show_inspect_summary_skips_when_no_entities(sample_device, mock_ha_client):
    """When Z2M device has no enabled entities, dashboard fetch is skipped."""
    from zigporter.commands.migrate import step_show_inspect_summary  # noqa: PLC0415

    mock_ha_client.get_entity_registry = AsyncMock(return_value=[])

    await step_show_inspect_summary(sample_device, mock_ha_client)

    mock_ha_client.get_panels.assert_not_called()


async def test_step_show_inspect_summary_normal(sample_device, mock_ha_client):
    """Normal path: fetches entity registry and dashboard data then shows the summary."""
    from zigporter.commands.migrate import step_show_inspect_summary  # noqa: PLC0415

    mock_ha_client.get_lovelace_config = AsyncMock(
        return_value={
            "views": [
                {
                    "title": "Home",
                    "cards": [{"type": "entities", "entities": ["switch.kitchen_plug"]}],
                }
            ]
        }
    )

    await step_show_inspect_summary(sample_device, mock_ha_client)

    mock_ha_client.get_z2m_device_id.assert_called_once_with(sample_device.ieee)
    mock_ha_client.get_entity_registry.assert_called()
    mock_ha_client.get_panels.assert_called_once()


async def test_step_show_inspect_summary_swallows_exceptions(sample_device, mock_ha_client):
    """Exceptions during summary fetching do not propagate — the wizard must not be interrupted."""
    from zigporter.commands.migrate import step_show_inspect_summary  # noqa: PLC0415

    mock_ha_client.get_z2m_device_id = AsyncMock(side_effect=RuntimeError("network error"))

    # Must not raise
    await step_show_inspect_summary(sample_device, mock_ha_client)
