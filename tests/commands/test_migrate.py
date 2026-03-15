from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zigporter.commands.migrate import (
    step_pair_with_z2m,
    step_post_migrate_rename,
    step_reconcile_entity_ids,
    step_remove_from_zha,
    step_rename,
    step_validate,
)
from zigporter.commands.migrate_reporting import (
    show_device_dependencies,
    step_show_inspect_summary,
    step_show_test_checklist,
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
    client.get_device_registry = AsyncMock(
        return_value=[{"id": "z2m-device-id", "config_entries": ["entry-abc"], "identifiers": []}]
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
    client._ws_command = AsyncMock(return_value=None)
    client.update_device_area = AsyncMock(return_value=None)
    client.get_z2m_device_id = AsyncMock(return_value="z2m-device-id")
    client.rename_entity_id = AsyncMock(return_value=None)
    client.delete_entity = AsyncMock(return_value=None)
    client.reload_config_entry = AsyncMock(return_value=None)
    client.get_entity_registry = AsyncMock(
        return_value=[
            {"entity_id": "switch.kitchen_plug", "device_id": "z2m-device-id", "disabled_by": None}
        ]
    )
    client.get_panels = AsyncMock(return_value={})
    client.get_lovelace_config = AsyncMock(return_value=None)
    client.save_lovelace_config = AsyncMock(return_value=None)
    client.update_automation = AsyncMock(return_value=None)
    client.update_script = AsyncMock(return_value=None)
    client.update_scene = AsyncMock(return_value=None)
    client.get_entities_for_device = AsyncMock(return_value=[{"entity_id": "switch.kitchen_plug"}])
    client.rename_device_name = AsyncMock(return_value=None)
    client.get_automations = AsyncMock(return_value=[])
    client.get_scripts = AsyncMock(return_value=[])
    client.get_scenes = AsyncMock(return_value=[])
    client.get_config_entries = AsyncMock(return_value=[])
    client.get_z2m_config_entry_id = AsyncMock(return_value=None)
    client.get_area_registry = AsyncMock(
        return_value=[
            {"area_id": "kitchen", "name": "Kitchen"},
            {"area_id": "living_room", "name": "Living Room"},
        ]
    )
    client.enable_zha_permit_join = AsyncMock(return_value=None)
    client.get_zha_device_id = AsyncMock(return_value=None)
    return client


@pytest.fixture
def mock_z2m_client():
    client = MagicMock()
    client.rename_device = AsyncMock(return_value=None)
    client.enable_permit_join = AsyncMock(return_value=None)
    client.disable_permit_join = AsyncMock(return_value=None)
    client.get_device_by_ieee = AsyncMock(return_value=None)
    client.get_devices = AsyncMock(return_value=[])
    client.wait_for_interview = AsyncMock(return_value=("successful", {}))
    client.remove_device = AsyncMock(return_value=None)
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

    with patch("zigporter.commands.migrate._step_assign_area", new_callable=AsyncMock):
        result = await step_rename(sample_device, z2m_device, mock_z2m_client, mock_ha_client)

    assert result is True
    mock_z2m_client.rename_device.assert_not_called()


async def test_step_rename_applies_rename_on_confirm(
    sample_device, mock_z2m_client, mock_ha_client
):
    z2m_device = {"friendly_name": "0xaabbccddeeff0011"}

    with (
        patch("questionary.confirm") as mock_confirm,
        patch("zigporter.commands.migrate._step_assign_area", new_callable=AsyncMock),
    ):
        mock_confirm.return_value.unsafe_ask_async = AsyncMock(return_value=True)
        result = await step_rename(sample_device, z2m_device, mock_z2m_client, mock_ha_client)

    assert result is True
    mock_z2m_client.rename_device.assert_called_once_with("0xaabbccddeeff0011", "Kitchen Plug")


async def test_step_rename_skips_on_cancel(sample_device, mock_z2m_client, mock_ha_client):
    z2m_device = {"friendly_name": "0xaabbccddeeff0011"}

    with (
        patch("questionary.confirm") as mock_confirm,
        patch("zigporter.commands.migrate._step_assign_area", new_callable=AsyncMock),
    ):
        mock_confirm.return_value.unsafe_ask_async = AsyncMock(return_value=False)
        result = await step_rename(sample_device, z2m_device, mock_z2m_client, mock_ha_client)

    assert result is False
    mock_z2m_client.rename_device.assert_not_called()


# ---------------------------------------------------------------------------
# _step_assign_area / _prompt_area
# ---------------------------------------------------------------------------


async def test_step_assign_area_keeps_original_area(sample_device, mock_ha_client):
    """When the user picks the original area, update_device_area is called with that area."""
    from zigporter.commands.migrate import _step_assign_area  # noqa: PLC0415

    with patch("questionary.select") as mock_select:
        mock_select.return_value.unsafe_ask_async = AsyncMock(return_value="kitchen")
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await _step_assign_area(sample_device, mock_ha_client)

    mock_ha_client.update_device_area.assert_called_once_with("z2m-device-id", "kitchen")


async def test_step_assign_area_changes_area(sample_device, mock_ha_client):
    """When the user selects a different area, update_device_area is called with the new area."""
    from zigporter.commands.migrate import _step_assign_area  # noqa: PLC0415

    with patch("questionary.select") as mock_select:
        mock_select.return_value.unsafe_ask_async = AsyncMock(return_value="living_room")
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await _step_assign_area(sample_device, mock_ha_client)

    mock_ha_client.update_device_area.assert_called_once_with("z2m-device-id", "living_room")


async def test_step_assign_area_no_area_device_assigns_area(mock_ha_client):
    """Device with no ZHA area: user can pick an area and it is applied."""
    from zigporter.commands.migrate import _step_assign_area  # noqa: PLC0415

    device_no_area = ZHADevice(
        device_id="dev-x",
        ieee="00:11:22:33:44:55:66:88",
        name="Hallway Motion",
        device_type="EndDevice",
        entities=[],
    )

    with patch("questionary.select") as mock_select:
        mock_select.return_value.unsafe_ask_async = AsyncMock(return_value="living_room")
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await _step_assign_area(device_no_area, mock_ha_client)

    mock_ha_client.update_device_area.assert_called_once_with("z2m-device-id", "living_room")


async def test_step_assign_area_clears_area(sample_device, mock_ha_client):
    """When the user selects 'No area', update_device_area is called with an empty string."""
    from zigporter.commands.migrate import _step_assign_area  # noqa: PLC0415

    with patch("questionary.select") as mock_select:
        mock_select.return_value.unsafe_ask_async = AsyncMock(return_value="__no_area__")
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await _step_assign_area(sample_device, mock_ha_client)

    mock_ha_client.update_device_area.assert_called_once_with("z2m-device-id", "")


async def test_step_assign_area_registry_fetch_fails_restores_original(
    sample_device, mock_ha_client
):
    """When get_area_registry raises, the original ZHA area is restored silently."""
    from zigporter.commands.migrate import _step_assign_area  # noqa: PLC0415

    mock_ha_client.get_area_registry = AsyncMock(side_effect=RuntimeError("WS error"))

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await _step_assign_area(sample_device, mock_ha_client)

    mock_ha_client.update_device_area.assert_called_once_with("z2m-device-id", "kitchen")


async def test_step_assign_area_skips_when_no_z2m_device(sample_device, mock_ha_client):
    """When Z2M device is not yet in HA, area assignment is skipped gracefully."""
    from zigporter.commands.migrate import _step_assign_area  # noqa: PLC0415

    mock_ha_client.get_z2m_device_id = AsyncMock(return_value=None)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await _step_assign_area(sample_device, mock_ha_client)

    mock_ha_client.get_area_registry.assert_not_called()
    mock_ha_client.update_device_area.assert_not_called()


async def test_prompt_area_returns_none_for_no_area():
    """Selecting '── No area ──' returns None."""
    from zigporter.commands.migrate import _prompt_area  # noqa: PLC0415

    device = ZHADevice(
        device_id="d",
        ieee="00:11:22:33:44:55:66:77",
        name="Lamp",
        device_type="EndDevice",
        entities=[],
    )
    areas = [{"area_id": "bedroom", "name": "Bedroom"}]

    with patch("questionary.select") as mock_select:
        mock_select.return_value.unsafe_ask_async = AsyncMock(return_value="__no_area__")
        result = await _prompt_area(device, areas)

    assert result is None


async def test_prompt_area_returns_selected_area_id():
    """Selecting a named area returns its area_id."""
    from zigporter.commands.migrate import _prompt_area  # noqa: PLC0415

    device = ZHADevice(
        device_id="d",
        ieee="00:11:22:33:44:55:66:77",
        name="Lamp",
        device_type="EndDevice",
        entities=[],
    )
    areas = [{"area_id": "bedroom", "name": "Bedroom"}]

    with patch("questionary.select") as mock_select:
        mock_select.return_value.unsafe_ask_async = AsyncMock(return_value="bedroom")
        result = await _prompt_area(device, areas)

    assert result == "bedroom"


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


async def test_step_validate_reload_triggers_config_entry_reload(sample_device, mock_ha_client):
    """Selecting 'reload' reloads Z2M config entries then polls again until entities are online."""
    mock_ha_client.get_states = AsyncMock(
        side_effect=[
            # First poll: entity has unknown state → prompt appears
            [{"entity_id": "switch.kitchen_plug", "state": "unknown", "attributes": {}}],
            # After reload, second poll: entity is online
            [{"entity_id": "switch.kitchen_plug", "state": "on", "attributes": {}}],
        ]
    )

    with patch("questionary.select") as mock_select:
        mock_select.return_value.unsafe_ask_async = AsyncMock(return_value="reload")
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await step_validate(sample_device, mock_ha_client, retries=1)

    mock_ha_client.reload_config_entry.assert_called_once_with("entry-abc")
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
    await step_show_test_checklist(sample_device, mock_ha_client, MagicMock())
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
    await step_show_test_checklist(sample_device, mock_ha_client, MagicMock())


async def test_step_show_test_checklist_ha_unreachable(sample_device, mock_ha_client):
    """When HA is unreachable the checklist step returns silently without raising."""
    sample_device.automations = []
    mock_ha_client.get_scripts = AsyncMock(side_effect=OSError("connection refused"))
    mock_ha_client.get_scenes = AsyncMock(return_value=[])

    await step_show_test_checklist(sample_device, mock_ha_client, MagicMock())


# ---------------------------------------------------------------------------
# show_device_dependencies
# ---------------------------------------------------------------------------


async def test_show_device_dependencies_renders_all_types(sample_device, mock_ha_client):
    """Automations, scripts, and scenes referencing the device are all printed."""
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
    mock_console = MagicMock()

    await show_device_dependencies(sample_device, mock_ha_client, mock_console)

    mock_ha_client.get_scripts.assert_called_once()
    mock_ha_client.get_scenes.assert_called_once()
    mock_console.rule.assert_called()


async def test_show_device_dependencies_silent_when_nothing_matches(sample_device, mock_ha_client):
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
    mock_ha_client.get_scenes = AsyncMock(return_value=[])
    mock_console = MagicMock()

    await show_device_dependencies(sample_device, mock_ha_client, mock_console)

    mock_console.rule.assert_not_called()


async def test_show_device_dependencies_ha_unreachable(sample_device, mock_ha_client):
    """HA being unreachable is silently swallowed — dependency preview is optional."""
    sample_device.automations = []
    mock_ha_client.get_scripts = AsyncMock(side_effect=OSError("connection refused"))
    mock_ha_client.get_scenes = AsyncMock(return_value=[])
    mock_console = MagicMock()

    await show_device_dependencies(sample_device, mock_ha_client, mock_console)

    mock_console.rule.assert_not_called()


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


async def test_step_reconcile_resolves_suffix_conflicts(mock_ha_client):
    """When Z2M entity has _2 suffix and a stale ZHA entity holds the base name, fix it."""
    mock_ha_client.get_entity_registry = AsyncMock(
        return_value=[
            {
                "entity_id": "sensor.kontor_temp_sensor_temperature_2",
                "device_id": "z2m-device-id",
                "disabled_by": None,
            },
            {
                "entity_id": "sensor.kontor_temp_sensor_temperature",
                "device_id": "stale-zha-device-id",
                "disabled_by": None,
            },
        ]
    )

    with patch("questionary.confirm") as mock_confirm:
        mock_confirm.return_value.unsafe_ask_async = AsyncMock(return_value=True)
        await step_reconcile_entity_ids(_KONTOR_DEVICE, mock_ha_client)

    mock_ha_client.delete_entity.assert_called_once_with("sensor.kontor_temp_sensor_temperature")
    mock_ha_client.rename_entity_id.assert_called_once_with(
        "sensor.kontor_temp_sensor_temperature_2",
        "sensor.kontor_temp_sensor_temperature",
    )


async def test_step_reconcile_suffix_conflict_skips_on_cancel(mock_ha_client):
    """When user declines suffix conflict resolution, no entities are deleted or renamed."""
    mock_ha_client.get_entity_registry = AsyncMock(
        return_value=[
            {
                "entity_id": "sensor.kontor_temp_sensor_temperature_2",
                "device_id": "z2m-device-id",
                "disabled_by": None,
            },
            {
                "entity_id": "sensor.kontor_temp_sensor_temperature",
                "device_id": "stale-zha-device-id",
                "disabled_by": None,
            },
        ]
    )

    with patch("questionary.confirm") as mock_confirm:
        mock_confirm.return_value.unsafe_ask_async = AsyncMock(return_value=False)
        await step_reconcile_entity_ids(_KONTOR_DEVICE, mock_ha_client)

    mock_ha_client.delete_entity.assert_not_called()
    mock_ha_client.rename_entity_id.assert_not_called()


# ---------------------------------------------------------------------------
# step_show_inspect_summary
# ---------------------------------------------------------------------------


async def test_step_show_inspect_summary_skips_when_no_z2m_device(sample_device, mock_ha_client):
    """When Z2M device is not found in HA, entity registry is never queried."""
    mock_ha_client.get_z2m_device_id = AsyncMock(return_value=None)

    await step_show_inspect_summary(sample_device, mock_ha_client, MagicMock())

    mock_ha_client.get_entity_registry.assert_not_called()


async def test_step_show_inspect_summary_skips_when_no_entities(sample_device, mock_ha_client):
    """When Z2M device has no enabled entities, dashboard fetch is skipped."""
    mock_ha_client.get_entity_registry = AsyncMock(return_value=[])

    await step_show_inspect_summary(sample_device, mock_ha_client, MagicMock())

    mock_ha_client.get_panels.assert_not_called()


async def test_step_show_inspect_summary_normal(sample_device, mock_ha_client):
    """Normal path: fetches entity registry and dashboard data then shows the summary."""
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

    await step_show_inspect_summary(sample_device, mock_ha_client, MagicMock())

    mock_ha_client.get_z2m_device_id.assert_called_once_with(sample_device.ieee)
    mock_ha_client.get_entity_registry.assert_called()
    mock_ha_client.get_panels.assert_called_once()


async def test_step_show_inspect_summary_swallows_exceptions(sample_device, mock_ha_client):
    """Exceptions during summary fetching do not propagate — the wizard must not be interrupted."""
    mock_ha_client.get_z2m_device_id = AsyncMock(side_effect=RuntimeError("network error"))

    # Must not raise
    await step_show_inspect_summary(sample_device, mock_ha_client, MagicMock())


# ---------------------------------------------------------------------------
# step_post_migrate_rename
# ---------------------------------------------------------------------------


async def test_step_post_migrate_rename_declines(sample_device, mock_ha_client, mock_z2m_client):
    """When the user declines the rename prompt, no HA calls are made."""
    with patch("questionary.confirm") as mock_confirm:
        mock_confirm.return_value.unsafe_ask_async = AsyncMock(return_value=False)
        await step_post_migrate_rename(sample_device, mock_ha_client, mock_z2m_client)

    mock_ha_client.get_entities_for_device.assert_not_called()
    mock_ha_client.rename_entity_id.assert_not_called()


async def test_step_post_migrate_rename_no_entities(sample_device, mock_ha_client, mock_z2m_client):
    """When no entities are found for the device, the step prints a note and returns."""
    mock_ha_client.get_entities_for_device = AsyncMock(return_value=[])

    with patch("questionary.confirm") as mock_confirm:
        mock_confirm.return_value.unsafe_ask_async = AsyncMock(return_value=True)
        with patch("questionary.text") as mock_text:
            mock_text.return_value.unsafe_ask_async = AsyncMock(return_value="Smart Plug Garage")
            await step_post_migrate_rename(sample_device, mock_ha_client, mock_z2m_client)

    mock_ha_client.rename_entity_id.assert_not_called()


async def test_step_post_migrate_rename_same_name(sample_device, mock_ha_client, mock_z2m_client):
    """When the user enters the same name, the step skips."""
    with patch("questionary.confirm") as mock_confirm:
        mock_confirm.return_value.unsafe_ask_async = AsyncMock(return_value=True)
        with patch("questionary.text") as mock_text:
            mock_text.return_value.unsafe_ask_async = AsyncMock(return_value=sample_device.name)
            await step_post_migrate_rename(sample_device, mock_ha_client, mock_z2m_client)

    mock_ha_client.get_entities_for_device.assert_not_called()


async def test_step_post_migrate_rename_executes(sample_device, mock_ha_client, mock_z2m_client):
    """When the user accepts, execute_device_rename is called with the correct args."""
    mock_ha_client.get_entity_registry = AsyncMock(
        return_value=[
            {"entity_id": "switch.kitchen_plug", "device_id": "z2m-device-id", "disabled_by": None},
            {
                "entity_id": "switch.smart_plug_garage",
                "device_id": "z2m-device-id",
                "disabled_by": None,
            },
        ]
    )

    with (
        patch("questionary.confirm") as mock_confirm,
        patch("questionary.text") as mock_text,
        patch(
            "zigporter.commands.migrate.execute_device_rename", new_callable=AsyncMock
        ) as mock_exec,
        patch(
            "zigporter.commands.migrate.build_device_rename_plan", new_callable=AsyncMock
        ) as mock_plan,
    ):
        from zigporter.commands.rename_device import DeviceRenamePlan  # noqa: PLC0415

        mock_plan.return_value = DeviceRenamePlan(
            device_id="z2m-device-id",
            old_device_name="Kitchen Plug",
            new_device_name="Smart Plug Garage",
            plans=[],
        )
        # First confirm = "yes to rename", second confirm = "apply changes"
        mock_confirm.return_value.unsafe_ask_async = AsyncMock(side_effect=[True, True])
        mock_text.return_value.unsafe_ask_async = AsyncMock(return_value="Smart Plug Garage")

        await step_post_migrate_rename(sample_device, mock_ha_client, mock_z2m_client)

    mock_exec.assert_called_once()
    call_kwargs = mock_exec.call_args[1]
    assert call_kwargs["z2m_client"] is mock_z2m_client
    assert call_kwargs["z2m_friendly_name"] == sample_device.name


async def test_step_post_migrate_rename_no_z2m_device(
    sample_device, mock_ha_client, mock_z2m_client
):
    """When Z2M device is not found in HA registry, the step prints a warning and returns."""
    mock_ha_client.get_z2m_device_id = AsyncMock(return_value=None)

    with patch("questionary.confirm") as mock_confirm:
        mock_confirm.return_value.unsafe_ask_async = AsyncMock(return_value=True)
        with patch("questionary.text") as mock_text:
            mock_text.return_value.unsafe_ask_async = AsyncMock(return_value="Smart Plug Garage")
            await step_post_migrate_rename(sample_device, mock_ha_client, mock_z2m_client)

    mock_ha_client.get_entities_for_device.assert_not_called()


# ---------------------------------------------------------------------------
# step_pair_with_z2m
# ---------------------------------------------------------------------------


@pytest.fixture
def pair_device() -> ZHADevice:
    return ZHADevice(
        device_id="dev-pair",
        ieee="c4:d8:c8:ff:fe:3e:e5:cf",
        name="Hallway Dimmer",
        manufacturer="IKEA",
        model="E2201",
        device_type="EndDevice",
        entities=[],
    )


async def test_step_pair_successful_interview(pair_device, mock_z2m_client):
    """Happy path: interview succeeds, device returned from registry."""
    device_entry = {"ieee_address": "0xc4d8c8fffe3ee5cf", "friendly_name": "Hallway Dimmer"}
    mock_z2m_client.wait_for_interview = AsyncMock(return_value=("successful", {}))
    mock_z2m_client.get_device_by_ieee = AsyncMock(return_value=device_entry)

    result = await step_pair_with_z2m(pair_device, mock_z2m_client, timeout=30)

    assert result == device_entry
    mock_z2m_client.disable_permit_join.assert_called_once()


async def test_step_pair_successful_interview_registry_fallback(pair_device, mock_z2m_client):
    """Interview succeeds but get_device_by_ieee returns None — fallback dict returned."""
    mock_z2m_client.wait_for_interview = AsyncMock(return_value=("successful", {}))
    mock_z2m_client.get_device_by_ieee = AsyncMock(return_value=None)

    result = await step_pair_with_z2m(pair_device, mock_z2m_client, timeout=30)

    assert result is not None
    assert result["ieee_address"] == "0xc4d8c8fffe3ee5cf"
    assert result["friendly_name"] == "0xc4d8c8fffe3ee5cf"


async def test_step_pair_warns_on_wrong_device(pair_device, mock_z2m_client, capsys):
    """Unexpected joiner triggers a warning via the on_event callback."""
    device_entry = {"ieee_address": "0xc4d8c8fffe3ee5cf", "friendly_name": "Hallway Dimmer"}
    mock_z2m_client.get_device_by_ieee = AsyncMock(return_value=device_entry)

    async def _wait_with_events(ieee, timeout, on_event=None):
        if on_event:
            on_event(
                "device_joined",
                {"ieee_address": "0xd44867fffe150421", "friendly_name": "0xd44867fffe150421"},
            )
        return "successful", {}

    mock_z2m_client.wait_for_interview.side_effect = _wait_with_events

    result = await step_pair_with_z2m(pair_device, mock_z2m_client, timeout=30)

    captured = capsys.readouterr()
    assert "different device joined Z2M" in captured.out
    assert "0xd44867fffe150421" in captured.out
    assert result == device_entry


async def test_step_pair_timeout_force_continue_with_device(pair_device, mock_z2m_client):
    """Timeout + force: device found in registry, returned."""
    device_entry = {"ieee_address": "0xc4d8c8fffe3ee5cf", "friendly_name": "Hallway Dimmer"}
    mock_z2m_client.wait_for_interview = AsyncMock(return_value=("timeout", None))
    mock_z2m_client.get_device_by_ieee = AsyncMock(return_value=device_entry)

    with patch("questionary.select") as mock_select:
        mock_select.return_value.unsafe_ask_async = AsyncMock(return_value="force")
        result = await step_pair_with_z2m(pair_device, mock_z2m_client, timeout=30)

    assert result == device_entry


async def test_step_pair_timeout_force_continue_fallback(pair_device, mock_z2m_client):
    """Timeout + force: device not in registry — IEEE-hex fallback dict returned."""
    mock_z2m_client.wait_for_interview = AsyncMock(return_value=("timeout", None))
    mock_z2m_client.get_device_by_ieee = AsyncMock(return_value=None)

    with patch("questionary.select") as mock_select:
        mock_select.return_value.unsafe_ask_async = AsyncMock(return_value="force")
        result = await step_pair_with_z2m(pair_device, mock_z2m_client, timeout=30)

    assert result is not None
    assert result["ieee_address"] == "0xc4d8c8fffe3ee5cf"
    assert result["friendly_name"] == "0xc4d8c8fffe3ee5cf"


async def test_step_pair_interview_failed_retry_then_succeeds(pair_device, mock_z2m_client):
    """Interview fails → user retries → second attempt succeeds."""
    device_entry = {"ieee_address": "0xc4d8c8fffe3ee5cf", "friendly_name": "Hallway Dimmer"}
    mock_z2m_client.wait_for_interview = AsyncMock(side_effect=[("failed", {}), ("successful", {})])
    mock_z2m_client.get_device_by_ieee = AsyncMock(return_value=device_entry)

    with patch("questionary.select") as mock_select:
        mock_select.return_value.unsafe_ask_async = AsyncMock(return_value="retry")
        result = await step_pair_with_z2m(pair_device, mock_z2m_client, timeout=30)

    assert result == device_entry


async def test_step_pair_interview_failed_force_continue(pair_device, mock_z2m_client):
    """Interview fails → user force-continues → device from registry returned."""
    device_entry = {"ieee_address": "0xc4d8c8fffe3ee5cf", "friendly_name": "Hallway Dimmer"}
    mock_z2m_client.wait_for_interview = AsyncMock(return_value=("failed", {}))
    mock_z2m_client.get_device_by_ieee = AsyncMock(return_value=device_entry)

    with patch("questionary.select") as mock_select:
        mock_select.return_value.unsafe_ask_async = AsyncMock(return_value="force")
        result = await step_pair_with_z2m(pair_device, mock_z2m_client, timeout=30)

    assert result == device_entry


async def test_step_pair_interview_failed_mark_as_failed(pair_device, mock_z2m_client):
    """Interview fails → user marks as failed → None returned."""
    mock_z2m_client.wait_for_interview = AsyncMock(return_value=("failed", {}))

    with patch("questionary.select") as mock_select:
        mock_select.return_value.unsafe_ask_async = AsyncMock(return_value="fail")
        result = await step_pair_with_z2m(pair_device, mock_z2m_client, timeout=30)

    assert result is None


async def test_step_pair_ws_error_falls_through_to_timeout(pair_device, mock_z2m_client):
    """WebSocket error in wait_for_interview → timeout prompt shown."""
    mock_z2m_client.wait_for_interview = AsyncMock(side_effect=RuntimeError("WS failed"))

    with patch("questionary.select") as mock_select:
        mock_select.return_value.unsafe_ask_async = AsyncMock(return_value="fail")
        result = await step_pair_with_z2m(pair_device, mock_z2m_client, timeout=30)

    assert result is None
    # Timeout prompt was shown (select was called)
    mock_select.assert_called_once()
