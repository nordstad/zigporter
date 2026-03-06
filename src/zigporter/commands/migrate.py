import asyncio
import re
from pathlib import Path
from typing import Any

import questionary
from rich.console import Console
from rich.table import Table

from zigporter.commands.migrate_reporting import (
    show_device_dependencies,
    step_show_inspect_summary,
    step_show_test_checklist,
)
from zigporter.ha_client import HAClient
from zigporter.migration_state import (
    DeviceStatus,
    MigrationState,
    load_state,
    mark_failed,
    mark_in_progress,
    mark_migrated,
    mark_pending,
    save_state,
)
from zigporter.models import ZHADevice, ZHAExport
from zigporter.ui import QUESTIONARY_STYLE
from zigporter.utils import normalize_ieee
from zigporter.z2m_client import Z2MClient
from zigporter.commands.rename_device import (
    build_device_rename_plan,
    compute_entity_pairs,
    display_device_plan,
    execute_device_rename,
    resolve_odd_entities,
    slugify,
)

console = Console()

_STYLE = QUESTIONARY_STYLE

WIZARD_STEPS = 8

# Zigbee PermitJoin duration is an 8-bit field; 255 = always-on, so 254 is the
# practical maximum for a timed window.  Requests larger than this are silently
# ignored by Z2M / the Zigbee stack.
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


def show_status(export: ZHAExport, state: MigrationState) -> None:
    device_map = {d.ieee: d for d in export.devices}
    table = Table(title="Migration Progress", show_header=True)
    table.add_column("", width=2)
    table.add_column("Device")
    table.add_column("Model")
    table.add_column("Area")
    table.add_column("Status")
    table.add_column("Migrated at")

    for ieee, dev_state in state.devices.items():
        device = device_map.get(ieee)
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


def pick_device(export: ZHAExport, state: MigrationState) -> ZHADevice | None:
    pending = [
        d
        for d in export.devices
        if state.devices.get(d.ieee, None) is None
        or state.devices[d.ieee].status
        in (DeviceStatus.PENDING, DeviceStatus.FAILED, DeviceStatus.IN_PROGRESS)
    ]

    if not pending:
        console.print("[green]All devices have been migrated![/green]")
        return None

    # Sort by area (no-area → end), then offline devices sink to bottom, then by name
    pending.sort(key=lambda d: (d.area_name or "\xff", d.available is False, d.name))

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

        dev_state = state.devices.get(device.ieee)
        status = dev_state.status if dev_state else DeviceStatus.PENDING
        offline_tag = "  (offline)" if device.available is False else ""
        suffix = (
            "  ◌ in progress"
            if status == DeviceStatus.IN_PROGRESS
            else ("  ✗ failed" if status == DeviceStatus.FAILED else "")
        )
        label = f"  {device.name:<40} {device.model or ''}{offline_tag}{suffix}"
        choices.append(questionary.Choice(title=label, value=device))

    selected = questionary.select(
        "Select a device to migrate:",
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


async def step_remove_from_zha(device: ZHADevice, ha_client: HAClient) -> bool:
    _print_step(1, "Remove from ZHA")

    console.print(
        f"\n  Device:       [bold]{device.name}[/bold]\n"
        f"  IEEE:         [dim]{device.ieee}[/dim]\n"
        f"  Manufacturer: {device.manufacturer or 'unknown'}\n"
        f"  Model:        {device.model or 'unknown'}\n"
    )
    confirmed = await questionary.confirm(
        f'Remove "{device.name}" from ZHA?', default=False, style=_STYLE
    ).unsafe_ask_async()
    if not confirmed:
        console.print("[yellow]Aborted.[/yellow]")
        return False

    console.print(f"\nRemoving [bold]{device.name}[/bold] from ZHA...", end=" ")

    try:
        await ha_client.remove_zha_device(device.ieee)
        console.print("[green]✓ Removal command sent[/green]")
    except (RuntimeError, OSError) as exc:
        console.print(f"[yellow]Could not remove automatically: {exc}[/yellow]")
        console.print(
            "\nRemove the device manually in Home Assistant:\n"
            "  [bold]Settings → Devices & Services → Zigbee Home Automation[/bold]\n"
            f"  → [bold]{device.name}[/bold] → Delete\n"
        )
        await questionary.press_any_key_to_continue(
            "Press Enter when the device is deleted...", style=_STYLE
        ).unsafe_ask_async()

    # Poll HA device registry to confirm device is gone
    console.print("Verifying removal...", end=" ")
    for _ in range(10):
        await asyncio.sleep(2)
        registry = await ha_client.get_device_registry()
        if not any(d.get("id") == device.device_id for d in registry):
            console.print("[green]✓ Device removed from ZHA[/green]")
            return True

    console.print(
        "[yellow]Warning:[/yellow] Device still appears in HA device registry. "
        "Continuing anyway — it may take a moment to update."
    )
    return True


async def step_reset_device(device: ZHADevice) -> None:
    _print_step(2, "Reset physical device")
    model = device.model or "your device"
    console.print(
        f"\nFactory-reset the [bold]{device.name}[/bold] to clear its old pairing.\n"
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


async def _permit_join_refresh_loop(z2m_client: Z2MClient) -> None:
    """Re-open permit join every 244 s so the Zigbee 254-second window never expires."""
    while True:
        await asyncio.sleep(_PERMIT_JOIN_MAX - 10)
        try:
            await z2m_client.enable_permit_join(seconds=_PERMIT_JOIN_MAX)
        except (RuntimeError, OSError):
            pass


async def step_pair_with_z2m(
    device: ZHADevice,
    z2m_client: Z2MClient,
    timeout: int = 300,
) -> dict[str, Any] | None:
    _print_step(3, "Pair with Zigbee2MQTT")

    pj_secs = min(timeout, _PERMIT_JOIN_MAX)
    console.print("\nEnabling permit join...")
    try:
        await z2m_client.enable_permit_join(seconds=pj_secs)
        console.print("[green]✓ Permit join enabled[/green]")
    except (RuntimeError, OSError) as exc:
        console.print(f"[red]Could not enable permit join: {exc}[/red]")
        return None

    console.print(f"\nTrigger [bold]{device.name}[/bold] to join now...")
    console.print(f"Waiting for device [dim]{device.ieee}[/dim] to appear in Z2M...\n")

    # Snapshot existing devices so we can detect unexpected joiners.
    try:
        known_iees: set[str] = {
            normalize_ieee(d.get("ieee_address", "")) for d in await z2m_client.get_devices()
        }
    except (RuntimeError, OSError):
        known_iees = set()
    target_ieee = normalize_ieee(device.ieee)

    def _on_event(event_type: str, data: dict[str, Any]) -> None:
        event_ieee = normalize_ieee(data.get("ieee_address", ""))
        if event_ieee == target_ieee:
            if event_type == "device_joined":
                console.print("\n  Device found — waiting for interview...         ", end="\r")
            elif event_type == "device_interview" and data.get("status") == "started":
                console.print("\n  Device found — interview in progress...         ", end="\r")
        elif event_type == "device_joined" and event_ieee and event_ieee not in known_iees:
            known_iees.add(event_ieee)
            console.print(
                f"\n  [yellow]⚠ A different device joined Z2M:[/yellow] "
                f"[dim]{data.get('ieee_address')}[/dim] "
                f"([bold]{data.get('friendly_name')}[/bold])\n"
                f"  This is NOT [bold]{device.name}[/bold]. "
                f"Put the correct device in pairing mode."
            )

    loop = asyncio.get_running_loop()
    start_mono = loop.time()

    async def _ticker() -> None:
        while True:
            remaining = max(0, timeout - int(loop.time() - start_mono))
            console.print(
                f"  ⠸ Listening for new device  [{remaining:03d}s remaining]",
                end="\r",
            )
            await asyncio.sleep(1)

    pj_task = asyncio.create_task(_permit_join_refresh_loop(z2m_client))
    ticker_task = asyncio.create_task(_ticker())

    status = "timeout"
    try:
        status, _ = await z2m_client.wait_for_interview(device.ieee, timeout, on_event=_on_event)
    except (RuntimeError, OSError):
        pass  # WS failure — fall through to the timeout prompt
    finally:
        pj_task.cancel()
        ticker_task.cancel()
        await asyncio.gather(pj_task, ticker_task, return_exceptions=True)

    ieee_hex = f"0x{target_ieee}"

    if status == "successful":
        console.print("\n[green]✓ Interview complete![/green]")
        await z2m_client.disable_permit_join()
        z2m_device = await z2m_client.get_device_by_ieee(device.ieee)
        if z2m_device:
            console.print(
                f"[green]✓ Device in Z2M:[/green] [bold]{z2m_device.get('friendly_name')}[/bold]"
            )
            return z2m_device
        # Registry hasn't caught up yet — use IEEE hex; rename step corrects it.
        console.print(
            f"[yellow]Proceeding with fallback name:[/yellow] {ieee_hex}\n"
            f"[dim]The rename step will set the correct name.[/dim]"
        )
        return {"ieee_address": ieee_hex, "friendly_name": ieee_hex}

    await z2m_client.disable_permit_join()

    if status == "failed":
        console.print(
            f"\n[yellow]⚠ Device joined but interview failed:[/yellow] [dim]{ieee_hex}[/dim]"
        )
        choice = await questionary.select(
            "How would you like to proceed?",
            choices=[
                questionary.Choice("Retry — wait for Z2M to retry the interview", value="retry"),
                questionary.Choice(
                    "Force continue — device joined but interview did not complete",
                    value="force",
                ),
                questionary.Choice("Mark as failed — revisit this device later", value="fail"),
            ],
            style=_STYLE,
        ).unsafe_ask_async()

        if choice == "retry":
            return await step_pair_with_z2m(device, z2m_client, timeout)
        if choice == "force":
            z2m_device = await z2m_client.get_device_by_ieee(device.ieee)
            if z2m_device:
                console.print(
                    f"[yellow]Proceeding with incomplete interview:[/yellow] "
                    f"[bold]{z2m_device.get('friendly_name')}[/bold]"
                )
                return z2m_device
            console.print(
                f"[yellow]Proceeding with fallback name:[/yellow] {ieee_hex}\n"
                f"[dim]The rename step will set the correct name.[/dim]"
            )
            return {"ieee_address": ieee_hex, "friendly_name": ieee_hex}
        return None

    # Timeout path
    console.print(f"\n[yellow]Timed out waiting for[/yellow] [dim]{device.ieee}[/dim]")
    choice = await questionary.select(
        "How would you like to proceed?",
        choices=[
            questionary.Choice("Retry — poll for another 5 minutes", value="retry"),
            questionary.Choice(
                "Force continue — device joined but interview did not complete",
                value="force",
            ),
            questionary.Choice("Mark as failed — revisit this device later", value="fail"),
        ],
        style=_STYLE,
    ).unsafe_ask_async()

    if choice == "retry":
        return await step_pair_with_z2m(device, z2m_client, timeout)
    if choice == "force":
        try:
            z2m_device = await z2m_client.get_device_by_ieee(device.ieee)
        except (RuntimeError, OSError):
            z2m_device = None
        if z2m_device:
            console.print(
                f"[green]✓ Device found in Z2M:[/green] "
                f"[bold]{z2m_device.get('friendly_name')}[/bold]"
            )
            return z2m_device
        # Z2M names a newly joined device by its IEEE hex by default.
        console.print(
            f"[yellow]Proceeding with fallback name:[/yellow] {ieee_hex}\n"
            f"[dim]The rename step will set the correct name.[/dim]"
        )
        return {"ieee_address": ieee_hex, "friendly_name": ieee_hex}
    return None


async def step_rename(
    device: ZHADevice,
    z2m_device: dict[str, Any],
    z2m_client: Z2MClient,
    ha_client: HAClient,
) -> bool:
    _print_step(4, "Rename device in Z2M")

    current_name = z2m_device.get("friendly_name", "")
    target_name = device.name

    if current_name == target_name:
        console.print(f"[green]✓ Already named correctly:[/green] {target_name}")
        return True

    console.print(f"\n  Z2M assigned:   [dim]{current_name}[/dim]")
    console.print(f"  Will rename to: [bold]{target_name}[/bold]\n")

    console.print()

    confirm = await questionary.confirm(
        f'Apply rename to "{target_name}"?', default=True, style=_STYLE
    ).unsafe_ask_async()
    if not confirm:
        console.print("[yellow]Rename skipped.[/yellow]")
        return False

    try:
        await z2m_client.rename_device(current_name, target_name)
        console.print(f"[green]✓ Renamed to {target_name}[/green]")
    except (RuntimeError, OSError) as exc:
        console.print(f"[red]Rename failed: {exc}[/red]")
        return False

    # Set area in HA if device has one — must use the new Z2M device_id.
    # Z2M may take a moment to register in HA after renaming, so we poll briefly.
    if device.area_id:
        z2m_device_id = await _wait_for_z2m_device_in_ha(device, ha_client, timeout=30)
        if z2m_device_id:
            for attempt in range(4):  # up to 4 attempts: 0, 3, 6, 9 s after rename
                try:
                    await ha_client.update_device_area(z2m_device_id, device.area_id)
                    console.print(f"[green]✓ Area set to {device.area_name}[/green]")
                    break
                except (RuntimeError, OSError):
                    if attempt < 3:
                        await asyncio.sleep(3)
            else:
                console.print(
                    f"[yellow]Note:[/yellow] Could not set area automatically. "
                    f"Set [bold]{device.area_name}[/bold] manually in HA."
                )
        else:
            console.print(
                f"[yellow]Note:[/yellow] Z2M device not yet in HA registry. "
                f"Set area [bold]{device.area_name}[/bold] manually in HA."
            )

    return True


_IEEE_PATTERN = re.compile(r"0x[0-9a-fA-F]{16}")
_NUMERIC_SUFFIX_PAT = re.compile(r"^(.+)_\d+$")


async def _wait_for_z2m_device_in_ha(
    device: ZHADevice, ha_client: HAClient, timeout: int = 90
) -> str | None:
    """Poll the HA device registry until the Z2M device appears, up to *timeout* seconds."""
    elapsed = 0
    poll_interval = 3
    while elapsed < timeout:
        z2m_device_id = await ha_client.get_z2m_device_id(device.ieee)
        if z2m_device_id:
            return z2m_device_id
        remaining = timeout - elapsed
        console.print(
            f"  Waiting for HA to register Z2M device... [{remaining:02d}s remaining]",
            end="\r",
        )
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    console.print()  # clear the \r line
    return None


def _is_ieee_entity(entity_id: str) -> bool:
    """Return True if the entity_id contains a raw IEEE address (pre-rename artifact)."""
    return bool(_IEEE_PATTERN.search(entity_id))


async def step_reconcile_entity_ids(device: ZHADevice, ha_client: HAClient) -> None:
    _print_step(5, "Restore entity IDs")

    z2m_device_id = await _wait_for_z2m_device_in_ha(device, ha_client)
    if z2m_device_id is None:
        console.print(
            "[yellow]Z2M device did not appear in HA registry within the wait window; "
            "skipping entity ID restore.[/yellow]"
        )
        return

    full_registry = await ha_client.get_entity_registry()
    z2m_entities = [e for e in full_registry if e.get("device_id") == z2m_device_id]

    # Build lookup: domain → ZHA entities from export snapshot
    zha_by_domain: dict[str, list] = {}
    for e in device.entities:
        domain = e.entity_id.split(".")[0]
        zha_by_domain.setdefault(domain, []).append(e)

    # Index the full registry by entity_id so we can detect stale conflicts.
    registry_by_id = {e["entity_id"]: e for e in full_registry}

    renames: list[tuple[str, str]] = []  # (current_entity_id, target_entity_id)
    conflicts: list[tuple[str, str]] = []  # renames blocked by a stale entity at the target ID
    for entry in z2m_entities:
        current_id: str = entry["entity_id"]
        if not _is_ieee_entity(current_id):
            continue
        domain, object_id = current_id.split(".", 1)
        # Extract the feature suffix after the IEEE hex, e.g. "0x00124b002a5333ab_temperature" → "temperature"
        suffix_match = re.search(r"0x[0-9a-fA-F]{16}_(.+)$", object_id)
        if not suffix_match:
            continue
        feature = suffix_match.group(1)
        candidates = [
            e
            for e in zha_by_domain.get(domain, [])
            if e.entity_id.split(".", 1)[1].endswith(f"_{feature}")
        ]
        if len(candidates) != 1:
            continue  # ambiguous or no match — skip
        target_id = candidates[0].entity_id
        if target_id == current_id:
            continue
        # If the target entity ID already exists and belongs to a different device, it is a
        # stale/leftover entity (e.g. an old ZHA entry that was never cleaned up).  Renaming
        # into it would either fail or silently overwrite it, leaving the dashboard broken.
        existing = registry_by_id.get(target_id)
        if existing and existing.get("device_id") != z2m_device_id:
            conflicts.append((current_id, target_id))
        else:
            renames.append((current_id, target_id))

    # --- Second pass: numeric-suffix conflicts ---
    # These arise when Z2M already has a friendly name but stale ZHA registry entries still
    # occupy the original entity IDs, so HA appends _2/_3/etc. to the new Z2M entities.
    suffix_resolvable: list[tuple[str, str]] = []  # (z2m_id_with_suffix, base_id)
    for entry in z2m_entities:
        current_id = entry["entity_id"]
        if _is_ieee_entity(current_id):
            continue
        m = _NUMERIC_SUFFIX_PAT.match(current_id)
        if not m:
            continue
        base_id = m.group(1)
        stale = registry_by_id.get(base_id)
        if stale and stale.get("device_id") != z2m_device_id:
            suffix_resolvable.append((current_id, base_id))

    if not renames and not conflicts and not suffix_resolvable:
        console.print("[green]✓ Entity IDs already use friendly names[/green]")
        return

    if renames:
        console.print("\n  The following entity IDs will be updated:\n")
        for current_id, target_id in renames:
            console.print(f"  [dim]{current_id}[/dim]")
            console.print(f"    → [bold]{target_id}[/bold]")

    if conflicts:
        console.print(
            "\n  [yellow]The following renames are blocked by stale entities already using "
            "the target ID.[/yellow]\n  Clean them up in "
            "[bold]Settings → Devices & Services → Entities[/bold] and re-run the wizard:\n"
        )
        for current_id, target_id in conflicts:
            console.print(f"  [dim]{current_id}[/dim]")
            console.print(
                f"    → [yellow]{target_id}[/yellow] [dim](entity ID already in use)[/dim]"
            )

    if not renames:
        console.print()
    else:
        console.print()

        confirmed = await questionary.confirm(
            "Apply entity ID renames?", default=True, style=_STYLE
        ).unsafe_ask_async()
        if not confirmed:
            console.print("[yellow]Entity ID restore skipped.[/yellow]")
        else:
            # Re-fetch right before applying: HA may have auto-renamed entities when Z2M renamed
            # the device.  Any IEEE-named entity that's already gone was handled by HA — skip.
            refreshed = await ha_client.get_entity_registry()
            still_ieee = {
                e["entity_id"]
                for e in refreshed
                if e.get("device_id") == z2m_device_id and _is_ieee_entity(e["entity_id"])
            }

            for current_id, target_id in renames:
                if current_id not in still_ieee:
                    console.print(f"[green]✓[/green] {current_id} → (already renamed by HA)")
                    continue
                try:
                    await ha_client.rename_entity_id(current_id, target_id)
                    console.print(f"[green]✓[/green] {current_id} → {target_id}")
                except (RuntimeError, OSError) as exc:
                    console.print(f"[yellow]Warning:[/yellow] Could not rename {current_id}: {exc}")

    # --- Resolve numeric-suffix conflicts ---
    if suffix_resolvable:
        console.print(
            "\n  [yellow]Z2M entities have a numeric suffix because stale ZHA entities still "
            "occupy the original IDs.[/yellow]\n"
            "  Deleting the stale entries restores original entity IDs and fixes your dashboards:\n"
        )
        for z2m_id, base_id in suffix_resolvable:
            console.print(f"  [dim]{z2m_id}[/dim]")
            console.print(
                f"    → [bold]{base_id}[/bold]  [dim](stale ZHA entity will be deleted)[/dim]"
            )
        console.print()

        confirmed = await questionary.confirm(
            "Delete stale ZHA entities and restore original entity IDs?",
            default=True,
            style=_STYLE,
        ).unsafe_ask_async()
        if not confirmed:
            console.print("[yellow]Suffix conflict resolution skipped.[/yellow]")
        else:
            for z2m_id, base_id in suffix_resolvable:
                try:
                    await ha_client.delete_entity(base_id)
                except (RuntimeError, OSError) as exc:
                    console.print(
                        f"[yellow]Warning:[/yellow] Could not delete stale {base_id}: {exc}"
                    )
                    continue
                try:
                    await ha_client.rename_entity_id(z2m_id, base_id)
                    console.print(f"[green]✓[/green] {z2m_id} → {base_id}")
                except (RuntimeError, OSError) as exc:
                    console.print(f"[yellow]Warning:[/yellow] Could not rename {z2m_id}: {exc}")


async def _reload_z2m_integration(ha_client: HAClient, z2m_device_id: str) -> None:
    """Reload the HA config entries that manage the Z2M device."""
    registry = await ha_client.get_device_registry()
    device_entry = next((d for d in registry if d["id"] == z2m_device_id), None)
    if not device_entry:
        console.print("[yellow]  Could not find Z2M device in HA registry.[/yellow]")
        return

    entry_ids: list[str] = device_entry.get("config_entries", [])
    if not entry_ids:
        console.print("[yellow]  No config entries found for Z2M device.[/yellow]")
        return

    for entry_id in entry_ids:
        try:
            await ha_client.reload_config_entry(entry_id)
            console.print(f"[green]✓[/green] Reloaded Z2M integration ({entry_id})")
        except (RuntimeError, OSError) as exc:
            console.print(
                f"[yellow]Warning:[/yellow] Could not reload config entry {entry_id}: {exc}"
            )


async def step_validate(device: ZHADevice, ha_client: HAClient, retries: int = 10) -> bool:
    _print_step(7, "Validate")

    z2m_device_id = await _wait_for_z2m_device_in_ha(device, ha_client)
    if z2m_device_id is None:
        console.print(
            "[yellow]Z2M device did not appear in HA registry within the wait window; "
            "skipping validation.[/yellow]"
        )
        return True

    while True:
        console.print("\nWaiting for entities to come online...")

        # (entity_id, state_val | None, status: "ok" | "unknown" | "missing")
        results: list[tuple[str, str | None, str]] = []
        all_ok = False

        for attempt in range(retries):
            await asyncio.sleep(3)
            # Re-fetch entity registry each attempt: Z2M renames entities after friendly name is set.
            # Exclude disabled entities — Z2M marks some (e.g. linkquality) as enabled_by_default:
            # false, so HA creates them with disabled_by="integration" and they never appear in
            # /api/states.
            full_registry = await ha_client.get_entity_registry()
            expected_ids = [
                e["entity_id"]
                for e in full_registry
                if e.get("device_id") == z2m_device_id and not e.get("disabled_by")
            ]
            states = await ha_client.get_states()
            state_map = {s["entity_id"]: s for s in states}

            # If there are both IEEE-named and friendly-named entities, drop the IEEE-named
            # ones from validation — they are stale pre-rename registry entries that Z2M has
            # already replaced with properly named entities.
            has_friendly = any(not _is_ieee_entity(e) for e in expected_ids)
            if has_friendly:
                expected_ids = [e for e in expected_ids if not _is_ieee_entity(e)]

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

            has_unknown = any(s == "unknown" for _, _, s in results)
            has_missing = any(s == "missing" for _, _, s in results)
            if has_unknown:
                console.print(
                    "\n  [dim]~ = entity exists in HA but hasn't reported state yet. "
                    "Try toggling the device on/off.[/dim]"
                )
            if has_missing:
                console.print(
                    "\n  [dim]! = entity not found — may still be loading or was renamed. "
                    "Check HA in a moment.[/dim]"
                )
        else:
            console.print(
                "  [yellow]No entities registered in HA yet. Z2M may still be initialising.[/yellow]"
            )

        if all_ok:
            return True

        only_unknown = results and all(s in ("ok", "unknown") for _, _, s in results)
        if only_unknown:
            prompt = (
                "Entities are registered but haven't reported state yet. What would you like to do?"
            )
        else:
            prompt = "Some entities are not yet online. What would you like to do?"

        choice = await questionary.select(
            prompt,
            choices=[
                questionary.Choice("Retry — poll again", value="retry"),
                questionary.Choice("Reload Z2M integration in HA, then retry", value="reload"),
                questionary.Choice(
                    "Accept — entities exist in HA, state will update soon", value="accept"
                ),
                questionary.Choice("Mark as failed — revisit this device later", value="fail"),
            ],
            style=_STYLE,
        ).unsafe_ask_async()

        if choice in ("retry", "reload"):
            if choice == "reload":
                await _reload_z2m_integration(ha_client, z2m_device_id)
            continue
        return choice == "accept"


# ---------------------------------------------------------------------------
# Post-migration optional rename (step 8)
# ---------------------------------------------------------------------------


async def step_post_migrate_rename(
    device: ZHADevice,
    ha_client: HAClient,
    z2m_client: Z2MClient,
) -> None:
    """Optional step 8: offer a full cascading rename after successful migration."""
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

    new_name = await questionary.text(
        "New device name:",
        default=device.name,
        style=_STYLE,
    ).unsafe_ask_async()
    if not new_name or not new_name.strip():
        console.print("[dim]Rename skipped.[/dim]")
        return
    new_name = new_name.strip()

    if new_name == device.name:
        console.print("[dim]Name unchanged — skipping.[/dim]")
        return

    z2m_device_id = await ha_client.get_z2m_device_id(device.ieee)
    if not z2m_device_id:
        console.print(
            "[yellow]Could not locate Z2M device in HA registry. Rename skipped.[/yellow]"
        )
        return

    console.print("  Fetching entities...", end=" ")
    entities = await ha_client.get_entities_for_device(z2m_device_id)
    console.print(f"[green]{len(entities)} found[/green]")

    if not entities:
        console.print("[yellow]No entities found for this device — rename skipped.[/yellow]")
        return

    old_slug = slugify(device.name)
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
            {"id": z2m_device_id},
            device.name,
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
    await execute_device_rename(
        ha_client,
        device_plan,
        z2m_client=z2m_client,
        z2m_friendly_name=device.name,
    )
    console.print(
        f"\n[green]✓[/green] Renamed device [bold]{device.name}[/bold]"
        f" → [bold cyan]{new_name}[/bold cyan]"
        f" ([bold]{len(device_plan.plans)}[/bold] entities updated)"
    )


# ---------------------------------------------------------------------------
# Main wizard orchestrator
# ---------------------------------------------------------------------------


async def run_wizard(
    device: ZHADevice,
    state: MigrationState,
    state_path: Path,
    ha_client: HAClient,
    z2m_client: Z2MClient,
) -> None:
    console.rule(f"[bold]Migrating: {device.name}[/bold]")

    console.print(
        "\nThis wizard will migrate [bold]{name}[/bold] from ZHA to Zigbee2MQTT "
        "in 7 steps (+ 1 optional):\n"
        "\n"
        "  [cyan]1[/cyan]  Remove from ZHA    Unpair the device from ZHA [red](cannot be undone)[/red]\n"
        "  [cyan]2[/cyan]  Reset device       Factory-reset to clear the old ZHA pairing\n"
        "  [cyan]3[/cyan]  Pair with Z2M      Put device in pairing mode and join Zigbee2MQTT\n"
        "  [cyan]4[/cyan]  Rename             Restore original name and area in Z2M\n"
        "  [cyan]5[/cyan]  Restore entity IDs Rename IEEE-hex entity IDs back to friendly names\n"
        "  [cyan]6[/cyan]  Review             Inspect entities and dashboard cards\n"
        "  [cyan]7[/cyan]  Validate           Confirm entities come back online\n"
        "  [cyan]8[/cyan]  Rename (optional)  Rename to a different name with full HA cascade\n"
        "\n"
        "[dim]You can abort safely at step 1. After that, the device must complete\n"
        "pairing with Z2M before it can be used again.[/dim]".format(name=device.name)
    )

    await show_device_dependencies(device, ha_client, console)

    mark_in_progress(state, device.ieee)
    save_state(state, state_path)

    try:
        # Step 1 — Remove from ZHA
        removed = await step_remove_from_zha(device, ha_client)
        if not removed:
            console.print("[yellow]Migration cancelled.[/yellow]")
            mark_pending(state, device.ieee)
            save_state(state, state_path)
            return

        # Step 2 — Reset physical device
        await step_reset_device(device)

        # Step 3 — Pair with Z2M
        z2m_device = await step_pair_with_z2m(device, z2m_client)
        if z2m_device is None:
            console.print("[red]Pairing failed. Device marked as failed.[/red]")
            mark_failed(state, device.ieee)
            save_state(state, state_path)
            return

        # Step 4 — Rename
        renamed = await step_rename(device, z2m_device, z2m_client, ha_client)

        # Step 5 — Restore entity IDs
        await step_reconcile_entity_ids(device, ha_client)

        # Show entity and dashboard summary before validation
        _print_step(6, "Review entities & dashboards")
        await step_show_inspect_summary(device, ha_client, console)

        # Step 6 — Validate
        valid = await step_validate(device, ha_client)

        if valid:
            friendly_name = device.name if renamed else z2m_device.get("friendly_name", "")
            mark_migrated(state, device.ieee, friendly_name)
            save_state(state, state_path)
            migrated = sum(1 for d in state.devices.values() if d.status == DeviceStatus.MIGRATED)
            total = len(state.devices)
            console.print(
                f"\n[bold green]✓ Migration complete![/bold green] "
                f"[bold]{device.name}[/bold] successfully migrated.\n"
                f"Progress: [bold]{migrated}/{total}[/bold] devices migrated."
            )
            await step_show_test_checklist(device, ha_client, console)
            await step_post_migrate_rename(device, ha_client, z2m_client)
        else:
            mark_failed(state, device.ieee)
            save_state(state, state_path)
            console.print(
                "\n[yellow]Device marked as failed.[/yellow] "
                "Select it again from the menu to retry."
            )

    except KeyboardInterrupt:
        console.print("\n[yellow]Wizard interrupted. Progress saved.[/yellow]")
        mark_failed(state, device.ieee)
        save_state(state, state_path)


def migrate_command(
    zha_export_path: Path,
    state_path: Path,
    status_only: bool,
    ha_url: str,
    token: str,
    verify_ssl: bool,
    z2m_url: str,
    mqtt_topic: str = "zigbee2mqtt",
) -> None:
    export = ZHAExport.model_validate_json(zha_export_path.read_text())
    devices_raw = [{"ieee": d.ieee, "name": d.name} for d in export.devices]
    state = load_state(state_path, zha_export_path, devices_raw)

    if status_only:
        show_status(export, state)
        return

    ha_client = HAClient(ha_url, token, verify_ssl)
    z2m_client = Z2MClient(ha_url, token, z2m_url, verify_ssl, mqtt_topic)

    device = pick_device(export, state)
    if device is None:
        return

    asyncio.run(run_wizard(device, state, state_path, ha_client, z2m_client))
