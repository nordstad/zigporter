"""fix-device command — clean up stale ZHA device entries left after migration to Z2M."""

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

import questionary
from rich.console import Console
from rich.table import Table

from zigporter.ha_client import HAClient
from zigporter.utils import normalize_ieee

console = Console()

_STYLE = questionary.Style(
    [
        ("qmark", "fg:ansicyan bold"),
        ("question", "bold"),
        ("answer", "fg:ansicyan bold"),
        ("pointer", "fg:ansicyan bold"),
        ("highlighted", "fg:ansicyan bold"),
        ("selected", "fg:ansicyan"),
        ("separator", "fg:ansibrightblack"),
        ("instruction", "fg:ansibrightblack"),
        ("text", ""),
        ("disabled", "fg:ansibrightblack italic"),
    ]
)

_NUMERIC_SUFFIX_PAT = re.compile(r"^(.+)_\d+$")


def _ieee_colon(normalized: str) -> str:
    """Convert 16-char normalized hex IEEE to colon-separated format for ZHA services."""
    return ":".join(normalized[i : i + 2] for i in range(0, 16, 2))


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class StalePair:
    """A device that appears as both a stale ZHA entry and an active Z2M entry in HA."""

    ieee: str  # normalized 16-char hex
    name: str  # display name (from ZHA or Z2M device entry)
    zha_device_id: str  # HA device registry ID for the stale ZHA entry
    z2m_device_id: str  # HA device registry ID for the active Z2M/MQTT entry
    stale_entity_ids: list[str] = field(default_factory=list)  # entities on ZHA device
    suffix_renames: list[tuple[str, str]] = field(
        default_factory=list
    )  # (z2m_id_with_suffix, base_id)


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------


def _zha_ieee(device_entry: dict[str, Any]) -> str | None:
    """Return normalized IEEE if this HA device entry belongs to ZHA, else None."""
    for platform, identifier in device_entry.get("identifiers", []):
        if platform == "zha":
            return normalize_ieee(identifier)
    return None


def _mqtt_ieee(device_entry: dict[str, Any]) -> str | None:
    """Return normalized IEEE if this HA device entry belongs to a Z2M MQTT device, else None."""
    for platform, identifier in device_entry.get("identifiers", []):
        if platform != "mqtt":
            continue
        ident = identifier.lower()
        if ident.startswith("zigbee2mqtt_"):
            ident = ident[len("zigbee2mqtt_") :]
        if ident.startswith("0x"):
            ident = ident[2:]
        return ident.zfill(16)
    return None


def _device_display_name(entry: dict[str, Any]) -> str:
    return entry.get("name_by_user") or entry.get("name") or entry.get("id", "?")


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def find_stale_pairs(
    device_registry: list[dict[str, Any]],
    entity_registry: list[dict[str, Any]],
) -> list[StalePair]:
    """Scan device/entity registries and return devices with both a stale ZHA and active Z2M entry."""
    zha_by_ieee: dict[str, dict[str, Any]] = {}
    z2m_by_ieee: dict[str, dict[str, Any]] = {}

    for entry in device_registry:
        ieee = _zha_ieee(entry)
        if ieee:
            zha_by_ieee[ieee] = entry
        ieee = _mqtt_ieee(entry)
        if ieee:
            z2m_by_ieee[ieee] = entry

    entity_by_device: dict[str, list[dict[str, Any]]] = {}
    for e in entity_registry:
        did = e.get("device_id")
        if did:
            entity_by_device.setdefault(did, []).append(e)

    pairs: list[StalePair] = []
    for ieee, zha_entry in zha_by_ieee.items():
        z2m_entry = z2m_by_ieee.get(ieee)
        if not z2m_entry:
            continue  # no Z2M counterpart — not our concern

        zha_did = zha_entry["id"]
        z2m_did = z2m_entry["id"]
        stale_entities = entity_by_device.get(zha_did, [])
        z2m_entities = entity_by_device.get(z2m_did, [])

        # Build map of base_id → stale entity for suffix-conflict detection
        stale_by_id = {e["entity_id"]: e for e in stale_entities}

        suffix_renames: list[tuple[str, str]] = []
        for z2m_e in z2m_entities:
            eid = z2m_e["entity_id"]
            m = _NUMERIC_SUFFIX_PAT.match(eid)
            if not m:
                continue
            base_id = m.group(1)
            if base_id in stale_by_id:
                suffix_renames.append((eid, base_id))

        pairs.append(
            StalePair(
                ieee=ieee,
                name=_device_display_name(z2m_entry),
                zha_device_id=zha_did,
                z2m_device_id=z2m_did,
                stale_entity_ids=[e["entity_id"] for e in stale_entities],
                suffix_renames=suffix_renames,
            )
        )

    return pairs


async def apply_fix(pair: StalePair, ha_client: HAClient) -> None:
    """Delete stale ZHA entities + device, then rename _2 Z2M entities."""
    # 1. Delete stale ZHA entities
    for eid in pair.stale_entity_ids:
        try:
            await ha_client.delete_entity(eid)
            console.print(f"  [green]✓[/green] Deleted stale entity  [dim]{eid}[/dim]")
        except Exception as exc:
            console.print(f"  [yellow]Warning:[/yellow] Could not delete {eid}: {exc}")

    # 2. Remove the stale ZHA device entry.
    # Try the device registry API first; if unsupported fall back to the ZHA service which
    # forces the ZHA integration to clean up its own device record (and the HA entry with it).
    removed = False
    try:
        await ha_client.remove_device(pair.zha_device_id)
        console.print(
            f"  [green]✓[/green] Removed stale ZHA device entry  [dim]{pair.zha_device_id}[/dim]"
        )
        removed = True
    except RuntimeError as exc:
        if "unknown_command" not in str(exc):
            console.print(f"  [yellow]Warning:[/yellow] Could not remove ZHA device entry: {exc}")

    if not removed:
        try:
            await ha_client.remove_zha_device(_ieee_colon(pair.ieee))
            console.print("  [green]✓[/green] Removed stale ZHA device entry via ZHA service")
            removed = True
        except Exception as exc:
            console.print(f"  [yellow]Warning:[/yellow] Could not remove via ZHA service: {exc}")

    if not removed:
        console.print(
            "  [dim]ℹ Ghost device entry will be removed automatically on HA restart.[/dim]"
        )

    # 3. Rename _2 Z2M entities to their original names
    for z2m_id, base_id in pair.suffix_renames:
        try:
            await ha_client.rename_entity_id(z2m_id, base_id)
            console.print(f"  [green]✓[/green] {z2m_id} → [bold]{base_id}[/bold]")
        except Exception as exc:
            console.print(f"  [yellow]Warning:[/yellow] Could not rename {z2m_id}: {exc}")


# ---------------------------------------------------------------------------
# Interactive flow
# ---------------------------------------------------------------------------


def _show_plan(pair: StalePair) -> None:
    console.print(f"\n  Device: [bold]{pair.name}[/bold]  [dim](IEEE {pair.ieee})[/dim]\n")

    if pair.stale_entity_ids or pair.suffix_renames:
        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        table.add_column("Action")
        table.add_column("Entity ID")

        for eid in pair.stale_entity_ids:
            table.add_row("[red]delete[/red]", f"[dim]{eid}[/dim]")
        for z2m_id, base_id in pair.suffix_renames:
            table.add_row("[cyan]rename[/cyan]", f"[dim]{z2m_id}[/dim] → [bold]{base_id}[/bold]")

        console.print(table)
        console.print()
    else:
        console.print(
            "  [dim]No entity changes needed — only the stale ZHA device entry will be removed.[/dim]\n"
        )


async def run_fix_device(ha_url: str, token: str, verify_ssl: bool) -> None:
    ha_client = HAClient(ha_url, token, verify_ssl)

    console.print("Fetching registry data from Home Assistant...", end=" ")
    device_registry, entity_registry = await asyncio.gather(
        ha_client.get_device_registry(),
        ha_client.get_entity_registry(),
    )
    console.print("[green]✓[/green]\n")

    pairs = find_stale_pairs(device_registry, entity_registry)

    if not pairs:
        console.print(
            "[green]✓ No stale ZHA device entries found.[/green]\n"
            "  All devices with a Z2M counterpart have already been cleaned up."
        )
        return

    if len(pairs) == 1:
        pair = pairs[0]
    else:
        choices = [questionary.Choice(f"{p.name}  ({p.ieee})", value=p) for p in pairs]
        try:
            pair = await questionary.select(
                f"Found {len(pairs)} devices with stale ZHA entries. Which would you like to fix?",
                choices=choices,
                style=_STYLE,
            ).unsafe_ask_async()
        except KeyboardInterrupt:
            return

    _show_plan(pair)

    confirmed = await questionary.confirm(
        "Apply fix?", default=True, style=_STYLE
    ).unsafe_ask_async()
    if not confirmed:
        console.print("[yellow]Aborted.[/yellow]")
        return

    console.print()
    await apply_fix(pair, ha_client)
    console.print("\n[green]✓ Done.[/green]  Reload the HA page to confirm the device is clean.")


def fix_device_command(ha_url: str, token: str, verify_ssl: bool) -> None:
    asyncio.run(run_fix_device(ha_url, token, verify_ssl))
