import json
from unittest.mock import AsyncMock, patch

import pytest

from zigporter.commands.list_z2m import run_list_z2m


HA_URL = "https://ha.test"
TOKEN = "test-token"
Z2M_URL = "https://z2m.test"


@pytest.fixture
def z2m_devices():
    return [
        {
            "friendly_name": "Kitchen Plug",
            "ieee_address": "0x0011223344556677",
            "type": "EndDevice",
            "power_source": "Mains (single phase)",
            "supported": True,
            "definition": {"vendor": "IKEA", "model": "E1603"},
        },
        {
            "friendly_name": "Coordinator",
            "ieee_address": "0x0000000000000000",
            "type": "Coordinator",
            "power_source": "Mains (single phase)",
            "supported": True,
            "definition": None,
        },
        {
            "friendly_name": "Unknown Sensor",
            "ieee_address": "0xaabbccddeeff0011",
            "type": "EndDevice",
            "power_source": "Battery",
            "supported": False,
            "definition": None,
            "manufacturer": "Acme",
            "model_id": "XYZ-1",
        },
    ]


async def test_run_list_z2m_excludes_coordinator(z2m_devices, mocker):
    mock_client = mocker.MagicMock()
    mock_client.get_devices = AsyncMock(return_value=z2m_devices)

    with patch("zigporter.commands.list_z2m.Z2MClient", return_value=mock_client):
        with patch("zigporter.commands.list_z2m.console"):
            await run_list_z2m(HA_URL, TOKEN, Z2M_URL, verify_ssl=False)

    mock_client.get_devices.assert_awaited_once()


async def test_run_list_z2m_unsupported_device_dim_style(z2m_devices, mocker):
    mock_client = mocker.MagicMock()
    mock_client.get_devices = AsyncMock(return_value=z2m_devices)

    added_rows = []

    def capture_add_row(*args, style="", **kwargs):
        added_rows.append({"args": args, "style": style})

    mock_table = mocker.MagicMock()
    mock_table.add_row = capture_add_row

    with patch("zigporter.commands.list_z2m.Z2MClient", return_value=mock_client):
        with patch("zigporter.commands.list_z2m.Table", return_value=mock_table):
            with patch("zigporter.commands.list_z2m.console"):
                with patch("zigporter.commands.list_z2m.Progress") as mock_progress_cls:
                    mock_progress = mocker.MagicMock()
                    mock_progress.__enter__ = mocker.MagicMock(return_value=mock_progress)
                    mock_progress.__exit__ = mocker.MagicMock(return_value=False)
                    mock_progress_cls.return_value = mock_progress
                    await run_list_z2m(HA_URL, TOKEN, Z2M_URL, verify_ssl=False)

    styles = [r["style"] for r in added_rows]
    assert "dim" in styles
    assert "" in styles


async def test_run_list_z2m_empty_device_list(mocker):
    mock_client = mocker.MagicMock()
    mock_client.get_devices = AsyncMock(return_value=[])

    with patch("zigporter.commands.list_z2m.Z2MClient", return_value=mock_client):
        with patch("zigporter.commands.list_z2m.console"):
            await run_list_z2m(HA_URL, TOKEN, Z2M_URL, verify_ssl=True)

    mock_client.get_devices.assert_awaited_once()


async def test_run_list_z2m_fallback_fields(mocker):
    """Devices without definition fall back to manufacturer/model_id fields."""
    devices = [
        {
            "friendly_name": "Bare Device",
            "ieee_address": "0x1234567890abcdef",
            "type": "EndDevice",
            "supported": True,
            "definition": None,
            "manufacturer": "Acme",
            "model_id": "M1",
            "power_source": "Battery",
        }
    ]
    mock_client = mocker.MagicMock()
    mock_client.get_devices = AsyncMock(return_value=devices)

    added_rows = []

    def capture_add_row(*args, style="", **kwargs):
        added_rows.append(args)

    mock_table = mocker.MagicMock()
    mock_table.add_row = capture_add_row

    with patch("zigporter.commands.list_z2m.Z2MClient", return_value=mock_client):
        with patch("zigporter.commands.list_z2m.Table", return_value=mock_table):
            with patch("zigporter.commands.list_z2m.console"):
                with patch("zigporter.commands.list_z2m.Progress") as mock_progress_cls:
                    mock_progress = mocker.MagicMock()
                    mock_progress.__enter__ = mocker.MagicMock(return_value=mock_progress)
                    mock_progress.__exit__ = mocker.MagicMock(return_value=False)
                    mock_progress_cls.return_value = mock_progress
                    await run_list_z2m(HA_URL, TOKEN, Z2M_URL, verify_ssl=False)

    assert len(added_rows) == 1
    row = added_rows[0]
    assert "Acme" in row
    assert "M1" in row


async def test_run_list_z2m_uses_ieee_when_no_friendly_name(mocker):
    """Falls back to ieee_address when friendly_name is missing."""
    devices = [
        {
            "ieee_address": "0xdeadbeefdeadbeef",
            "type": "EndDevice",
            "supported": True,
            "definition": None,
        }
    ]
    mock_client = mocker.MagicMock()
    mock_client.get_devices = AsyncMock(return_value=devices)

    added_rows = []

    def capture_add_row(*args, style="", **kwargs):
        added_rows.append(args)

    mock_table = mocker.MagicMock()
    mock_table.add_row = capture_add_row

    with patch("zigporter.commands.list_z2m.Z2MClient", return_value=mock_client):
        with patch("zigporter.commands.list_z2m.Table", return_value=mock_table):
            with patch("zigporter.commands.list_z2m.console"):
                with patch("zigporter.commands.list_z2m.Progress") as mock_progress_cls:
                    mock_progress = mocker.MagicMock()
                    mock_progress.__enter__ = mocker.MagicMock(return_value=mock_progress)
                    mock_progress.__exit__ = mocker.MagicMock(return_value=False)
                    mock_progress_cls.return_value = mock_progress
                    await run_list_z2m(HA_URL, TOKEN, Z2M_URL, verify_ssl=False)

    assert added_rows[0][0] == "0xdeadbeefdeadbeef"


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


async def test_run_list_z2m_json_output_shape(z2m_devices, mocker, capsys):
    """--json emits valid JSON with the expected top-level key and field names."""
    mock_client = mocker.MagicMock()
    mock_client.get_devices = AsyncMock(return_value=z2m_devices)

    with patch("zigporter.commands.list_z2m.Z2MClient", return_value=mock_client):
        await run_list_z2m(HA_URL, TOKEN, Z2M_URL, verify_ssl=False, json_output=True)

    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert "devices" in data
    devices = data["devices"]
    # Coordinator is excluded
    assert all(d["type"] != "Coordinator" for d in devices)
    # Check required fields are present
    for d in devices:
        for key in (
            "friendly_name",
            "ieee_address",
            "type",
            "vendor",
            "model",
            "power_source",
            "supported",
        ):
            assert key in d


async def test_run_list_z2m_json_output_field_values(z2m_devices, mocker, capsys):
    """Field values are correctly populated from Z2M device data."""
    mock_client = mocker.MagicMock()
    mock_client.get_devices = AsyncMock(return_value=z2m_devices)

    with patch("zigporter.commands.list_z2m.Z2MClient", return_value=mock_client):
        await run_list_z2m(HA_URL, TOKEN, Z2M_URL, verify_ssl=False, json_output=True)

    captured = capsys.readouterr()
    devices = json.loads(captured.out)["devices"]

    plug = next(d for d in devices if d["friendly_name"] == "Kitchen Plug")
    assert plug["ieee_address"] == "0x0011223344556677"
    assert plug["vendor"] == "IKEA"
    assert plug["model"] == "E1603"
    assert plug["power_source"] == "Mains (single phase)"
    assert plug["supported"] is True

    unsupported = next(d for d in devices if d["friendly_name"] == "Unknown Sensor")
    assert unsupported["supported"] is False
    assert unsupported["vendor"] == "Acme"
    assert unsupported["model"] == "XYZ-1"


async def test_run_list_z2m_json_output_no_spinner(z2m_devices, mocker, capsys):
    """The progress spinner is not printed when json_output=True."""
    mock_client = mocker.MagicMock()
    mock_client.get_devices = AsyncMock(return_value=z2m_devices)

    with patch("zigporter.commands.list_z2m.Z2MClient", return_value=mock_client):
        with patch("zigporter.commands.list_z2m.Progress") as mock_progress_cls:
            await run_list_z2m(HA_URL, TOKEN, Z2M_URL, verify_ssl=False, json_output=True)

    mock_progress_cls.assert_not_called()


async def test_run_list_z2m_json_output_excludes_coordinator(z2m_devices, mocker, capsys):
    """Coordinator is excluded from JSON output."""
    mock_client = mocker.MagicMock()
    mock_client.get_devices = AsyncMock(return_value=z2m_devices)

    with patch("zigporter.commands.list_z2m.Z2MClient", return_value=mock_client):
        await run_list_z2m(HA_URL, TOKEN, Z2M_URL, verify_ssl=False, json_output=True)

    captured = capsys.readouterr()
    devices = json.loads(captured.out)["devices"]
    assert not any(d["friendly_name"] == "Coordinator" for d in devices)
    assert len(devices) == 2
