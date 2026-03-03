"""Pydantic data models shared across zigporter.

All models use Pydantic v2. They are used for serialising ZHA export snapshots,
tracking migration state, and reporting pre-flight check results.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class AutomationRef(BaseModel):
    """A Home Assistant automation that references one or more ZHA entities.

    Collected during export so the migrate wizard can update entity IDs inside
    automation configs after a device has been re-paired with Z2M.

    Attributes:
        automation_id: Internal HA automation ID (e.g. ``"1a2b3c4d5e6f"``).
        alias: Human-readable name shown in the HA UI.
        entity_references: Entity IDs from this device that appear in the automation.
    """

    automation_id: str
    alias: str
    entity_references: list[str] = Field(default_factory=list)


class ZHAEntity(BaseModel):
    """A single entity belonging to a ZHA device.

    Attributes:
        entity_id: HA entity ID (e.g. ``"climate.living_room_thermostat"``).
        name: Integration-assigned name.
        name_by_user: User-customised label, overrides ``name`` in the UI.
        platform: Integration platform (always ``"zha"`` for ZHA entities).
        unique_id: Stable identifier used by HA to track the entity across renames.
        device_class: HA device class (e.g. ``"temperature"``, ``"motion"``).
        disabled: ``True`` if the entity is disabled in HA.
        state: Last-known state string at export time (e.g. ``"on"``, ``"22.5"``).
        attributes: Full state attributes dict at export time.
    """

    entity_id: str
    name: str
    name_by_user: str | None = None
    platform: str
    unique_id: str | None = None
    device_class: str | None = None
    disabled: bool = False
    state: str | None = None
    attributes: dict = Field(default_factory=dict)


class ZHADevice(BaseModel):
    """A ZHA device as exported from Home Assistant.

    Represents the complete state of a Zigbee device at export time, including
    its entities, area assignment, and any automations that reference it.

    Attributes:
        device_id: HA device registry ID (UUID string).
        ieee: IEEE 802.15.4 address in colon-separated hex (e.g. ``"00:11:22:33:44:55:66:77"``).
        name: Integration-assigned device name.
        name_by_user: User-customised label shown in the HA UI.
        manufacturer: Device manufacturer as reported by the Zigbee coordinator.
        model: Device model string.
        area_id: HA area registry ID, if the device is assigned to a room.
        area_name: Human-readable area name corresponding to ``area_id``.
        device_type: Zigbee device role: ``"EndDevice"``, ``"Router"``, or ``"Coordinator"``.
        quirk_applied: ``True`` if a ZHA quirk patch was active on the device.
        quirk_class: Fully-qualified Python class name of the applied quirk.
        entities: All entities registered to this device.
        automations: Automations that reference at least one entity of this device.
    """

    device_id: str
    ieee: str
    name: str
    name_by_user: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    area_id: str | None = None
    area_name: str | None = None
    device_type: str
    quirk_applied: bool = False
    quirk_class: str | None = None
    entities: list[ZHAEntity] = Field(default_factory=list)
    automations: list[AutomationRef] = Field(default_factory=list)
    available: bool | None = None  # True=online, False=all entities offline, None=unknown


class ZHAExport(BaseModel):
    """Top-level container for a ZHA device inventory snapshot.

    Written to ``~/.config/zigporter/zha-export.json`` by ``zigporter export``
    and read back by ``zigporter migrate``.

    Attributes:
        exported_at: UTC timestamp when the export was created.
        ha_url: HA base URL used during the export (e.g. ``"http://homeassistant.local:8123"``).
        devices: All ZHA devices found in the HA registry at export time.
    """

    exported_at: datetime
    ha_url: str
    devices: list[ZHADevice] = Field(default_factory=list)


class CheckStatus(str, Enum):
    """Result severity for a single pre-flight check.

    Attributes:
        OK: Check passed with no issues.
        FAILED: Check failed; the operation should be blocked if ``blocking=True``.
        WARNING: Check raised a concern but does not block the operation.
        SKIPPED: Check was intentionally not run (e.g. missing optional config).
    """

    OK = "ok"
    FAILED = "failed"
    WARNING = "warning"
    SKIPPED = "skipped"


class CheckResult(BaseModel):
    """Result of a single pre-flight connectivity or configuration check.

    Attributes:
        name: Short human-readable check name (e.g. ``"HA WebSocket"``).
        status: Outcome of the check.
        message: Detail message shown to the user.
        blocking: If ``True`` and ``status`` is ``FAILED``, the calling command
            should abort rather than proceed.
    """

    name: str
    status: CheckStatus
    message: str
    blocking: bool = True
