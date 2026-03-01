"""Tests for the fix_device command."""

from unittest.mock import AsyncMock, MagicMock


from zigporter.commands.fix_device import (
    StalePair,
    _device_display_name,
    _ieee_colon,
    _mqtt_ieee,
    _zha_ieee,
    apply_fix,
    find_stale_pairs,
    run_fix_device,
)


# ---------------------------------------------------------------------------
# Pure helper function tests
# ---------------------------------------------------------------------------


def test_ieee_colon_formats_correctly():
    assert _ieee_colon("0011223344556677") == "00:11:22:33:44:55:66:77"


def test_zha_ieee_returns_normalized_ieee():
    entry = {"identifiers": [["zha", "00:11:22:33:44:55:66:77"]]}
    assert _zha_ieee(entry) == "0011223344556677"


def test_zha_ieee_returns_none_for_non_zha():
    entry = {"identifiers": [["mqtt", "zigbee2mqtt_0x0011223344556677"]]}
    assert _zha_ieee(entry) is None


def test_zha_ieee_returns_none_for_empty():
    assert _zha_ieee({}) is None


def test_mqtt_ieee_with_zigbee2mqtt_0x_prefix():
    entry = {"identifiers": [["mqtt", "zigbee2mqtt_0x0011223344556677"]]}
    assert _mqtt_ieee(entry) == "0011223344556677"


def test_mqtt_ieee_with_zigbee2mqtt_prefix_no_0x():
    entry = {"identifiers": [["mqtt", "zigbee2mqtt_0011223344556677"]]}
    assert _mqtt_ieee(entry) == "0011223344556677"


def test_mqtt_ieee_with_0x_prefix():
    entry = {"identifiers": [["mqtt", "0x0011223344556677"]]}
    assert _mqtt_ieee(entry) == "0011223344556677"


def test_mqtt_ieee_returns_none_for_non_mqtt():
    entry = {"identifiers": [["zha", "00:11:22:33:44:55:66:77"]]}
    assert _mqtt_ieee(entry) is None


def test_mqtt_ieee_returns_none_for_empty():
    assert _mqtt_ieee({}) is None


def test_device_display_name_prefers_name_by_user():
    entry = {"name_by_user": "My Device", "name": "Default Name", "id": "dev-1"}
    assert _device_display_name(entry) == "My Device"


def test_device_display_name_falls_back_to_name():
    entry = {"name": "Default Name", "id": "dev-1"}
    assert _device_display_name(entry) == "Default Name"


def test_device_display_name_falls_back_to_id():
    entry = {"id": "dev-1"}
    assert _device_display_name(entry) == "dev-1"


# ---------------------------------------------------------------------------
# find_stale_pairs tests
# ---------------------------------------------------------------------------


def _zha_dev(dev_id: str, ieee: str, name: str = "") -> dict:
    return {"id": dev_id, "identifiers": [["zha", ieee]], "name": name or f"ZHA {dev_id}"}


def _z2m_dev(dev_id: str, ieee_hex: str, name: str = "Z2M Device") -> dict:
    return {
        "id": dev_id,
        "identifiers": [["mqtt", f"zigbee2mqtt_0x{ieee_hex}"]],
        "name": name,
    }


def _entity(entity_id: str, device_id: str) -> dict:
    return {"entity_id": entity_id, "device_id": device_id}


def test_find_stale_pairs_empty_registries():
    assert find_stale_pairs([], []) == []


def test_find_stale_pairs_no_z2m_counterpart():
    devices = [_zha_dev("dev-zha", "00:11:22:33:44:55:66:77")]
    assert find_stale_pairs(devices, []) == []


def test_find_stale_pairs_no_zha_entry():
    devices = [_z2m_dev("dev-z2m", "0011223344556677")]
    assert find_stale_pairs(devices, []) == []


def test_find_stale_pairs_single_pair_no_suffix_conflicts():
    devices = [
        _zha_dev("dev-zha", "00:11:22:33:44:55:66:77"),
        _z2m_dev("dev-z2m", "0011223344556677", name="Living Room Plug"),
    ]
    entities = [
        _entity("switch.plug", "dev-zha"),
        _entity("sensor.energy", "dev-zha"),
    ]
    pairs = find_stale_pairs(devices, entities)

    assert len(pairs) == 1
    p = pairs[0]
    assert p.ieee == "0011223344556677"
    assert p.name == "Living Room Plug"
    assert p.zha_device_id == "dev-zha"
    assert p.z2m_device_id == "dev-z2m"
    assert set(p.stale_entity_ids) == {"switch.plug", "sensor.energy"}
    assert p.suffix_renames == []


def test_find_stale_pairs_detects_suffix_conflicts():
    devices = [
        _zha_dev("dev-zha", "00:11:22:33:44:55:66:77"),
        _z2m_dev("dev-z2m", "0011223344556677"),
    ]
    entities = [
        # Stale ZHA entities occupy original IDs
        _entity("switch.plug", "dev-zha"),
        _entity("sensor.energy", "dev-zha"),
        # Z2M got _2 suffixes due to conflict
        _entity("switch.plug_2", "dev-z2m"),
        _entity("sensor.energy_2", "dev-z2m"),
        _entity("sensor.linkquality", "dev-z2m"),  # no conflict — no suffix
    ]

    pairs = find_stale_pairs(devices, entities)

    assert len(pairs) == 1
    p = pairs[0]
    assert set(p.stale_entity_ids) == {"switch.plug", "sensor.energy"}
    assert set(p.suffix_renames) == {
        ("switch.plug_2", "switch.plug"),
        ("sensor.energy_2", "sensor.energy"),
    }


def test_find_stale_pairs_z2m_suffix_without_stale_conflict_ignored():
    """A _2 suffix on a Z2M entity is only flagged if the base ID exists on the ZHA device."""
    devices = [
        _zha_dev("dev-zha", "00:11:22:33:44:55:66:77"),
        _z2m_dev("dev-z2m", "0011223344556677"),
    ]
    entities = [
        _entity("switch.plug", "dev-zha"),
        # sensor.unrelated_2 has a suffix but "sensor.unrelated" is NOT on the ZHA device
        _entity("sensor.unrelated_2", "dev-z2m"),
    ]
    pairs = find_stale_pairs(devices, entities)

    assert len(pairs) == 1
    assert pairs[0].suffix_renames == []


def test_find_stale_pairs_multiple_devices():
    devices = [
        _zha_dev("dev-zha1", "00:11:22:33:44:55:66:77"),
        _z2m_dev("dev-z2m1", "0011223344556677", name="Device A"),
        _zha_dev("dev-zha2", "aa:bb:cc:dd:ee:ff:00:11"),
        _z2m_dev("dev-z2m2", "aabbccddeeff0011", name="Device B"),
    ]
    pairs = find_stale_pairs(devices, [])
    assert len(pairs) == 2
    names = {p.name for p in pairs}
    assert names == {"Device A", "Device B"}


# ---------------------------------------------------------------------------
# apply_fix tests
# ---------------------------------------------------------------------------


def _make_pair(
    stale_entities: list[str] | None = None,
    suffix_renames: list[tuple[str, str]] | None = None,
) -> StalePair:
    return StalePair(
        ieee="0011223344556677",
        name="Test Device",
        zha_device_id="dev-zha",
        z2m_device_id="dev-z2m",
        stale_entity_ids=stale_entities or [],
        suffix_renames=suffix_renames or [],
    )


async def test_apply_fix_happy_path(mocker):
    pair = _make_pair(
        stale_entities=["switch.plug", "sensor.energy"],
        suffix_renames=[("switch.plug_2", "switch.plug")],
    )
    ha = mocker.AsyncMock()

    await apply_fix(pair, ha)

    ha.delete_entity.assert_any_call("switch.plug")
    ha.delete_entity.assert_any_call("sensor.energy")
    ha.remove_device.assert_called_once_with("dev-zha")
    ha.rename_entity_id.assert_called_once_with("switch.plug_2", "switch.plug")


async def test_apply_fix_delete_entity_failure_continues(mocker):
    pair = _make_pair(stale_entities=["switch.plug"])
    ha = mocker.AsyncMock()
    ha.delete_entity.side_effect = Exception("Not found")

    await apply_fix(pair, ha)  # must not raise

    ha.delete_entity.assert_called_once()


async def test_apply_fix_remove_device_unknown_command_falls_back_to_zha(mocker):
    pair = _make_pair()
    ha = mocker.AsyncMock()
    ha.remove_device.side_effect = RuntimeError("command failed: unknown_command")

    await apply_fix(pair, ha)

    ha.remove_zha_device.assert_called_once_with("00:11:22:33:44:55:66:77")


async def test_apply_fix_remove_device_other_error_also_tries_zha(mocker):
    pair = _make_pair()
    ha = mocker.AsyncMock()
    ha.remove_device.side_effect = RuntimeError("some other error")

    await apply_fix(pair, ha)

    ha.remove_zha_device.assert_called_once()


async def test_apply_fix_both_removal_methods_fail(mocker):
    pair = _make_pair()
    ha = mocker.AsyncMock()
    ha.remove_device.side_effect = RuntimeError("command failed: unknown_command")
    ha.remove_zha_device.side_effect = Exception("ZHA offline")

    await apply_fix(pair, ha)  # must not raise


async def test_apply_fix_rename_entity_failure_continues(mocker):
    pair = _make_pair(suffix_renames=[("switch.plug_2", "switch.plug")])
    ha = mocker.AsyncMock()
    ha.rename_entity_id.side_effect = Exception("Rename failed")

    await apply_fix(pair, ha)  # must not raise

    ha.rename_entity_id.assert_called_once()


async def test_apply_fix_no_stale_entities_or_renames(mocker):
    """Pair with no entities/renames: only device removal is attempted."""
    pair = _make_pair()
    ha = mocker.AsyncMock()

    await apply_fix(pair, ha)

    ha.delete_entity.assert_not_called()
    ha.remove_device.assert_called_once_with("dev-zha")
    ha.rename_entity_id.assert_not_called()


# ---------------------------------------------------------------------------
# run_fix_device tests
# ---------------------------------------------------------------------------


async def test_run_fix_device_no_stale_pairs(mocker):
    ha_mock = mocker.AsyncMock()
    ha_mock.get_device_registry.return_value = []
    ha_mock.get_entity_registry.return_value = []
    mocker.patch("zigporter.commands.fix_device.HAClient", return_value=ha_mock)
    mock_apply = mocker.patch("zigporter.commands.fix_device.apply_fix", new_callable=AsyncMock)

    await run_fix_device("https://ha.test", "token", False)

    mock_apply.assert_not_called()


async def test_run_fix_device_single_pair_confirmed(mocker):
    pair = _make_pair(stale_entities=["switch.plug"])
    ha_mock = mocker.AsyncMock()
    mocker.patch("zigporter.commands.fix_device.HAClient", return_value=ha_mock)
    mocker.patch("zigporter.commands.fix_device.find_stale_pairs", return_value=[pair])
    mocker.patch(
        "questionary.confirm",
        return_value=MagicMock(unsafe_ask_async=AsyncMock(return_value=True)),
    )
    mock_apply = mocker.patch("zigporter.commands.fix_device.apply_fix", new_callable=AsyncMock)

    await run_fix_device("https://ha.test", "token", False)

    mock_apply.assert_called_once_with(pair, ha_mock)


async def test_run_fix_device_single_pair_no_entities_confirmed(mocker):
    """Pair with no stale entities hits the else branch in _show_plan."""
    pair = _make_pair()
    ha_mock = mocker.AsyncMock()
    mocker.patch("zigporter.commands.fix_device.HAClient", return_value=ha_mock)
    mocker.patch("zigporter.commands.fix_device.find_stale_pairs", return_value=[pair])
    mocker.patch(
        "questionary.confirm",
        return_value=MagicMock(unsafe_ask_async=AsyncMock(return_value=True)),
    )
    mock_apply = mocker.patch("zigporter.commands.fix_device.apply_fix", new_callable=AsyncMock)

    await run_fix_device("https://ha.test", "token", False)

    mock_apply.assert_called_once_with(pair, ha_mock)


async def test_run_fix_device_single_pair_with_renames_confirmed(mocker):
    """Covers the suffix_renames row in _show_plan's table (line 217)."""
    pair = _make_pair(
        stale_entities=["switch.plug"],
        suffix_renames=[("switch.plug_2", "switch.plug")],
    )
    ha_mock = mocker.AsyncMock()
    mocker.patch("zigporter.commands.fix_device.HAClient", return_value=ha_mock)
    mocker.patch("zigporter.commands.fix_device.find_stale_pairs", return_value=[pair])
    mocker.patch(
        "questionary.confirm",
        return_value=MagicMock(unsafe_ask_async=AsyncMock(return_value=True)),
    )
    mock_apply = mocker.patch("zigporter.commands.fix_device.apply_fix", new_callable=AsyncMock)

    await run_fix_device("https://ha.test", "token", False)

    mock_apply.assert_called_once_with(pair, ha_mock)


async def test_run_fix_device_single_pair_aborted(mocker):
    pair = _make_pair(stale_entities=["switch.plug"])
    ha_mock = mocker.AsyncMock()
    mocker.patch("zigporter.commands.fix_device.HAClient", return_value=ha_mock)
    mocker.patch("zigporter.commands.fix_device.find_stale_pairs", return_value=[pair])
    mocker.patch(
        "questionary.confirm",
        return_value=MagicMock(unsafe_ask_async=AsyncMock(return_value=False)),
    )
    mock_apply = mocker.patch("zigporter.commands.fix_device.apply_fix", new_callable=AsyncMock)

    await run_fix_device("https://ha.test", "token", False)

    mock_apply.assert_not_called()


async def test_run_fix_device_multiple_pairs_user_selects(mocker):
    pair1 = _make_pair(stale_entities=["switch.plug"])
    pair2 = StalePair(
        ieee="aabbccddeeff0011",
        name="Other Device",
        zha_device_id="dev-zha2",
        z2m_device_id="dev-z2m2",
    )
    ha_mock = mocker.AsyncMock()
    mocker.patch("zigporter.commands.fix_device.HAClient", return_value=ha_mock)
    mocker.patch("zigporter.commands.fix_device.find_stale_pairs", return_value=[pair1, pair2])
    mocker.patch(
        "questionary.select",
        return_value=MagicMock(unsafe_ask_async=AsyncMock(return_value=pair1)),
    )
    mocker.patch(
        "questionary.confirm",
        return_value=MagicMock(unsafe_ask_async=AsyncMock(return_value=True)),
    )
    mock_apply = mocker.patch("zigporter.commands.fix_device.apply_fix", new_callable=AsyncMock)

    await run_fix_device("https://ha.test", "token", False)

    mock_apply.assert_called_once_with(pair1, ha_mock)


async def test_run_fix_device_keyboard_interrupt_aborts(mocker):
    pair1 = _make_pair()
    pair2 = StalePair(
        ieee="aabbccddeeff0011",
        name="Other",
        zha_device_id="dev-zha2",
        z2m_device_id="dev-z2m2",
    )
    ha_mock = mocker.AsyncMock()
    mocker.patch("zigporter.commands.fix_device.HAClient", return_value=ha_mock)
    mocker.patch("zigporter.commands.fix_device.find_stale_pairs", return_value=[pair1, pair2])
    mocker.patch(
        "questionary.select",
        return_value=MagicMock(unsafe_ask_async=AsyncMock(side_effect=KeyboardInterrupt)),
    )
    mock_apply = mocker.patch("zigporter.commands.fix_device.apply_fix", new_callable=AsyncMock)

    await run_fix_device("https://ha.test", "token", False)

    mock_apply.assert_not_called()


def test_fix_device_command_runs_asyncio(mocker):
    mock_run = mocker.patch("asyncio.run")
    from zigporter.commands.fix_device import fix_device_command  # noqa: PLC0415

    fix_device_command("https://ha.test", "token", True)

    mock_run.assert_called_once()
    # Close the unawaited coroutine passed to asyncio.run to suppress ResourceWarning
    mock_run.call_args[0][0].close()
