import json
from datetime import datetime, timezone


from zigporter.stale_state import (
    StaleDeviceStatus,
    StaleState,
    load_stale_state,
    mark_ignored,
    mark_stale,
    record_first_seen,
    save_stale_state,
    unmark,
)


DEVICE_ID = "abc-device-id"
DEVICE_NAME = "Kitchen Outlet"


def test_load_stale_state_creates_empty_when_missing(tmp_path):
    state = load_stale_state(tmp_path / "stale.json")
    assert state.devices == {}


def test_load_stale_state_loads_existing_file(tmp_path):
    path = tmp_path / "stale.json"
    state = StaleState()
    mark_stale(state, DEVICE_ID, DEVICE_NAME)
    save_stale_state(state, path)

    loaded = load_stale_state(path)
    assert DEVICE_ID in loaded.devices
    assert loaded.devices[DEVICE_ID].status == StaleDeviceStatus.STALE


def test_save_stale_state_persists(tmp_path):
    path = tmp_path / "stale.json"
    state = StaleState()
    mark_stale(state, DEVICE_ID, DEVICE_NAME, note="replace soon")
    save_stale_state(state, path)

    data = json.loads(path.read_text())
    assert "devices" in data
    assert DEVICE_ID in data["devices"]
    assert data["devices"][DEVICE_ID]["note"] == "replace soon"


def test_record_first_seen_sets_timestamp(tmp_path):
    state = StaleState()
    before = datetime.now(tz=timezone.utc)
    record_first_seen(state, DEVICE_ID, DEVICE_NAME)
    after = datetime.now(tz=timezone.utc)

    entry = state.devices[DEVICE_ID]
    assert before <= entry.first_seen_offline_at <= after
    assert entry.status == StaleDeviceStatus.NEW


def test_record_first_seen_does_not_overwrite(tmp_path):
    state = StaleState()
    record_first_seen(state, DEVICE_ID, DEVICE_NAME)
    original_ts = state.devices[DEVICE_ID].first_seen_offline_at

    record_first_seen(state, DEVICE_ID, DEVICE_NAME)
    assert state.devices[DEVICE_ID].first_seen_offline_at == original_ts


def test_mark_stale_sets_status_and_note():
    state = StaleState()
    mark_stale(state, DEVICE_ID, DEVICE_NAME, note="check later")
    assert state.devices[DEVICE_ID].status == StaleDeviceStatus.STALE
    assert state.devices[DEVICE_ID].note == "check later"


def test_mark_stale_without_note():
    state = StaleState()
    mark_stale(state, DEVICE_ID, DEVICE_NAME)
    assert state.devices[DEVICE_ID].note is None


def test_mark_ignored_sets_status():
    state = StaleState()
    mark_ignored(state, DEVICE_ID, DEVICE_NAME)
    assert state.devices[DEVICE_ID].status == StaleDeviceStatus.IGNORED


def test_mark_ignored_clears_note():
    state = StaleState()
    mark_stale(state, DEVICE_ID, DEVICE_NAME, note="old note")
    mark_ignored(state, DEVICE_ID, DEVICE_NAME)
    assert state.devices[DEVICE_ID].note is None


def test_unmark_removes_entry():
    state = StaleState()
    mark_stale(state, DEVICE_ID, DEVICE_NAME)
    unmark(state, DEVICE_ID)
    assert DEVICE_ID not in state.devices


def test_unmark_noop_when_missing():
    state = StaleState()
    unmark(state, DEVICE_ID)  # should not raise
    assert DEVICE_ID not in state.devices


def test_save_updates_updated_at(tmp_path):
    path = tmp_path / "stale.json"
    state = StaleState()
    before = datetime.now(tz=timezone.utc)
    save_stale_state(state, path)
    after = datetime.now(tz=timezone.utc)

    loaded = load_stale_state(path)
    assert before <= loaded.updated_at <= after
