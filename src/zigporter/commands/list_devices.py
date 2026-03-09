import asyncio
from typing import Any

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from zigporter.ha_client import HAClient
from zigporter.utils import device_display_name

console = Console()


def _integration_label(device: dict[str, Any]) -> str:
    """Return a short integration label derived from the device's identifiers."""
    for pair in device.get("identifiers", []):
        if len(pair) < 1:
            continue
        domain = pair[0]
        if domain == "zha":
            return "zha"
        if domain == "mqtt" and len(pair) == 2 and str(pair[1]).lower().startswith("zigbee2mqtt_"):
            return "z2m"
        if domain == "matter":
            return "matter"
        if domain == "zwave_js":
            return "zwave"
        # First identifier domain as a readable fallback
        return domain
    return ""


async def run_list_devices(ha_url: str, token: str, verify_ssl: bool) -> None:
    client = HAClient(ha_url, token, verify_ssl)

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
        t = progress.add_task("Fetching HA device registry...", total=None)
        devices, areas = await asyncio.gather(
            client.get_device_registry(),
            client.get_area_registry(),
        )
        progress.update(t, description="Done")

    area_names: dict[str, str] = {a["area_id"]: a["name"] for a in areas}

    # Drop system/internal entries that have no human name
    named = [d for d in devices if d.get("name_by_user") or d.get("name")]

    named.sort(
        key=lambda d: (
            area_names.get(d.get("area_id") or "", "zzz").lower(),
            device_display_name(d).lower(),
        )
    )

    table = Table(title=f"Home Assistant Devices ({len(named)})", show_header=True)
    table.add_column("Name", no_wrap=True)
    table.add_column("Area")
    table.add_column("Integration")
    table.add_column("Manufacturer")
    table.add_column("Model")

    for d in named:
        name = device_display_name(d)
        area = area_names.get(d.get("area_id") or "", "")
        integration = _integration_label(d)
        manufacturer = d.get("manufacturer") or ""
        model = d.get("model") or ""
        table.add_row(name, area, integration, manufacturer, model)

    console.print(table)


def list_devices_command(ha_url: str, token: str, verify_ssl: bool) -> None:
    asyncio.run(run_list_devices(ha_url, token, verify_ssl))
