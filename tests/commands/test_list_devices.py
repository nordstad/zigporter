"""Tests for the list-devices command."""

import json
from unittest.mock import AsyncMock, patch

from zigporter.commands.list_devices import (
    _integration_label,
    list_devices_command,
    run_list_devices,
)


HA_URL = "https://ha.test"
TOKEN = "test-token"

DEVICES = [
    {
        "id": "dev1",
        "name": "Kitchen Light",
        "name_by_user": None,
        "area_id": "kitchen",
        "manufacturer": "IKEA",
        "model": "E14",
        "identifiers": [["zha", "00:11:22:33:44:55:66:77"]],
    },
    {
        "id": "dev2",
        "name": "Bedroom Sensor",
        "name_by_user": "My Sensor",
        "area_id": "bedroom",
        "manufacturer": "Aqara",
        "model": "RTCGQ11LM",
        "identifiers": [["mqtt", "zigbee2mqtt_0xaabbccddeeff0011"]],
    },
    {
        "id": "dev3",
        "name": None,
        "name_by_user": None,
        "area_id": None,
        "manufacturer": None,
        "model": None,
        "identifiers": [],
    },
]

AREAS = [
    {"area_id": "kitchen", "name": "Kitchen"},
    {"area_id": "bedroom", "name": "Bedroom"},
]


# ---------------------------------------------------------------------------
# _integration_label
# ---------------------------------------------------------------------------


def test_integration_label_zha():
    assert _integration_label({"identifiers": [["zha", "00:11:22:33:44:55:66:77"]]}) == "zha"


def test_integration_label_z2m():
    assert (
        _integration_label({"identifiers": [["mqtt", "zigbee2mqtt_0x1234567890abcdef"]]}) == "z2m"
    )


def test_integration_label_mqtt_non_z2m_falls_back_to_domain():
    assert _integration_label({"identifiers": [["mqtt", "some_other_topic"]]}) == "mqtt"


def test_integration_label_matter():
    assert _integration_label({"identifiers": [["matter", "abc"]]}) == "matter"


def test_integration_label_zwave():
    assert _integration_label({"identifiers": [["zwave_js", "abc"]]}) == "zwave"


def test_integration_label_unknown_domain_returns_domain():
    assert _integration_label({"identifiers": [["homekit", "abc"]]}) == "homekit"


def test_integration_label_empty_identifiers_list():
    assert _integration_label({"identifiers": []}) == ""


def test_integration_label_no_identifiers_key():
    assert _integration_label({}) == ""


def test_integration_label_pair_too_short_is_skipped():
    # A pair with len 0 is skipped; the loop exhausts and returns ""
    assert _integration_label({"identifiers": [[]]}) == ""


def test_integration_label_mqtt_single_element_pair_returns_domain():
    # mqtt pair with only one element doesn't satisfy len==2 check → falls through
    assert _integration_label({"identifiers": [["mqtt"]]}) == "mqtt"


# ---------------------------------------------------------------------------
# run_list_devices
# ---------------------------------------------------------------------------


def _make_progress_mock(mocker):
    mock_progress = mocker.MagicMock()
    mock_progress.__enter__ = mocker.MagicMock(return_value=mock_progress)
    mock_progress.__exit__ = mocker.MagicMock(return_value=False)
    return mock_progress


async def test_run_list_devices_fetches_registry(mocker):
    mock_client = mocker.MagicMock()
    mock_client.get_device_registry = AsyncMock(return_value=DEVICES)
    mock_client.get_area_registry = AsyncMock(return_value=AREAS)

    with patch("zigporter.commands.list_devices.HAClient", return_value=mock_client):
        with patch("zigporter.commands.list_devices.console"):
            with patch("zigporter.commands.list_devices.Progress") as mock_cls:
                mock_cls.return_value = _make_progress_mock(mocker)
                await run_list_devices(HA_URL, TOKEN, True)

    mock_client.get_device_registry.assert_awaited_once()
    mock_client.get_area_registry.assert_awaited_once()


async def test_run_list_devices_filters_unnamed_devices(mocker):
    """Devices without a name or name_by_user are excluded from the table."""
    mock_client = mocker.MagicMock()
    mock_client.get_device_registry = AsyncMock(return_value=DEVICES)
    mock_client.get_area_registry = AsyncMock(return_value=AREAS)

    added_rows = []
    mock_table = mocker.MagicMock()
    mock_table.add_row = lambda *args: added_rows.append(args)

    with patch("zigporter.commands.list_devices.HAClient", return_value=mock_client):
        with patch("zigporter.commands.list_devices.console"):
            with patch("zigporter.commands.list_devices.Table", return_value=mock_table):
                with patch("zigporter.commands.list_devices.Progress") as mock_cls:
                    mock_cls.return_value = _make_progress_mock(mocker)
                    await run_list_devices(HA_URL, TOKEN, True)

    # dev3 has no name — must be excluded
    assert len(added_rows) == 2


async def test_run_list_devices_area_names_resolved(mocker):
    """Area names are resolved from the area registry."""
    mock_client = mocker.MagicMock()
    mock_client.get_device_registry = AsyncMock(return_value=DEVICES)
    mock_client.get_area_registry = AsyncMock(return_value=AREAS)

    added_rows = []
    mock_table = mocker.MagicMock()
    mock_table.add_row = lambda *args: added_rows.append(args)

    with patch("zigporter.commands.list_devices.HAClient", return_value=mock_client):
        with patch("zigporter.commands.list_devices.console"):
            with patch("zigporter.commands.list_devices.Table", return_value=mock_table):
                with patch("zigporter.commands.list_devices.Progress") as mock_cls:
                    mock_cls.return_value = _make_progress_mock(mocker)
                    await run_list_devices(HA_URL, TOKEN, True)

    area_values = {row[1] for row in added_rows}
    assert "Kitchen" in area_values
    assert "Bedroom" in area_values


async def test_run_list_devices_no_area_shows_empty_string(mocker):
    """Devices without an area_id show an empty string in the Area column."""
    devices = [
        {
            "id": "dev1",
            "name": "Hallway Plug",
            "name_by_user": None,
            "area_id": None,
            "manufacturer": "IKEA",
            "model": "E1603",
            "identifiers": [["zha", "00:aa:bb:cc:dd:ee:ff:00"]],
        }
    ]
    mock_client = mocker.MagicMock()
    mock_client.get_device_registry = AsyncMock(return_value=devices)
    mock_client.get_area_registry = AsyncMock(return_value=[])

    added_rows = []
    mock_table = mocker.MagicMock()
    mock_table.add_row = lambda *args: added_rows.append(args)

    with patch("zigporter.commands.list_devices.HAClient", return_value=mock_client):
        with patch("zigporter.commands.list_devices.console"):
            with patch("zigporter.commands.list_devices.Table", return_value=mock_table):
                with patch("zigporter.commands.list_devices.Progress") as mock_cls:
                    mock_cls.return_value = _make_progress_mock(mocker)
                    await run_list_devices(HA_URL, TOKEN, False)

    assert added_rows[0][1] == ""


async def test_run_list_devices_empty_registry(mocker):
    """No devices → table with zero rows, no error."""
    mock_client = mocker.MagicMock()
    mock_client.get_device_registry = AsyncMock(return_value=[])
    mock_client.get_area_registry = AsyncMock(return_value=[])

    with patch("zigporter.commands.list_devices.HAClient", return_value=mock_client):
        with patch("zigporter.commands.list_devices.console"):
            with patch("zigporter.commands.list_devices.Progress") as mock_cls:
                mock_cls.return_value = _make_progress_mock(mocker)
                await run_list_devices(HA_URL, TOKEN, True)


# ---------------------------------------------------------------------------
# list_devices_command (sync wrapper)
# ---------------------------------------------------------------------------


def test_list_devices_command_runs_asyncio(mocker):
    mock_run = mocker.patch(
        "zigporter.commands.list_devices.asyncio.run",
        side_effect=lambda coro: coro.close(),
    )
    list_devices_command(HA_URL, TOKEN, True)
    mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


async def test_run_list_devices_json_output_shape(mocker, capsys):
    """--json emits valid JSON with expected top-level key and field names."""
    mock_client = mocker.MagicMock()
    mock_client.get_device_registry = AsyncMock(return_value=DEVICES)
    mock_client.get_area_registry = AsyncMock(return_value=AREAS)

    with patch("zigporter.commands.list_devices.HAClient", return_value=mock_client):
        await run_list_devices(HA_URL, TOKEN, True, json_output=True)

    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert "devices" in data
    for d in data["devices"]:
        for key in ("name", "area", "integration", "manufacturer", "model", "device_id"):
            assert key in d


async def test_run_list_devices_json_output_field_values(mocker, capsys):
    """Field values are correctly populated from the HA device registry."""
    mock_client = mocker.MagicMock()
    mock_client.get_device_registry = AsyncMock(return_value=DEVICES)
    mock_client.get_area_registry = AsyncMock(return_value=AREAS)

    with patch("zigporter.commands.list_devices.HAClient", return_value=mock_client):
        await run_list_devices(HA_URL, TOKEN, True, json_output=True)

    captured = capsys.readouterr()
    devices = json.loads(captured.out)["devices"]

    kitchen = next(d for d in devices if d["name"] == "Kitchen Light")
    assert kitchen["area"] == "Kitchen"
    assert kitchen["integration"] == "zha"
    assert kitchen["manufacturer"] == "IKEA"
    assert kitchen["model"] == "E14"
    assert kitchen["device_id"] == "dev1"

    bedroom = next(d for d in devices if d["name"] == "My Sensor")
    assert bedroom["area"] == "Bedroom"
    assert bedroom["integration"] == "z2m"
    assert bedroom["device_id"] == "dev2"


async def test_run_list_devices_json_output_filters_unnamed(mocker, capsys):
    """Devices without a name are excluded from JSON output."""
    mock_client = mocker.MagicMock()
    mock_client.get_device_registry = AsyncMock(return_value=DEVICES)
    mock_client.get_area_registry = AsyncMock(return_value=AREAS)

    with patch("zigporter.commands.list_devices.HAClient", return_value=mock_client):
        await run_list_devices(HA_URL, TOKEN, True, json_output=True)

    captured = capsys.readouterr()
    devices = json.loads(captured.out)["devices"]
    # dev3 has no name — excluded
    assert len(devices) == 2


async def test_run_list_devices_json_output_no_spinner(mocker, capsys):
    """The progress spinner is not shown when json_output=True."""
    mock_client = mocker.MagicMock()
    mock_client.get_device_registry = AsyncMock(return_value=DEVICES)
    mock_client.get_area_registry = AsyncMock(return_value=AREAS)

    with patch("zigporter.commands.list_devices.HAClient", return_value=mock_client):
        with patch("zigporter.commands.list_devices.Progress") as mock_progress_cls:
            await run_list_devices(HA_URL, TOKEN, True, json_output=True)

    mock_progress_cls.assert_not_called()
