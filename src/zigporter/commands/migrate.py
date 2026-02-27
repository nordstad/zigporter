import asyncio
import re
from pathlib import Path
from typing import Any

import questionary
from rich.console import Console
from rich.table import Table

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
from zigporter.z2m_client import Z2MClient

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

WIZARD_STEPS = 6

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
        table.add_row(
            f"[{style}]{icon}[/{style}]",
            f"[{style}]{dev_state.name}[/{style}]",
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

    # Sort by area (no-area → end), then by name within each area
    pending.sort(key=lambda d: (d.area_name or "\xff", d.name))

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
        suffix = (
            "  ◌ in progress"
            if status == DeviceStatus.IN_PROGRESS
            else ("  ✗ failed" if status == DeviceStatus.FAILED else "")
        )
        label = f"  {device.name:<40} {device.model or ''}{suffix}"
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
    except Exception as exc:
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


async def step_pair_with_z2m(
    device: ZHADevice,
    z2m_client: Z2MClient,
    timeout: int = 300,
) -> dict[str, Any] | None:
    _print_step(3, "Pair with Zigbee2MQTT")

    # Zigbee spec caps PermitJoin at 254 s per request; we refresh automatically.
    pj_secs = min(timeout, _PERMIT_JOIN_MAX)
    console.print("\nEnabling permit join...")
    try:
        await z2m_client.enable_permit_join(seconds=pj_secs)
        console.print("[green]✓ Permit join enabled[/green]")
    except Exception as exc:
        console.print(f"[red]Could not enable permit join: {exc}[/red]")
        return None

    console.print(f"\nTrigger [bold]{device.name}[/bold] to join now...")
    console.print(f"Waiting for device [dim]{device.ieee}[/dim] to appear in Z2M...\n")

    elapsed = 0
    poll_interval = 3
    pj_started_at = 0  # elapsed seconds when permit join was last issued

    while elapsed < timeout:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
        remaining = timeout - elapsed

        # Refresh permit join 10 s before the current window expires.
        if elapsed >= pj_started_at + pj_secs - 10 and remaining > 0:
            refresh_secs = min(remaining + poll_interval, _PERMIT_JOIN_MAX)
            try:
                await z2m_client.enable_permit_join(seconds=refresh_secs)
            except Exception:
                pass
            pj_started_at = elapsed

        console.print(
            f"  ⠸ Listening for new device  [{remaining:03d}s remaining]",
            end="\r",
        )
        z2m_device = await z2m_client.get_device_by_ieee(device.ieee)
        if z2m_device:
            console.print(
                f"\n[green]✓ Device found in Z2M:[/green] "
                f"[bold]{z2m_device.get('friendly_name')}[/bold]"
            )
            await z2m_client.disable_permit_join()
            return z2m_device

    await z2m_client.disable_permit_join()
    console.print("\n[yellow]Timed out waiting for device.[/yellow]")
    retry = await questionary.confirm("Retry pairing?", style=_STYLE).unsafe_ask_async()
    if retry:
        return await step_pair_with_z2m(device, z2m_client, timeout)
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
    except Exception as exc:
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
                except Exception:
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


def _collect_entity_ids(node: Any) -> set[str]:
    """Recursively collect all entity_id string values from a config dict/list."""
    ids: set[str] = set()
    if isinstance(node, dict):
        val = node.get("entity_id")
        if isinstance(val, str):
            ids.add(val)
        elif isinstance(val, list):
            ids.update(v for v in val if isinstance(v, str))
        for v in node.values():
            ids.update(_collect_entity_ids(v))
    elif isinstance(node, list):
        for item in node:
            ids.update(_collect_entity_ids(item))
    return ids


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

    renames: list[tuple[str, str]] = []  # (current_entity_id, target_entity_id)
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
        if target_id != current_id:
            renames.append((current_id, target_id))

    if not renames:
        console.print("[green]✓ Entity IDs already use friendly names[/green]")
        return

    console.print("\n  The following entity IDs will be updated:\n")
    for current_id, target_id in renames:
        console.print(f"  [dim]{current_id}[/dim]")
        console.print(f"    → [bold]{target_id}[/bold]")
    console.print()

    confirmed = await questionary.confirm(
        "Apply entity ID renames?", default=True, style=_STYLE
    ).unsafe_ask_async()
    if not confirmed:
        console.print("[yellow]Entity ID restore skipped.[/yellow]")
        return

    # Re-fetch right before applying: HA may have auto-renamed entities when Z2M renamed the
    # device.  Any IEEE-named entity that's already gone was handled by HA — skip it silently.
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
        except Exception as exc:
            console.print(f"[yellow]Warning:[/yellow] Could not rename {current_id}: {exc}")


async def step_validate(device: ZHADevice, ha_client: HAClient, retries: int = 10) -> bool:
    _print_step(6, "Validate")

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


# ---------------------------------------------------------------------------
# Post-migration test checklist
# ---------------------------------------------------------------------------


async def step_show_test_checklist(device: ZHADevice, ha_client: HAClient) -> None:
    """Display a checklist of automations, scripts, and scenes to test after migration."""
    old_ids = {e.entity_id for e in device.entities}

    # Collect matching scripts and scenes from live HA data
    scripts, scenes = await asyncio.gather(ha_client.get_scripts(), ha_client.get_scenes())

    matching_scripts: list[tuple[str, list[str]]] = []
    for s in scripts:
        refs = _collect_entity_ids(s) & old_ids
        if refs:
            name = s.get("alias") or s.get("id", "?")
            matching_scripts.append((name, sorted(refs)))

    matching_scenes: list[tuple[str, list[str]]] = []
    for s in scenes:
        # Scene entities live under an "entities" dict keyed by entity_id
        refs = set(s.get("entities", {}).keys()) & old_ids
        if refs:
            name = s.get("name") or s.get("id", "?")
            matching_scenes.append((name, sorted(refs)))

    has_automations = bool(device.automations)
    has_scripts = bool(matching_scripts)
    has_scenes = bool(matching_scenes)

    if not has_automations and not has_scripts and not has_scenes:
        return

    console.print("\n")
    console.rule("[bold cyan]Post-migration test checklist[/bold cyan]")

    if has_automations:
        console.print("\n  [bold]Automations[/bold]")
        for auto in device.automations:
            console.print(f"  [cyan]□[/cyan]  {auto.alias}")
            for eid in auto.entity_references:
                console.print(f"       [dim]{eid}[/dim]")

    if has_scripts:
        console.print("\n  [bold]Scripts[/bold]")
        for name, refs in matching_scripts:
            console.print(f"  [cyan]□[/cyan]  {name}")
            for eid in refs:
                console.print(f"       [dim]{eid}[/dim]")

    if has_scenes:
        console.print("\n  [bold]Scenes[/bold]")
        for name, refs in matching_scenes:
            console.print(f"  [cyan]□[/cyan]  {name}")
            for eid in refs:
                console.print(f"       [dim]{eid}[/dim]")

    console.print(
        "\n  [dim]Tip: Also check your Lovelace dashboards for cards referencing these entities.[/dim]"
    )
    console.rule()


# ---------------------------------------------------------------------------
# Pre-migration dependency summary
# ---------------------------------------------------------------------------


async def _show_device_deps(device: ZHADevice, ha_client: HAClient) -> None:
    """Show automations, scripts, and scenes that reference this device before migration starts."""
    old_ids = {e.entity_id for e in device.entities}

    matching_scripts: list[dict[str, Any]] = []
    matching_scenes: list[dict[str, Any]] = []
    try:
        scripts, scenes = await asyncio.gather(ha_client.get_scripts(), ha_client.get_scenes())
        matching_scripts = [s for s in scripts if _collect_entity_ids(s) & old_ids]
        matching_scenes = [s for s in scenes if set(s.get("entities", {}).keys()) & old_ids]
    except Exception:
        pass

    has_automations = bool(device.automations)
    has_scripts = bool(matching_scripts)
    has_scenes = bool(matching_scenes)

    if not has_automations and not has_scripts and not has_scenes:
        return

    console.print()
    console.rule("[bold cyan]Dependencies[/bold cyan]")
    console.print("[dim]These will need testing after migration:[/dim]\n")

    if has_automations:
        console.print(f"  [bold]Automations[/bold] ({len(device.automations)})")
        for auto in device.automations:
            console.print(f"  [cyan]□[/cyan]  {auto.alias}")

    if has_scripts:
        console.print(f"\n  [bold]Scripts[/bold] ({len(matching_scripts)})")
        for s in matching_scripts:
            name = s.get("alias") or s.get("id", "?")
            console.print(f"  [cyan]□[/cyan]  {name}")

    if has_scenes:
        console.print(f"\n  [bold]Scenes[/bold] ({len(matching_scenes)})")
        for s in matching_scenes:
            name = s.get("name") or s.get("id", "?")
            console.print(f"  [cyan]□[/cyan]  {name}")

    console.print(
        "\n  [dim]Tip: Run [bold]zigporter inspect[/bold] for dashboard cards "
        "and full entity details.[/dim]"
    )
    console.rule()


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
        "in 6 steps:\n"
        "\n"
        "  [cyan]1[/cyan]  Remove from ZHA    Unpair the device from ZHA [red](cannot be undone)[/red]\n"
        "  [cyan]2[/cyan]  Reset device       Factory-reset to clear the old ZHA pairing\n"
        "  [cyan]3[/cyan]  Pair with Z2M      Put device in pairing mode and join Zigbee2MQTT\n"
        "  [cyan]4[/cyan]  Rename             Restore original name and area in Z2M\n"
        "  [cyan]5[/cyan]  Restore entity IDs Rename IEEE-hex entity IDs back to friendly names\n"
        "  [cyan]6[/cyan]  Validate           Confirm entities come back online\n"
        "\n"
        "[dim]You can abort safely at step 1. After that, the device must complete\n"
        "pairing with Z2M before it can be used again.[/dim]".format(name=device.name)
    )

    await _show_device_deps(device, ha_client)

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
            await step_show_test_checklist(device, ha_client)
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
