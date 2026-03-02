import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from zigporter.entity_refs import collect_config_entity_ids
from zigporter.ha_client import HAClient
from zigporter.models import AutomationRef, ZHADevice, ZHAEntity, ZHAExport

console = Console()


def _build_area_map(areas: list[dict[str, Any]]) -> dict[str, str]:
    """Return {area_id: area_name}."""
    return {a["area_id"]: a["name"] for a in areas}


def _build_entity_map(
    entity_registry: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Return {device_id: [entity_registry_entry, ...]} for ZHA entities only."""
    result: dict[str, list[dict[str, Any]]] = {}
    for entry in entity_registry:
        if entry.get("platform") != "zha":
            continue
        device_id = entry.get("device_id")
        if not device_id:
            continue
        result.setdefault(device_id, []).append(entry)
    return result


def _build_state_map(states: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return {entity_id: state_entry}."""
    return {s["entity_id"]: s for s in states}


def _extract_entity_ids_from_automation(config: dict[str, Any]) -> list[str]:
    """Walk an automation config dict and collect all entity_id values."""
    return sorted(collect_config_entity_ids(config))


def _match_automations_to_devices(
    automation_configs: list[dict[str, Any]],
    entity_to_device: dict[str, str],
) -> dict[str, list[AutomationRef]]:
    """Return {device_id: [AutomationRef, ...]}."""
    result: dict[str, list[AutomationRef]] = {}

    for config in automation_configs:
        auto_id = config.get("id", "")
        alias = config.get("alias", auto_id)
        entity_ids = _extract_entity_ids_from_automation(config)

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


def build_export(
    zha_devices: list[dict[str, Any]],
    device_registry: list[dict[str, Any]],
    entity_registry: list[dict[str, Any]],
    area_registry: list[dict[str, Any]],
    states: list[dict[str, Any]],
    automation_configs: list[dict[str, Any]],
    ha_url: str,
) -> ZHAExport:
    """Join all data sources into a ZHAExport."""
    area_map = _build_area_map(area_registry)
    entity_map = _build_entity_map(entity_registry)
    state_map = _build_state_map(states)

    # Build device_id -> area_id from device registry
    dr_map: dict[str, dict[str, Any]] = {d["id"]: d for d in device_registry}

    # Build entity_id -> device_id for automation matching
    entity_to_device: dict[str, str] = {
        e["entity_id"]: e["device_id"]
        for e in entity_registry
        if e.get("platform") == "zha" and e.get("device_id")
    }

    auto_map = _match_automations_to_devices(automation_configs, entity_to_device)

    devices: list[ZHADevice] = []

    for zha_dev in zha_devices:
        ieee = zha_dev.get("ieee", "")
        device_id = zha_dev.get("device_reg_id", "")

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
                    platform="zha",
                    unique_id=entry.get("unique_id"),
                    device_class=entry.get("device_class"),
                    disabled=entry.get("disabled_by") is not None,
                    state=state_entry.get("state"),
                    attributes=attrs,
                )
            )

        devices.append(
            ZHADevice(
                device_id=device_id,
                ieee=ieee,
                name=zha_dev.get("user_given_name") or zha_dev.get("name", ieee),
                name_by_user=zha_dev.get("user_given_name"),
                manufacturer=zha_dev.get("manufacturer"),
                model=zha_dev.get("model"),
                area_id=area_id,
                area_name=area_name,
                device_type=zha_dev.get("device_type", "Unknown"),
                quirk_applied=zha_dev.get("quirk_applied", False),
                quirk_class=zha_dev.get("quirk_class"),
                entities=entities,
                automations=auto_map.get(device_id, []),
            )
        )

    return ZHAExport(
        exported_at=datetime.now(tz=timezone.utc),
        ha_url=ha_url,
        devices=devices,
    )


async def run_export(ha_url: str, token: str, verify_ssl: bool) -> ZHAExport:
    """Fetch all data from HA and build the export."""
    client = HAClient(ha_url, token, verify_ssl)

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
        t = progress.add_task("Connecting to Home Assistant...", total=None)

        progress.update(t, description="Fetching ZHA + registry data...")
        ws_data = await client.get_all_ws_data()

        progress.update(t, description="Fetching entity states...")
        states = await client.get_states()

        progress.update(t, description="Building device map...")
        export = build_export(
            zha_devices=ws_data["zha_devices"],
            device_registry=ws_data["device_registry"],
            entity_registry=ws_data["entity_registry"],
            area_registry=ws_data["area_registry"],
            states=states,
            automation_configs=ws_data["automation_configs"],
            ha_url=ha_url,
        )
        progress.stop()

    return export


def export_command(
    output: Path,
    pretty: bool,
    ha_url: str,
    token: str,
    verify_ssl: bool,
) -> None:
    """Entry point called from the CLI."""
    export = asyncio.run(run_export(ha_url, token, verify_ssl))

    indent = 2 if pretty else None
    output.write_text(export.model_dump_json(indent=indent))

    console.print(
        f"\nExport complete: [bold]{len(export.devices)}[/bold] devices, "
        f"[bold]{sum(len(d.entities) for d in export.devices)}[/bold] entities, "
        f"[bold]{sum(len(d.automations) for d in export.devices)}[/bold] automation references\n"
        f"Written to [green]{output}[/green]"
    )
