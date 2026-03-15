"""Reverse migration wizard: Z2M -> ZHA.

Mirrors the forward wizard in ``migrate.py`` but reverses the direction:
devices are removed from Z2M and paired with ZHA.
"""

import asyncio
import re
from pathlib import Path
from typing import Any

import questionary
from rich.console import Console
from rich.table import Table

from zigporter.commands.rename_device import (
    build_device_rename_plan,
    compute_entity_pairs,
    display_device_plan,
    execute_device_rename,
    resolve_odd_entities,
    slugify,
)
from zigporter.ha_client import HAClient
from zigporter.migration_state import (
    DeviceStatus,
    MigrationState,
    load_state,
    mark_failed,
    mark_in_progress,
    mark_migrated_reverse,
    mark_pending,
    save_state,
)
from zigporter.models import Z2MDevice, Z2MExport
from zigporter.ui import QUESTIONARY_STYLE
from zigporter.utils import normalize_ieee
from zigporter.z2m_client import Z2MClient

console = Console()

_STYLE = QUESTIONARY_STYLE

WIZARD_STEPS = 8

# Zigbee PermitJoin duration is an 8-bit field; 255 = always-on, so 254 is the
# practical maximum for a timed window.
_PERMIT_JOIN_MAX = 254

_STATUS_ICON = {
    DeviceStatus.PENDING: "○",
    DeviceStatus.IN_PROGRESS: "◌",
    DeviceStatus.MIGRATED: "✓",
    DeviceStatus.FAILED: "✗",
}

_STATUS_STYLE = {
    DeviceStatus.PENDING: "white",
    DeviceStatus.IN_PROGRESS: "yellow",
    DeviceStatus.MIGRATED: "green",
    DeviceStatus.FAILED: "red",
}


# ---------------------------------------------------------------------------
# Status display
# ---------------------------------------------------------------------------


def show_status(export: Z2MExport, state: MigrationState) -> None:
    device_map = {normalize_ieee(d.ieee): d for d in export.devices}
    table = Table(title="Reverse Migration Progress (Z2M → ZHA)", show_header=True)
    table.add_column("", width=2)
    table.add_column("Device")
    table.add_column("Model")
    table.add_column("Area")
    table.add_column("Status")
    table.add_column("Migrated at")

    for ieee, dev_state in state.devices.items():
        device = device_map.get(normalize_ieee(ieee))
        icon = _STATUS_ICON[dev_state.status]
        style = _STATUS_STYLE[dev_state.status]
        model = device.model or "" if device else ""
        area = device.area_name or "" if device else ""
        migrated_at = (
            dev_state.migrated_at.strftime("%Y-%m-%d %H:%M") if dev_state.migrated_at else ""
        )
        offline_suffix = " [dim](offline)[/dim]" if device and device.available is False else ""
        table.add_row(
            f"[{style}]{icon}[/{style}]",
            f"[{style}]{dev_state.name}[/{style}]{offline_suffix}",
            model,
            area,
            f"[{style}]{dev_state.status.value}[/{style}]",
            migrated_at,
        )

    console.print(table)

    migrated = sum(1 for d in state.devices.values() if d.status == DeviceStatus.MIGRATED)
    total = len(state.devices)
    console.print(f"\n[bold]{migrated} / {total}[/bold] devices migrated")


# ---------------------------------------------------------------------------
# Device picker
# ---------------------------------------------------------------------------


def pick_device(export: Z2MExport, state: MigrationState) -> Z2MDevice | None:
    pending = [
        d
        for d in export.devices
        if state.devices.get(normalize_ieee(d.ieee), None) is None
        or state.devices[normalize_ieee(d.ieee)].status
        in (DeviceStatus.PENDING, DeviceStatus.FAILED, DeviceStatus.IN_PROGRESS)
    ]

    if not pending:
        console.print("[green]All devices have been migrated![/green]")
        return None

    pending.sort(key=lambda d: (d.area_name or "\xff", d.available is False, d.friendly_name))

    choices: list = []
    current_area: str | None = object()  # type: ignore[assignment]
    for device in pending:
        area = device.area_name or ""
        if area != current_area:
            current_area = area
            heading = f" {area or 'No area'} "
            choices.append(
                questionary.Separator(f"{'─' * 4}{heading}{'─' * max(0, 48 - len(heading))}")
            )

        dev_state = state.devices.get(normalize_ieee(device.ieee))
        status = dev_state.status if dev_state else DeviceStatus.PENDING
        offline_tag = "  (offline)" if device.available is False else ""
        suffix = (
            "  ◌ in progress"
            if status == DeviceStatus.IN_PROGRESS
            else ("  ✗ failed" if status == DeviceStatus.FAILED else "")
        )
        label = f"  {device.friendly_name:<40} {device.model or ''}{offline_tag}{suffix}"
        choices.append(questionary.Choice(title=label, value=device))

    selected = questionary.select(
        "Select a device to migrate back to ZHA:",
        choices=choices,
        use_indicator=True,
        style=_STYLE,
    ).ask()

    return selected


# ---------------------------------------------------------------------------
# Wizard steps
# ---------------------------------------------------------------------------


def _print_step(n: int, title: str) -> None:
    console.print(f"\n[bold cyan][{n}/{WIZARD_STEPS}] {title}[/bold cyan]")


async def step_remove_from_z2m(device: Z2MDevice, z2m_client: Z2MClient) -> bool:
    """Step 1: Remove device from Z2M via MQTT."""
    _print_step(1, "Remove from Zigbee2MQTT")

    console.print(
        f"\n  Device:       [bold]{device.friendly_name}[/bold]\n"
        f"  IEEE:         [dim]{device.ieee}[/dim]\n"
        f"  Manufacturer: {device.manufacturer or 'unknown'}\n"
        f"  Model:        {device.model or 'unknown'}\n"
    )
    confirmed = await questionary.confirm(
        f'Remove "{device.friendly_name}" from Z2M?', default=False, style=_STYLE
    ).unsafe_ask_async()
    if not confirmed:
        console.print("[yellow]Aborted.[/yellow]")
        return False

    console.print(f"\nRemoving [bold]{device.friendly_name}[/bold] from Z2M...", end=" ")

    try:
        await z2m_client.remove_device(device.friendly_name, force=True)
        console.print("[green]✓ Removal command sent[/green]")
    except (RuntimeError, OSError) as exc:
        console.print(f"[yellow]Could not remove automatically: {exc}[/yellow]")
        console.print(
            "\nRemove the device manually in Zigbee2MQTT:\n"
            "  [bold]Z2M Dashboard → Devices → Select device → Delete[/bold]\n"
        )
        await questionary.press_any_key_to_continue(
            "Press Enter when the device is deleted...", style=_STYLE
        ).unsafe_ask_async()

    console.print("[green]✓ Device removal initiated[/green]")
    return True


async def step_reset_device(device: Z2MDevice) -> None:
    """Step 2: Prompt user to factory-reset the physical device."""
    _print_step(2, "Reset physical device")
    model = device.model or "your device"
    console.print(
        f"\nFactory-reset the [bold]{device.friendly_name}[/bold] to clear its old pairing.\n"
        f"  Manufacturer: {device.manufacturer or 'unknown'}\n"
        f"  Model:        {model}\n"
    )
    if device.model:
        slug = device.model.replace(" ", "_").replace("/", "_")
        console.print(
            f"  Model-specific reset instructions:\n"
            f"  [blue]https://www.zigbee2mqtt.io/devices/{slug}.html[/blue]\n"
        )
    await questionary.press_any_key_to_continue(
        "Press Enter when the device has been reset...", style=_STYLE
    ).unsafe_ask_async()


async def _permit_join_refresh_loop(ha_client: HAClient) -> None:
    """Re-open ZHA permit join every 244 s so the Zigbee 254-second window never expires."""
    while True:
        await asyncio.sleep(_PERMIT_JOIN_MAX - 10)
        try:
            await ha_client.enable_zha_permit_join(duration=_PERMIT_JOIN_MAX)
        except (RuntimeError, OSError):
            pass


async def step_pair_with_zha(
    device: Z2MDevice,
    ha_client: HAClient,
    timeout: int = 300,
) -> dict[str, Any] | None:
    """Step 3: Open ZHA network and poll for device to appear."""
    _print_step(3, "Pair with ZHA")

    pj_secs = min(timeout, _PERMIT_JOIN_MAX)
    console.print("\nEnabling ZHA permit join...")
    try:
        await ha_client.enable_zha_permit_join(duration=pj_secs)
        console.print("[green]✓ ZHA permit join enabled[/green]")
    except (RuntimeError, OSError) as exc:
        console.print(f"[red]Could not enable permit join: {exc}[/red]")
        return None

    console.print(f"\nTrigger [bold]{device.friendly_name}[/bold] to join now...")
    console.print(f"Waiting for device [dim]{device.ieee}[/dim] to appear in ZHA...\n")

    target_ieee = normalize_ieee(device.ieee)

    # Snapshot current ZHA devices
    try:
        known_iees: set[str] = {
            normalize_ieee(d.get("ieee", "")) for d in await ha_client.get_zha_devices()
        }
    except (RuntimeError, OSError):
        known_iees = set()

    loop = asyncio.get_running_loop()
    start_mono = loop.time()

    async def _ticker() -> None:
        while True:
            remaining = max(0, timeout - int(loop.time() - start_mono))
            console.print(
                f"  ⠸ Polling ZHA for new device  [{remaining:03d}s remaining]",
                end="\r",
            )
            await asyncio.sleep(1)

    pj_task = asyncio.create_task(_permit_join_refresh_loop(ha_client))
    ticker_task = asyncio.create_task(_ticker())

    found_device: dict[str, Any] | None = None
    poll_interval = 3

    try:
        elapsed = 0
        while elapsed < timeout:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            try:
                zha_devices = await ha_client.get_zha_devices()
            except (RuntimeError, OSError):
                continue

            for dev in zha_devices:
                dev_ieee = normalize_ieee(dev.get("ieee", ""))
                if dev_ieee == target_ieee:
                    found_device = dev
                    break
                if dev_ieee and dev_ieee not in known_iees:
                    known_iees.add(dev_ieee)
                    console.print(
                        f"\n  [yellow]⚠ A different device joined ZHA:[/yellow] "
                        f"[dim]{dev.get('ieee')}[/dim] "
                        f"([bold]{dev.get('user_given_name') or dev.get('name', '?')}[/bold])\n"
                        f"  This is NOT [bold]{device.friendly_name}[/bold]. "
                        f"Put the correct device in pairing mode."
                    )

            if found_device:
                break
    finally:
        pj_task.cancel()
        ticker_task.cancel()
        await asyncio.gather(pj_task, ticker_task, return_exceptions=True)

    if found_device:
        console.print("\n[green]✓ Device found in ZHA![/green]")
        dev_name = (
            found_device.get("user_given_name")
            or found_device.get("name")
            or normalize_ieee(device.ieee)
        )
        console.print(f"[green]✓ ZHA device:[/green] [bold]{dev_name}[/bold]")
        return found_device

    # Timeout
    console.print(f"\n[yellow]Timed out waiting for[/yellow] [dim]{device.ieee}[/dim]")
    choice = await questionary.select(
        "How would you like to proceed?",
        choices=[
            questionary.Choice("Retry — poll for another 5 minutes", value="retry"),
            questionary.Choice("Mark as failed — revisit this device later", value="fail"),
        ],
        style=_STYLE,
    ).unsafe_ask_async()

    if choice == "retry":
        return await step_pair_with_zha(device, ha_client, timeout)
    return None


async def step_rename_and_area(
    device: Z2MDevice,
    zha_device: dict[str, Any],
    ha_client: HAClient,
) -> bool:
    """Step 4: Restore device name and area in HA."""
    _print_step(4, "Rename & area")

    # Get HA device_id for the new ZHA device
    target_name = device.name_by_user or device.friendly_name
    zha_device_id = await _wait_for_zha_device_in_ha(device, ha_client, timeout=30)

    if zha_device_id:
        current_name = zha_device.get("user_given_name") or zha_device.get("name", "")
        if current_name != target_name:
            console.print(f"\n  ZHA assigned:   [dim]{current_name}[/dim]")
            console.print(f"  Will rename to: [bold]{target_name}[/bold]\n")

            confirm = await questionary.confirm(
                f'Apply rename to "{target_name}"?', default=True, style=_STYLE
            ).unsafe_ask_async()
            if confirm:
                try:
                    await ha_client.rename_device_name(zha_device_id, target_name)
                    console.print(f"[green]✓ Renamed to {target_name}[/green]")
                except (RuntimeError, OSError) as exc:
                    console.print(f"[red]Rename failed: {exc}[/red]")
                    return False
            else:
                console.print("[yellow]Rename skipped.[/yellow]")
                return False
        else:
            console.print(f"[green]✓ Already named correctly:[/green] {target_name}")
    else:
        console.print(
            "[yellow]Note:[/yellow] ZHA device not yet in HA registry — name/area skipped."
        )
        return True

    await _step_assign_area(device, ha_client, zha_device_id)
    return True


async def _prompt_area(device: Z2MDevice, areas: list[dict[str, Any]]) -> str | None:
    """Show an area picker and return the chosen area_id, or None for no area."""
    _NO_AREA = "__no_area__"

    areas_sorted = sorted(areas, key=lambda a: a.get("name", "").lower())
    original_area = next((a for a in areas_sorted if a.get("area_id") == device.area_id), None)

    if device.area_name:
        if original_area:
            console.print(f"\n  Area (from Z2M): [bold]{device.area_name}[/bold]")
        else:
            console.print(
                f"\n  Area (from Z2M): [dim]{device.area_name}[/dim] "
                f"[yellow](no longer exists in HA)[/yellow]"
            )
    else:
        console.print("\n  No area was assigned in Z2M.")

    choices = [
        questionary.Choice(title=a.get("name", a["area_id"]), value=a["area_id"])
        for a in areas_sorted
    ]
    choices.append(questionary.Choice(title="── No area ──", value=_NO_AREA))

    default_value = original_area["area_id"] if original_area else _NO_AREA

    selected = await questionary.select(
        "Assign area:",
        choices=choices,
        default=default_value,
        style=_STYLE,
    ).unsafe_ask_async()

    return None if selected == _NO_AREA else selected


async def _step_assign_area(device: Z2MDevice, ha_client: HAClient, zha_device_id: str) -> None:
    """Prompt for area assignment and apply it to the ZHA device in HA."""
    try:
        areas = await ha_client.get_area_registry()
    except (RuntimeError, OSError) as exc:
        console.print(f"[yellow]Could not fetch area list: {exc}[/yellow]")
        if device.area_id:
            try:
                await ha_client.update_device_area(zha_device_id, device.area_id)
                console.print(f"[green]✓ Area restored: {device.area_name}[/green]")
            except (RuntimeError, OSError):
                console.print(
                    f"[yellow]Note:[/yellow] Could not set area. "
                    f"Set [bold]{device.area_name}[/bold] manually in HA."
                )
        return

    chosen_area_id = await _prompt_area(device, areas)

    area_id_to_set = chosen_area_id or ""
    try:
        await ha_client.update_device_area(zha_device_id, area_id_to_set)
        if chosen_area_id:
            area_name = next(
                (a["name"] for a in areas if a.get("area_id") == chosen_area_id), chosen_area_id
            )
            console.print(f"[green]✓ Area set: {area_name}[/green]")
        elif device.area_id:
            console.print("[green]✓ Area cleared[/green]")
    except (RuntimeError, OSError) as exc:
        console.print(f"[yellow]Could not set area: {exc}[/yellow]")


_NUMERIC_SUFFIX_PAT = re.compile(r"^(.+)_\d+$")


async def _wait_for_zha_device_in_ha(
    device: Z2MDevice, ha_client: HAClient, timeout: int = 90
) -> str | None:
    """Poll the HA device registry until the ZHA device appears, up to *timeout* seconds."""
    elapsed = 0
    poll_interval = 3
    while elapsed < timeout:
        zha_device_id = await ha_client.get_zha_device_id(device.ieee)
        if zha_device_id:
            return zha_device_id
        remaining = timeout - elapsed
        console.print(
            f"  Waiting for HA to register ZHA device... [{remaining:02d}s remaining]",
            end="\r",
        )
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    console.print()  # clear the \r line
    return None


async def step_reconcile_entity_ids(device: Z2MDevice, ha_client: HAClient) -> None:
    """Step 5: Restore entity IDs — resolve stale MQTT entities and _2/_3 suffix conflicts."""
    _print_step(5, "Restore entity IDs")

    zha_device_id = await _wait_for_zha_device_in_ha(device, ha_client)
    if zha_device_id is None:
        console.print(
            "[yellow]ZHA device did not appear in HA registry within the wait window; "
            "skipping entity ID restore.[/yellow]"
        )
        return

    full_registry = await ha_client.get_entity_registry()
    zha_entities = [e for e in full_registry if e.get("device_id") == zha_device_id]

    # Index the full registry by entity_id so we can detect stale conflicts.
    registry_by_id = {e["entity_id"]: e for e in full_registry}

    # --- Numeric-suffix conflicts ---
    # When stale MQTT entities still occupy the original entity IDs, ZHA entities
    # get _2/_3/etc. suffixes. We detect and resolve this.
    suffix_resolvable: list[tuple[str, str]] = []  # (zha_id_with_suffix, base_id)
    for entry in zha_entities:
        current_id = entry["entity_id"]
        m = _NUMERIC_SUFFIX_PAT.match(current_id)
        if not m:
            continue
        base_id = m.group(1)
        stale = registry_by_id.get(base_id)
        if stale and stale.get("device_id") != zha_device_id:
            suffix_resolvable.append((current_id, base_id))

    if not suffix_resolvable:
        console.print("[green]✓ Entity IDs already use expected names[/green]")
        return

    console.print(
        "\n  [yellow]ZHA entities have a numeric suffix because stale MQTT entities still "
        "occupy the original IDs.[/yellow]\n"
        "  Deleting the stale entries restores original entity IDs and fixes your dashboards:\n"
    )
    for zha_id, base_id in suffix_resolvable:
        console.print(f"  [dim]{zha_id}[/dim]")
        console.print(
            f"    → [bold]{base_id}[/bold]  [dim](stale MQTT entity will be deleted)[/dim]"
        )
    console.print()

    confirmed = await questionary.confirm(
        "Delete stale MQTT entities and restore original entity IDs?",
        default=True,
        style=_STYLE,
    ).unsafe_ask_async()
    if not confirmed:
        console.print("[yellow]Suffix conflict resolution skipped.[/yellow]")
        return

    for zha_id, base_id in suffix_resolvable:
        try:
            await ha_client.delete_entity(base_id)
        except (RuntimeError, OSError) as exc:
            console.print(f"[yellow]Warning:[/yellow] Could not delete stale {base_id}: {exc}")
            continue
        try:
            await ha_client.rename_entity_id(zha_id, base_id)
            console.print(f"[green]✓[/green] {zha_id} → {base_id}")
        except (RuntimeError, OSError) as exc:
            console.print(f"[yellow]Warning:[/yellow] Could not rename {zha_id}: {exc}")


async def step_show_review(device: Z2MDevice, ha_client: HAClient) -> None:
    """Step 6: Show entity and dashboard summary."""
    _print_step(6, "Review entities & dashboards")

    zha_device_id = await ha_client.get_zha_device_id(device.ieee)
    if zha_device_id is None:
        console.print("[yellow]ZHA device not in HA registry — skipping review.[/yellow]")
        return

    full_registry = await ha_client.get_entity_registry()
    entity_ids = [
        e["entity_id"]
        for e in full_registry
        if e.get("device_id") == zha_device_id and not e.get("disabled_by")
    ]
    if not entity_ids:
        console.print("[dim]No entities registered yet.[/dim]")
        return

    console.print(f"\n  ZHA entities for [bold]{device.friendly_name}[/bold]:")
    for eid in entity_ids:
        console.print(f"  [green]•[/green] {eid}")


async def step_validate(device: Z2MDevice, ha_client: HAClient, retries: int = 10) -> bool:
    """Step 7: Validate ZHA entities come online."""
    _print_step(7, "Validate")

    zha_device_id = await _wait_for_zha_device_in_ha(device, ha_client)
    if zha_device_id is None:
        console.print(
            "[yellow]ZHA device did not appear in HA registry within the wait window; "
            "skipping validation.[/yellow]"
        )
        return True

    while True:
        console.print("\nWaiting for entities to come online...")

        results: list[tuple[str, str | None, str]] = []
        all_ok = False

        for attempt in range(retries):
            await asyncio.sleep(3)
            full_registry = await ha_client.get_entity_registry()
            expected_ids = [
                e["entity_id"]
                for e in full_registry
                if e.get("device_id") == zha_device_id and not e.get("disabled_by")
            ]
            states = await ha_client.get_states()
            state_map = {s["entity_id"]: s for s in states}

            results = []
            if expected_ids:
                for eid in expected_ids:
                    if eid not in state_map:
                        results.append((eid, None, "missing"))
                    else:
                        state_val = state_map[eid]["state"]
                        status = "ok" if state_val not in ("unavailable", "unknown") else "unknown"
                        results.append((eid, state_val, status))

                all_ok = all(s == "ok" for _, _, s in results)

            if all_ok:
                break

        if results:
            for eid, state_val, status in results:
                if status == "ok":
                    icon = "[green]✓[/green]"
                    state_display = f"[green]{state_val}[/green]"
                elif status == "unknown":
                    icon = "[yellow]~[/yellow]"
                    state_display = f"[dim]{state_val}[/dim]"
                else:
                    icon = "[red]![/red]"
                    state_display = "[red]missing[/red]"
                console.print(f"  {icon}  {eid:<50} {state_display}")
        else:
            console.print(
                "  [yellow]No entities registered in HA yet. ZHA may still be initialising."
                "[/yellow]"
            )

        if all_ok:
            return True

        choice = await questionary.select(
            "Some entities are not yet online. What would you like to do?",
            choices=[
                questionary.Choice("Retry — poll again", value="retry"),
                questionary.Choice(
                    "Accept — entities exist in HA, state will update soon", value="accept"
                ),
                questionary.Choice("Mark as failed — revisit this device later", value="fail"),
            ],
            style=_STYLE,
        ).unsafe_ask_async()

        if choice == "retry":
            continue
        return choice == "accept"


async def step_post_migrate_rename(
    device: Z2MDevice,
    ha_client: HAClient,
) -> None:
    """Step 8: Optional cascading rename after successful migration."""
    console.print(f"\n[bold cyan][8/{WIZARD_STEPS}] Rename (optional)[/bold cyan]")

    wants_rename = (
        await questionary.confirm(
            "Rename this device to a different name?",
            default=False,
            style=_STYLE,
        ).unsafe_ask_async()
        or False
    )
    if not wants_rename:
        return

    target_name = device.name_by_user or device.friendly_name
    new_name = await questionary.text(
        "New device name:",
        default=target_name,
        style=_STYLE,
    ).unsafe_ask_async()
    if not new_name or not new_name.strip():
        console.print("[dim]Rename skipped.[/dim]")
        return
    new_name = new_name.strip()

    if new_name == target_name:
        console.print("[dim]Name unchanged — skipping.[/dim]")
        return

    zha_device_id = await ha_client.get_zha_device_id(device.ieee)
    if not zha_device_id:
        console.print(
            "[yellow]Could not locate ZHA device in HA registry. Rename skipped.[/yellow]"
        )
        return

    console.print("  Fetching entities...", end=" ")
    entities = await ha_client.get_entities_for_device(zha_device_id)
    console.print(f"[green]{len(entities)} found[/green]")

    if not entities:
        console.print("[yellow]No entities found for this device — rename skipped.[/yellow]")
        return

    old_slug = slugify(target_name)
    new_slug = slugify(new_name)
    entity_pairs, odd_entities = compute_entity_pairs(entities, old_slug, new_slug)

    if odd_entities:
        entity_pairs = await resolve_odd_entities(odd_entities, entity_pairs, new_slug)

    if not entity_pairs:
        console.print("\n[yellow]No entities to rename.[/yellow]")
        return

    try:
        device_plan = await build_device_rename_plan(
            ha_client,
            {"id": zha_device_id},
            target_name,
            new_name,
            entity_pairs,
        )
    except ValueError:
        return

    display_device_plan(device_plan)

    confirmed = (
        await questionary.confirm(
            "Apply these changes?",
            default=False,
            style=_STYLE,
        ).unsafe_ask_async()
        or False
    )
    if not confirmed:
        console.print("[dim]Rename aborted.[/dim]")
        return

    console.print()
    await execute_device_rename(ha_client, device_plan)
    console.print(
        f"\n[green]✓[/green] Renamed device [bold]{target_name}[/bold]"
        f" → [bold cyan]{new_name}[/bold cyan]"
        f" ([bold]{len(device_plan.plans)}[/bold] entities updated)"
    )


# ---------------------------------------------------------------------------
# Main wizard orchestrator
# ---------------------------------------------------------------------------


def _device_display_name(device: Z2MDevice) -> str:
    return device.name_by_user or device.friendly_name


async def run_reverse_wizard(
    device: Z2MDevice,
    state: MigrationState,
    state_path: Path,
    ha_client: HAClient,
    z2m_client: Z2MClient,
) -> None:
    name = _device_display_name(device)
    console.rule(f"[bold]Reverse-migrating: {name}[/bold]")

    console.print(
        "\nThis wizard will migrate [bold]{name}[/bold] from Zigbee2MQTT back to ZHA "
        "in 7 steps (+ 1 optional):\n"
        "\n"
        "  [cyan]1[/cyan]  Remove from Z2M    Unpair the device from Zigbee2MQTT "
        "[red](cannot be undone)[/red]\n"
        "  [cyan]2[/cyan]  Reset device       Factory-reset to clear the old Z2M pairing\n"
        "  [cyan]3[/cyan]  Pair with ZHA      Put device in pairing mode and join ZHA\n"
        "  [cyan]4[/cyan]  Rename & area      Restore name and choose an area in HA\n"
        "  [cyan]5[/cyan]  Restore entity IDs Resolve stale MQTT entities and _2/_3 suffixes\n"
        "  [cyan]6[/cyan]  Review             Inspect entities\n"
        "  [cyan]7[/cyan]  Validate           Confirm entities come back online\n"
        "  [cyan]8[/cyan]  Rename (optional)  Rename to a different name with full HA cascade\n"
        "\n"
        "[dim]You can abort safely at step 1. After that, the device must complete\n"
        "pairing with ZHA before it can be used again.[/dim]".format(name=name)
    )

    ieee_key = normalize_ieee(device.ieee)
    mark_in_progress(state, ieee_key)
    save_state(state, state_path)

    try:
        # Step 1 — Remove from Z2M
        removed = await step_remove_from_z2m(device, z2m_client)
        if not removed:
            console.print("[yellow]Migration cancelled.[/yellow]")
            mark_pending(state, ieee_key)
            save_state(state, state_path)
            return

        # Step 2 — Reset physical device
        await step_reset_device(device)

        # Step 3 — Pair with ZHA
        zha_device = await step_pair_with_zha(device, ha_client)
        if zha_device is None:
            console.print("[red]Pairing failed. Device marked as failed.[/red]")
            mark_failed(state, ieee_key)
            save_state(state, state_path)
            return

        # Step 4 — Rename & area
        await step_rename_and_area(device, zha_device, ha_client)

        # Step 5 — Restore entity IDs
        await step_reconcile_entity_ids(device, ha_client)

        # Step 6 — Review
        await step_show_review(device, ha_client)

        # Step 7 — Validate
        valid = await step_validate(device, ha_client)

        if valid:
            device_name = _device_display_name(device)
            mark_migrated_reverse(state, ieee_key, device_name)
            save_state(state, state_path)
            migrated = sum(1 for d in state.devices.values() if d.status == DeviceStatus.MIGRATED)
            total = len(state.devices)
            console.print(
                f"\n[bold green]✓ Reverse migration complete![/bold green] "
                f"[bold]{device_name}[/bold] successfully migrated to ZHA.\n"
                f"Progress: [bold]{migrated}/{total}[/bold] devices migrated."
            )

            # Convert Z2MDevice entities to ZHADevice-like for test checklist
            _show_checklist_for_z2m_device(device)

            # Step 8 — Optional rename
            await step_post_migrate_rename(device, ha_client)
        else:
            mark_failed(state, ieee_key)
            save_state(state, state_path)
            console.print(
                "\n[yellow]Device marked as failed.[/yellow] "
                "Select it again from the menu to retry."
            )

    except KeyboardInterrupt:
        console.print("\n[yellow]Wizard interrupted. Progress saved.[/yellow]")
        mark_failed(state, ieee_key)
        save_state(state, state_path)


def _show_checklist_for_z2m_device(device: Z2MDevice) -> None:
    """Show automations that reference this device's entities."""
    if not device.automations:
        return

    console.print("\n")
    console.rule("[bold cyan]Post-migration test checklist[/bold cyan]")
    console.print("\n  [bold]Automations[/bold]")
    for auto in device.automations:
        console.print(f"  [cyan]□[/cyan]  {auto.alias}")
        for eid in auto.entity_references:
            console.print(f"       [dim]{eid}[/dim]")
    console.rule()


def reverse_migrate_command(
    z2m_export_path: Path,
    state_path: Path,
    status_only: bool,
    ha_url: str,
    token: str,
    verify_ssl: bool,
    z2m_url: str,
    mqtt_topic: str = "zigbee2mqtt",
) -> None:
    export = Z2MExport.model_validate_json(z2m_export_path.read_text())
    devices_raw = [
        {"ieee": normalize_ieee(d.ieee), "name": d.name_by_user or d.friendly_name}
        for d in export.devices
    ]
    state = load_state(state_path, z2m_export_path, devices_raw)

    if status_only:
        show_status(export, state)
        return

    ha_client = HAClient(ha_url, token, verify_ssl)
    z2m_client = Z2MClient(ha_url, token, z2m_url, verify_ssl, mqtt_topic)

    device = pick_device(export, state)
    if device is None:
        return

    asyncio.run(run_reverse_wizard(device, state, state_path, ha_client, z2m_client))
