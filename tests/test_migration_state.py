import json

from zigporter.migration_state import (
    DeviceStatus,
    load_state,
    mark_failed,
    mark_in_progress,
    mark_migrated,
    save_state,
)

DEVICES = [
    {"ieee": "00:11:22:33:44:55:66:77", "name": "Living Room Thermostat"},
    {"ieee": "aa:bb:cc:dd:ee:ff:00:11", "name": "Kitchen Plug"},
]


def test_load_state_creates_fresh_when_missing(tmp_path):
    state_path = tmp_path / "state.json"
    export_path = tmp_path / "export.json"

    state = load_state(state_path, export_path, DEVICES)

    assert len(state.devices) == 2
    assert state.devices["00:11:22:33:44:55:66:77"].status == DeviceStatus.PENDING
    assert state.devices["aa:bb:cc:dd:ee:ff:00:11"].name == "Kitchen Plug"


def test_load_state_loads_existing_file(tmp_path):
    state_path = tmp_path / "state.json"
    export_path = tmp_path / "export.json"

    # Create and save initial state
    state = load_state(state_path, export_path, DEVICES)
    mark_migrated(state, "00:11:22:33:44:55:66:77", "Living Room Thermostat")
    save_state(state, state_path)

    # Reload from disk
    reloaded = load_state(state_path, export_path, DEVICES)

    assert reloaded.devices["00:11:22:33:44:55:66:77"].status == DeviceStatus.MIGRATED
    assert reloaded.devices["aa:bb:cc:dd:ee:ff:00:11"].status == DeviceStatus.PENDING


def test_load_state_adds_new_devices_to_existing(tmp_path):
    state_path = tmp_path / "state.json"
    export_path = tmp_path / "export.json"

    # Save state with only one device
    state = load_state(state_path, export_path, DEVICES[:1])
    save_state(state, state_path)

    # Reload with two devices — second should be added as pending
    reloaded = load_state(state_path, export_path, DEVICES)

    assert "aa:bb:cc:dd:ee:ff:00:11" in reloaded.devices
    assert reloaded.devices["aa:bb:cc:dd:ee:ff:00:11"].status == DeviceStatus.PENDING


def test_save_state_persists_to_disk(tmp_path):
    state_path = tmp_path / "state.json"
    export_path = tmp_path / "export.json"

    state = load_state(state_path, export_path, DEVICES)
    save_state(state, state_path)

    assert state_path.exists()
    data = json.loads(state_path.read_text())
    assert "devices" in data


def test_mark_in_progress(tmp_path):
    state = load_state(tmp_path / "s.json", tmp_path / "e.json", DEVICES)
    mark_in_progress(state, "00:11:22:33:44:55:66:77")
    assert state.devices["00:11:22:33:44:55:66:77"].status == DeviceStatus.IN_PROGRESS


def test_mark_migrated(tmp_path):
    state = load_state(tmp_path / "s.json", tmp_path / "e.json", DEVICES)
    mark_migrated(state, "00:11:22:33:44:55:66:77", "Living Room Thermostat")

    dev = state.devices["00:11:22:33:44:55:66:77"]
    assert dev.status == DeviceStatus.MIGRATED
    assert dev.migrated_at is not None
    assert dev.z2m_friendly_name == "Living Room Thermostat"


def test_mark_failed(tmp_path):
    state = load_state(tmp_path / "s.json", tmp_path / "e.json", DEVICES)
    mark_failed(state, "aa:bb:cc:dd:ee:ff:00:11")
    assert state.devices["aa:bb:cc:dd:ee:ff:00:11"].status == DeviceStatus.FAILED
