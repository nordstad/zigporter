from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class DeviceStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    MIGRATED = "migrated"
    FAILED = "failed"


class DeviceState(BaseModel):
    ieee: str
    name: str
    status: DeviceStatus = DeviceStatus.PENDING
    migrated_at: datetime | None = None
    z2m_friendly_name: str | None = None
    zha_device_name: str | None = None


class MigrationState(BaseModel):
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    zha_export: str
    devices: dict[str, DeviceState] = Field(default_factory=dict)


def load_state(state_path: Path, zha_export_path: Path, devices: list[dict]) -> MigrationState:
    """Load existing state file or create a fresh one from the ZHA export.

    Args:
        state_path: Path to the state JSON file.
        zha_export_path: Path to the ZHA export JSON (stored in state for reference).
        devices: List of ZHADevice-like dicts from the export (must have ieee and name).

    Returns:
        MigrationState loaded from disk or freshly initialised.
    """
    if state_path.exists():
        state = MigrationState.model_validate_json(state_path.read_text())
        # Add any new devices from the export that aren't in the state yet
        for device in devices:
            ieee = device["ieee"]
            if ieee not in state.devices:
                state.devices[ieee] = DeviceState(ieee=ieee, name=device["name"])
        return state

    device_states = {d["ieee"]: DeviceState(ieee=d["ieee"], name=d["name"]) for d in devices}
    return MigrationState(zha_export=str(zha_export_path), devices=device_states)


def save_state(state: MigrationState, state_path: Path) -> None:
    """Persist migration state to disk."""
    state_path.write_text(state.model_dump_json(indent=2))


def mark_pending(state: MigrationState, ieee: str) -> None:
    state.devices[ieee].status = DeviceStatus.PENDING


def mark_in_progress(state: MigrationState, ieee: str) -> None:
    state.devices[ieee].status = DeviceStatus.IN_PROGRESS


def mark_migrated(state: MigrationState, ieee: str, z2m_friendly_name: str) -> None:
    state.devices[ieee].status = DeviceStatus.MIGRATED
    state.devices[ieee].migrated_at = datetime.now(tz=timezone.utc)
    state.devices[ieee].z2m_friendly_name = z2m_friendly_name


def mark_migrated_reverse(state: MigrationState, ieee: str, zha_device_name: str) -> None:
    state.devices[ieee].status = DeviceStatus.MIGRATED
    state.devices[ieee].migrated_at = datetime.now(tz=timezone.utc)
    state.devices[ieee].zha_device_name = zha_device_name


def mark_failed(state: MigrationState, ieee: str) -> None:
    state.devices[ieee].status = DeviceStatus.FAILED
