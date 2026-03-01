from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch


from zigporter.commands.z2m_health import (
    SortField,
    _extract_health,
    _format_relative,
    _parse_last_seen,
    _row_sort_key,
    run_z2m_health,
)


HA_URL = "https://ha.test"
TOKEN = "test-token"
Z2M_URL = "https://z2m.test"

NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# _parse_last_seen
# ---------------------------------------------------------------------------


def test_parse_last_seen_none():
    assert _parse_last_seen(None) is None


def test_parse_last_seen_na_string():
    assert _parse_last_seen("N/A") is None


def test_parse_last_seen_empty_string():
    assert _parse_last_seen("") is None


def test_parse_last_seen_ms_integer():
    result = _parse_last_seen(1717243200000)  # 2024-06-01T12:00:00Z
    assert result is not None
    assert result.tzinfo is UTC
    assert result.year == 2024


def test_parse_last_seen_iso_string_utc():
    result = _parse_last_seen("2024-06-01T12:00:00Z")
    assert result is not None
    assert result.year == 2024
    assert result.month == 6


def test_parse_last_seen_iso_string_with_offset():
    result = _parse_last_seen("2024-06-01T14:00:00+02:00")
    assert result is not None
    assert result.year == 2024


def test_parse_last_seen_invalid_string():
    assert _parse_last_seen("not-a-date") is None


# ---------------------------------------------------------------------------
# _format_relative
# ---------------------------------------------------------------------------


def test_format_relative_none():
    assert _format_relative(None, NOW) == "—"


def test_format_relative_seconds():
    dt = NOW - timedelta(seconds=30)
    assert _format_relative(dt, NOW) == "30s ago"


def test_format_relative_minutes():
    dt = NOW - timedelta(minutes=45)
    result = _format_relative(dt, NOW)
    assert result == "45m ago"


def test_format_relative_hours():
    dt = NOW - timedelta(hours=3)
    result = _format_relative(dt, NOW)
    assert result == "3h ago"


def test_format_relative_days():
    dt = NOW - timedelta(days=3)
    result = _format_relative(dt, NOW)
    assert result == "3d ago"


def test_format_relative_future():
    dt = NOW + timedelta(seconds=10)
    assert _format_relative(dt, NOW) == "just now"


# ---------------------------------------------------------------------------
# _extract_health
# ---------------------------------------------------------------------------


def test_extract_health_top_level_fields():
    device = {
        "linkquality": 200,
        "battery": 85,
        "last_seen": "2024-06-01T11:55:00Z",
    }
    lqi, battery, last_seen_dt = _extract_health(device)
    assert lqi == 200
    assert battery == 85
    assert last_seen_dt is not None


def test_extract_health_state_nested():
    device = {
        "state": {"linkquality": 150, "battery": 42},
        "last_seen": 1717243200000,
    }
    lqi, battery, _ = _extract_health(device)
    assert lqi == 150
    assert battery == 42


def test_extract_health_all_missing():
    lqi, battery, last_seen_dt = _extract_health({})
    assert lqi is None
    assert battery is None
    assert last_seen_dt is None


# ---------------------------------------------------------------------------
# _row_sort_key
# ---------------------------------------------------------------------------


def _make_row(status: str, lqi: int | None, battery: int | None, dt: datetime | None) -> dict:
    return {"status": status, "lqi": lqi, "battery": battery, "last_seen_dt": dt}


def test_sort_key_lqi_none_last():
    row_with = _make_row("OK", 100, 50, None)
    row_without = _make_row("OK", None, 50, None)
    key_with = _row_sort_key(row_with, SortField.lqi)
    key_without = _row_sort_key(row_without, SortField.lqi)
    assert key_with < key_without


def test_sort_key_battery_ascending():
    row_low = _make_row("WARN", 200, 5, None)
    row_high = _make_row("OK", 200, 90, None)
    assert _row_sort_key(row_low, SortField.battery) < _row_sort_key(row_high, SortField.battery)


def test_sort_key_default_offline_first():
    offline = _make_row("OFFLINE", 50, 80, None)
    warn = _make_row("WARN", 50, 5, None)
    ok = _make_row("OK", 200, 80, None)
    assert _row_sort_key(offline, None) < _row_sort_key(warn, None)
    assert _row_sort_key(warn, None) < _row_sort_key(ok, None)


# ---------------------------------------------------------------------------
# run_z2m_health — integration
# ---------------------------------------------------------------------------


def _now_minus(minutes: int) -> str:
    return (datetime.now(tz=UTC) - timedelta(minutes=minutes)).isoformat()


_HEALTHY_DEVICE = {
    "friendly_name": "Kitchen Plug",
    "ieee_address": "0x001122334455",
    "type": "EndDevice",
    "linkquality": 200,
    "state": {"battery": 90},
    "last_seen": _now_minus(2),
}

_WARN_DEVICE = {
    "friendly_name": "Bedroom Door Sensor",
    "ieee_address": "0x00aabbccddee",
    "type": "EndDevice",
    "linkquality": 30,
    "state": {"battery": 5},
    "last_seen": _now_minus(30),
}

_OFFLINE_DEVICE = {
    "friendly_name": "Hallway Temp",
    "ieee_address": "0x001234567890",
    "type": "EndDevice",
    "linkquality": 100,
    "state": {"battery": 80},
    "last_seen": _now_minus(60 * 24 * 3),  # 3 days ago
}

_COORDINATOR = {
    "friendly_name": "Coordinator",
    "ieee_address": "0x0000000000000000",
    "type": "Coordinator",
}


async def _run(devices: list, **kwargs) -> bool:
    defaults = dict(
        ha_url=HA_URL,
        token=TOKEN,
        z2m_url=Z2M_URL,
        verify_ssl=False,
        mqtt_topic="zigbee2mqtt",
        sort=None,
        warn_battery=10,
        warn_lqi=50,
        offline_after=60,
        output_format="table",
    )
    defaults.update(kwargs)
    mock_client = AsyncMock()
    mock_client.get_devices = AsyncMock(return_value=devices)
    with patch("zigporter.commands.z2m_health.Z2MClient", return_value=mock_client):
        with patch("zigporter.commands.z2m_health.console"):
            with patch("zigporter.commands.z2m_health.Progress") as mock_progress_cls:
                mock_progress = MagicMock()
                mock_progress.__enter__ = MagicMock(return_value=mock_progress)
                mock_progress.__exit__ = MagicMock(return_value=False)
                mock_progress_cls.return_value = mock_progress
                return await run_z2m_health(**defaults)


async def test_healthy_devices_returns_true():
    result = await _run([_HEALTHY_DEVICE])
    assert result is True


async def test_warn_device_returns_false():
    result = await _run([_WARN_DEVICE])
    assert result is False


async def test_offline_device_returns_false():
    result = await _run([_OFFLINE_DEVICE])
    assert result is False


async def test_coordinator_excluded():
    mock_client = AsyncMock()
    mock_client.get_devices = AsyncMock(return_value=[_COORDINATOR, _HEALTHY_DEVICE])
    with patch("zigporter.commands.z2m_health.Z2MClient", return_value=mock_client):
        with patch("zigporter.commands.z2m_health.console"):
            with patch("zigporter.commands.z2m_health.Progress") as mock_progress_cls:
                mock_progress = MagicMock()
                mock_progress.__enter__ = MagicMock(return_value=mock_progress)
                mock_progress.__exit__ = MagicMock(return_value=False)
                mock_progress_cls.return_value = mock_progress
                result = await run_z2m_health(
                    ha_url=HA_URL,
                    token=TOKEN,
                    z2m_url=Z2M_URL,
                    verify_ssl=False,
                    mqtt_topic="zigbee2mqtt",
                    sort=None,
                    warn_battery=10,
                    warn_lqi=50,
                    offline_after=60,
                    output_format="table",
                )
    assert result is True


async def test_json_output_returns_correct_keys():
    import json as _json

    captured: list[str] = []

    def fake_print(content, *args, **kwargs):
        captured.append(str(content))

    mock_client = AsyncMock()
    mock_client.get_devices = AsyncMock(return_value=[_HEALTHY_DEVICE])
    with patch("zigporter.commands.z2m_health.Z2MClient", return_value=mock_client):
        with patch("zigporter.commands.z2m_health.console") as mock_console:
            mock_console.print.side_effect = fake_print
            with patch("zigporter.commands.z2m_health.Progress") as mock_progress_cls:
                mock_progress = MagicMock()
                mock_progress.__enter__ = MagicMock(return_value=mock_progress)
                mock_progress.__exit__ = MagicMock(return_value=False)
                mock_progress_cls.return_value = mock_progress
                await run_z2m_health(
                    ha_url=HA_URL,
                    token=TOKEN,
                    z2m_url=Z2M_URL,
                    verify_ssl=False,
                    mqtt_topic="zigbee2mqtt",
                    sort=None,
                    warn_battery=10,
                    warn_lqi=50,
                    offline_after=60,
                    output_format="json",
                )

    assert len(captured) == 1
    parsed = _json.loads(captured[0])
    assert isinstance(parsed, list)
    assert len(parsed) == 1
    assert "name" in parsed[0]
    assert "lqi" in parsed[0]
    assert "battery" in parsed[0]
    assert "last_seen" in parsed[0]
    assert "status" in parsed[0]


async def test_no_devices_returns_true():
    result = await _run([])
    assert result is True


async def test_custom_warn_battery_threshold():
    device = dict(_HEALTHY_DEVICE)
    device = {**device, "state": {"battery": 25}}
    # Default warn_battery=10, device at 25% → OK
    assert await _run([device], warn_battery=10) is True
    # With warn_battery=30, device at 25% → WARN
    assert await _run([device], warn_battery=30) is False


async def test_custom_warn_lqi_threshold():
    device = {**_HEALTHY_DEVICE, "linkquality": 60}
    # Default warn_lqi=50, device at 60 → OK
    assert await _run([device], warn_lqi=50) is True
    # With warn_lqi=80, device at 60 → WARN
    assert await _run([device], warn_lqi=80) is False


async def test_sort_by_lqi():
    """Sorting by LQI ascending should not raise and should return a result."""
    result = await _run([_HEALTHY_DEVICE, _WARN_DEVICE], sort=SortField.lqi)
    assert result is False  # _WARN_DEVICE has lqi=30 < threshold=50


async def test_sort_by_battery():
    result = await _run([_HEALTHY_DEVICE, _WARN_DEVICE], sort=SortField.battery)
    assert result is False


async def test_offline_after_custom_threshold():
    # Device last seen 90 minutes ago
    past = datetime.now(tz=UTC) - timedelta(minutes=90)
    device = {
        "friendly_name": "Old Device",
        "ieee_address": "0x001122",
        "type": "EndDevice",
        "linkquality": 200,
        "state": {"battery": 80},
        "last_seen": past.isoformat(),
    }
    # With offline_after=60 (default), 90m → OFFLINE
    assert await _run([device], offline_after=60) is False
    # With offline_after=120, 90m is still OK
    assert await _run([device], offline_after=120) is True
