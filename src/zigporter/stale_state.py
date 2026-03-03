"""Persistent state for the `zigporter stale` offline-device manager.

State is stored in ``~/.config/zigporter/stale.json`` and tracks which offline
devices the user has annotated as stale or ignored across sessions.
"""

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class StaleDeviceStatus(str, Enum):
    STALE = "stale"
    IGNORED = "ignored"


class StaleDeviceEntry(BaseModel):
    device_id: str
    name: str
    first_seen_offline_at: datetime
    status: StaleDeviceStatus = StaleDeviceStatus.STALE
    note: str | None = None


class StaleState(BaseModel):
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    devices: dict[str, StaleDeviceEntry] = Field(default_factory=dict)


def load_stale_state(path: Path) -> StaleState:
    """Load stale state from disk, or return empty state if the file does not exist."""
    if path.exists():
        return StaleState.model_validate_json(path.read_text())
    return StaleState()


def save_stale_state(state: StaleState, path: Path) -> None:
    """Persist stale state to disk."""
    state.updated_at = datetime.now(tz=timezone.utc)
    path.write_text(state.model_dump_json(indent=2))


def record_first_seen(state: StaleState, device_id: str, name: str) -> None:
    """Add a device to the state with first_seen_offline_at if not already present."""
    if device_id not in state.devices:
        state.devices[device_id] = StaleDeviceEntry(
            device_id=device_id,
            name=name,
            first_seen_offline_at=datetime.now(tz=timezone.utc),
        )


def mark_stale(state: StaleState, device_id: str, name: str, note: str | None = None) -> None:
    """Mark a device as stale (optionally with a note)."""
    record_first_seen(state, device_id, name)
    state.devices[device_id].status = StaleDeviceStatus.STALE
    state.devices[device_id].note = note


def mark_ignored(state: StaleState, device_id: str, name: str) -> None:
    """Mark a device as ignored (known offline, no action needed)."""
    record_first_seen(state, device_id, name)
    state.devices[device_id].status = StaleDeviceStatus.IGNORED
    state.devices[device_id].note = None


def unmark(state: StaleState, device_id: str) -> None:
    """Remove a device from the stale state entirely."""
    state.devices.pop(device_id, None)
