"""Export Z2M devices from Home Assistant for reverse migration."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from zigporter.entity_refs import collect_config_entity_ids
from zigporter.ha_client import HAClient
from zigporter.models import AutomationRef, Z2MDevice, Z2MExport, ZHAEntity
from zigporter.utils import normalize_ieee, parse_z2m_ieee_identifier
from zigporter.z2m_client import Z2MClient

console = Console()


def _build_area_map(areas: list[dict[str, Any]]) -> dict[str, str]:
    """Return {area_id: area_name}."""
    return {a["area_id"]: a["name"] for a in areas}


def _build_state_map(states: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return {entity_id: state_entry}."""
    return {s["entity_id"]: s for s in states}


def _build_z2m_entity_map(
    entity_registry: list[dict[str, Any]],
    z2m_device_ids: set[str],
) -> dict[str, list[dict[str, Any]]]:
    """Return {device_id: [entity_registry_entry, ...]} for Z2M (MQTT) devices."""
    result: dict[str, list[dict[str, Any]]] = {}
    for entry in entity_registry:
        device_id = entry.get("device_id")
        if not device_id or device_id not in z2m_device_ids:
            continue
        result.setdefault(device_id, []).append(entry)
    return result


def _match_automations_to_devices(
    automation_configs: list[dict[str, Any]],
    entity_to_device: dict[str, str],
) -> dict[str, list[AutomationRef]]:
    """Return {device_id: [AutomationRef, ...]}."""
    result: dict[str, list[AutomationRef]] = {}

    for config in automation_configs:
        auto_id = config.get("id", "")
        alias = config.get("alias", auto_id)
        entity_ids = sorted(collect_config_entity_ids(config))

        referenced_devices: dict[str, list[str]] = {}
        for eid in entity_ids:
            device_id = entity_to_device.get(eid)
            if device_id:
                referenced_devices.setdefault(device_id, []).append(eid)

        for device_id, refs in referenced_devices.items():
            ref = AutomationRef(
                automation_id=f"automation.{alias.lower().replace(' ', '_')}",
                alias=alias,
                entity_references=refs,
            )
            result.setdefault(device_id, []).append(ref)

    return result


def build_z2m_export(
    z2m_devices: list[dict[str, Any]],
    device_registry: list[dict[str, Any]],
    entity_registry: list[dict[str, Any]],
    area_registry: list[dict[str, Any]],
    states: list[dict[str, Any]],
    automation_configs: list[dict[str, Any]],
    ha_url: str,
) -> Z2MExport:
    """Join all data sources into a Z2MExport."""
    area_map = _build_area_map(area_registry)
    state_map = _build_state_map(states)

    # Map Z2M IEEE -> HA device registry entry
    dr_map: dict[str, dict[str, Any]] = {d["id"]: d for d in device_registry}

    # Build ieee -> device_registry_id mapping from device registry (MQTT identifiers)
    ieee_to_dr_id: dict[str, str] = {}
    for entry in device_registry:
        for platform, identifier in entry.get("identifiers", []):
            if platform == "mqtt":
                ieee_hex = parse_z2m_ieee_identifier(identifier)
                if ieee_hex:
                    ieee_to_dr_id[ieee_hex] = entry["id"]

    z2m_device_ids = set(ieee_to_dr_id.values())
    entity_map = _build_z2m_entity_map(entity_registry, z2m_device_ids)

    # Build entity_id -> device_id for automation matching
    entity_to_device: dict[str, str] = {}
    for entry in entity_registry:
        did = entry.get("device_id")
        if did and did in z2m_device_ids:
            entity_to_device[entry["entity_id"]] = did

    auto_map = _match_automations_to_devices(automation_configs, entity_to_device)

    devices: list[Z2MDevice] = []

    for z2m_dev in z2m_devices:
        ieee_raw = z2m_dev.get("ieee_address", "")
        ieee_norm = normalize_ieee(ieee_raw)
        friendly_name = z2m_dev.get("friendly_name", "")

        # Skip the coordinator
        dev_type = z2m_dev.get("type", "")
        if dev_type == "Coordinator":
            continue

        device_id = ieee_to_dr_id.get(ieee_norm, "")
        if not device_id:
            continue

        dr_entry = dr_map.get(device_id, {})
        area_id = dr_entry.get("area_id")
        area_name = area_map.get(area_id, None) if area_id else None

        # Build entity list
        entity_entries = entity_map.get(device_id, [])
        entities: list[ZHAEntity] = []
        for entry in entity_entries:
            eid = entry.get("entity_id", "")
            state_entry = state_map.get(eid, {})
            attrs = state_entry.get("attributes", {})
            entities.append(
                ZHAEntity(
                    entity_id=eid,
                    name=attrs.get("friendly_name", eid),
                    name_by_user=entry.get("name"),
                    platform=entry.get("platform", "mqtt"),
                    unique_id=entry.get("unique_id"),
                    device_class=entry.get("device_class"),
                    disabled=entry.get("disabled_by") is not None,
                    state=state_entry.get("state"),
                    attributes=attrs,
                )
            )

        enabled_states = [e.state for e in entities if not e.disabled and e.state is not None]
        if enabled_states:
            available: bool | None = any(
                s not in ("unavailable", "unknown") for s in enabled_states
            )
        else:
            available = None

        name_by_user = dr_entry.get("name_by_user")
        manufacturer = z2m_dev.get("manufacturer") or z2m_dev.get("definition", {}).get("vendor")
        model = z2m_dev.get("model_id") or z2m_dev.get("definition", {}).get("model")

        devices.append(
            Z2MDevice(
                device_id=device_id,
                ieee=ieee_raw,
                friendly_name=friendly_name,
                name_by_user=name_by_user,
                manufacturer=manufacturer,
                model=model,
                area_id=area_id,
                area_name=area_name,
                device_type=dev_type or "EndDevice",
                power_source=z2m_dev.get("power_source"),
                entities=entities,
                automations=auto_map.get(device_id, []),
                available=available,
            )
        )

    return Z2MExport(
        exported_at=datetime.now(tz=timezone.utc),
        ha_url=ha_url,
        devices=devices,
    )


async def run_z2m_export(
    ha_url: str,
    token: str,
    verify_ssl: bool,
    z2m_url: str,
    mqtt_topic: str = "zigbee2mqtt",
) -> Z2MExport:
    """Fetch all data from HA and Z2M and build the export."""
    ha_client = HAClient(ha_url, token, verify_ssl)
    z2m_client = Z2MClient(ha_url, token, z2m_url, verify_ssl, mqtt_topic)

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
        t = progress.add_task("Connecting to Home Assistant...", total=None)

        progress.update(t, description="Fetching Z2M device list...")
        z2m_devices = await z2m_client.get_devices()

        if not z2m_devices:
            progress.stop()
            console.print(
                "[yellow]Warning:[/yellow] No Z2M devices found. "
                "Zigbee2MQTT may not be running or accessible."
            )

        progress.update(t, description="Fetching HA registry data...")
        device_registry = await ha_client.get_device_registry()
        entity_registry = await ha_client.get_entity_registry()
        area_registry = await ha_client.get_area_registry()
        automation_configs = await ha_client.get_automation_configs()

        progress.update(t, description="Fetching entity states...")
        states = await ha_client.get_states()

        progress.update(t, description="Building device map...")
        export = build_z2m_export(
            z2m_devices=z2m_devices,
            device_registry=device_registry,
            entity_registry=entity_registry,
            area_registry=area_registry,
            states=states,
            automation_configs=automation_configs,
            ha_url=ha_url,
        )
        progress.stop()

    return export


def z2m_export_command(
    output: Path,
    pretty: bool,
    ha_url: str,
    token: str,
    verify_ssl: bool,
    z2m_url: str,
    mqtt_topic: str = "zigbee2mqtt",
) -> None:
    """Entry point called from the CLI."""
    export = asyncio.run(run_z2m_export(ha_url, token, verify_ssl, z2m_url, mqtt_topic))

    indent = 2 if pretty else None
    output.write_text(export.model_dump_json(indent=indent))

    offline_count = sum(1 for d in export.devices if d.available is False)
    offline_note = f" [dim]({offline_count} offline)[/dim]" if offline_count else ""
    console.print(
        f"\nExport complete: [bold]{len(export.devices)}[/bold] devices{offline_note}, "
        f"[bold]{sum(len(d.entities) for d in export.devices)}[/bold] entities, "
        f"[bold]{sum(len(d.automations) for d in export.devices)}[/bold] automation references\n"
        f"Written to [green]{output}[/green]"
    )
