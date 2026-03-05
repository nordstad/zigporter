"""Interactive command for identifying and managing offline/stale HA devices."""

import asyncio
from pathlib import Path
from typing import Any

import questionary
from rich.console import Console

from zigporter.config import default_stale_path
from zigporter.ha_client import HAClient
from zigporter.stale_state import (
    StaleDeviceStatus,
    StaleState,
    load_stale_state,
    mark_ignored,
    mark_stale,
    mark_suppressed,
    record_first_seen,
    save_stale_state,
    unmark,
)
from zigporter.ui import QUESTIONARY_STYLE

console = Console()
_STYLE = QUESTIONARY_STYLE

_OFFLINE_STATES = {"unavailable", "unknown"}


# ---------------------------------------------------------------------------
# Detection logic
# ---------------------------------------------------------------------------


def _is_ha_core_device(device: dict[str, Any]) -> bool:
    """Return True if this is the special Home Assistant core device."""
    for identifier in device.get("identifiers", []):
        if isinstance(identifier, list) and len(identifier) >= 1:
            if identifier[0] == "homeassistant":
                return True
    return False


def _integration(device: dict[str, Any]) -> str:
    """Infer the integration name from the first device identifier."""
    identifiers = device.get("identifiers", [])
    if identifiers:
        first = identifiers[0]
        if isinstance(first, list) and len(first) >= 1:
            return str(first[0])
    return "unknown"


def _device_is_offline(
    device: dict[str, Any],
    entity_registry: list[dict[str, Any]],
    state_map: dict[str, str],
) -> bool:
    """Return True if the device qualifies as offline."""
    device_id = device["id"]
    entities = [e for e in entity_registry if e.get("device_id") == device_id]

    if not entities:
        # Ghost device — no entities at all
        return True

    enabled_entities = [e for e in entities if e.get("disabled_by") is None]
    if not enabled_entities:
        # All entities disabled — not reliably detectable
        return False

    return all(
        state_map.get(e["entity_id"], "unknown") in _OFFLINE_STATES for e in enabled_entities
    )


def detect_offline_devices(
    device_registry: list[dict[str, Any]],
    entity_registry: list[dict[str, Any]],
    area_registry: list[dict[str, Any]],
    states: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return devices that appear offline, enriched with area and integration info."""
    state_map = {s["entity_id"]: s["state"] for s in states}
    area_map = {a["area_id"]: a["name"] for a in area_registry}

    # Build a set of device IDs that are acting as hubs with at least one non-offline child.
    # Physical gateways (e.g. Plejd GWY-01, UniFi controller) register their leaf devices
    # with via_device_id pointing back to themselves. If any child device is online, the hub
    # is clearly working — don't flag it as stale regardless of its own entity states.
    hubs_with_active_children: set[str] = set()
    for device in device_registry:
        via = device.get("via_device_id")
        if via and not _device_is_offline(device, entity_registry, state_map):
            hubs_with_active_children.add(via)

    offline: list[dict[str, Any]] = []
    for device in device_registry:
        if _is_ha_core_device(device):
            continue
        # Skip integration-level service entries (hubs, coordinators, gateway virtual devices).
        # Their entities (e.g. firmware update sensors) can report unavailable even when the
        # actual physical devices they manage are working fine.
        if device.get("entry_type") == "service":
            continue
        # Skip hub/gateway devices that have non-offline child devices. The hub's own
        # entities (identify button, firmware sensor) may be unavailable even when the
        # devices it manages are all responsive.
        if device["id"] in hubs_with_active_children:
            continue
        if not _device_is_offline(device, entity_registry, state_map):
            continue

        device_id = device["id"]
        name = device.get("name_by_user") or device.get("name") or device_id
        area_id = device.get("area_id")
        area_name = area_map.get(area_id, "") if area_id else ""
        entities = [e for e in entity_registry if e.get("device_id") == device_id]
        enabled_entity_ids = [e["entity_id"] for e in entities if e.get("disabled_by") is None]

        offline.append(
            {
                "device_id": device_id,
                "name": name,
                "area_name": area_name,
                "integration": _integration(device),
                "identifiers": device.get("identifiers", []),
                "entity_ids": enabled_entity_ids,
                "state_map": {eid: state_map.get(eid, "unknown") for eid in enabled_entity_ids},
            }
        )

    return offline


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------


async def _fetch_offline_devices(ha_url: str, token: str, verify_ssl: bool) -> list[dict[str, Any]]:
    client = HAClient(ha_url, token, verify_ssl)
    ws_data = await client.get_stale_check_data()
    states = await client.get_states()
    return detect_offline_devices(
        device_registry=ws_data["device_registry"],
        entity_registry=ws_data["entity_registry"],
        area_registry=ws_data["area_registry"],
        states=states,
    )


def _zha_ieee_from_identifiers(identifiers: list) -> str | None:
    """Return the ZHA IEEE address from a device's identifiers list, or None."""
    for identifier in identifiers:
        if isinstance(identifier, list) and len(identifier) >= 2 and identifier[0] == "zha":
            return str(identifier[1])
    return None


async def _do_remove_device(
    ha_url: str, token: str, verify_ssl: bool, device: dict[str, Any]
) -> bool:
    """Remove the device and verify it is gone from the registry.

    Mirrors the fallback logic in fix_device: tries the device registry WS command first,
    then falls back to ``zha.remove`` for ZHA devices if the command is unsupported.
    Returns True when the device is confirmed gone.
    """
    client = HAClient(ha_url, token, verify_ssl)
    device_id = device["device_id"]
    removed = False

    try:
        await client.remove_device(device_id)
        removed = True
    except RuntimeError as exc:
        if "unknown_command" not in str(exc):
            raise
        # config/device_registry/remove is unsupported on this HA version.
        # Fall back to the ZHA service for ZHA devices.
        ieee = _zha_ieee_from_identifiers(device.get("identifiers", []))
        if ieee:
            await client.remove_zha_device(ieee)
            removed = True

    if not removed:
        return False

    # Wait briefly so HA can process the removal before we verify.
    await asyncio.sleep(1)

    ws_data = await client.get_stale_check_data()
    registry_ids = {d["id"] for d in ws_data["device_registry"]}
    return device_id not in registry_ids


# ---------------------------------------------------------------------------
# Picker helpers
# ---------------------------------------------------------------------------

_GROUP_ORDER = {
    None: 0,
    StaleDeviceStatus.NEW: 0,
    StaleDeviceStatus.STALE: 1,
    StaleDeviceStatus.IGNORED: 2,
    StaleDeviceStatus.SUPPRESSED: 3,
}
_GROUP_LABEL = {
    None: "New",
    StaleDeviceStatus.NEW: "New",
    StaleDeviceStatus.STALE: "Stale",
    StaleDeviceStatus.IGNORED: "Ignored",
    StaleDeviceStatus.SUPPRESSED: "Suppressed",
}

# Sentinel for the "Done" picker choice.  questionary.Choice treats value=None as
# "no value set" and falls back to returning the title string, so we use a distinct
# sentinel to reliably detect when the user selects Done.
_DONE = object()


def _build_picker_choices(offline: list[dict[str, Any]], state: StaleState) -> list:
    """Build questionary choices sorted by group (New → Stale → Ignored), then area and name."""

    def _sort_key(d: dict[str, Any]) -> tuple:
        entry = state.devices.get(d["device_id"])
        grp = _GROUP_ORDER[entry.status if entry else None]
        return (grp, d["area_name"], d["name"])

    sorted_offline = sorted(offline, key=_sort_key)

    choices: list = []
    current_group: int | None = None

    for device in sorted_offline:
        entry = state.devices.get(device["device_id"])
        grp = _GROUP_ORDER[entry.status if entry else None]
        if grp != current_group:
            current_group = grp
            label = _GROUP_LABEL[entry.status if entry else None]
            choices.append(
                questionary.Separator(f" ── {label} {'─' * max(0, 50 - len(label) - 4)}")
            )

        area = f"{device['area_name']:<18}" if device["area_name"] else " " * 18
        note = f"· {entry.note[:35]}" if entry and entry.note else ""
        row = f"  {device['name']:<40}  {area}  {note}"
        choices.append(questionary.Choice(title=row, value=device))

    choices.append(questionary.Separator("─" * 56))
    choices.append(questionary.Choice(title="  Done", value=_DONE))
    return choices


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------


def _handle_remove(
    device: dict[str, Any],
    state: StaleState,
    state_path: Path,
    removed_ids: set[str],
    ha_url: str,
    token: str,
    verify_ssl: bool,
) -> None:
    """Prompt and execute device removal, updating removed_ids on success."""
    confirmed = questionary.confirm(
        f'Remove "{device["name"]}" from Home Assistant? This cannot be undone.',
        default=False,
        style=_STYLE,
    ).ask()
    if not confirmed:
        return

    console.print(f"\nRemoving [bold]{device['name']}[/bold]...", end=" ")
    try:
        success = asyncio.run(_do_remove_device(ha_url, token, verify_ssl, device))
    except (RuntimeError, OSError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        return

    if success:
        console.print("[green]✓ Removed[/green]")
        unmark(state, device["device_id"])
        save_stale_state(state, state_path)
        removed_ids.add(device["device_id"])
    else:
        console.print(
            "[yellow]⚠ Device still present in registry.[/yellow]\n"
            "[dim]It may be actively managed by an integration that re-registered it. "
            "To remove it permanently, delete the device from within that integration's settings "
            "or disable the integration entry.[/dim]"
        )


def _handle_mark_stale(device: dict[str, Any], state: StaleState, state_path: Path) -> None:
    note = questionary.text("Add a note (optional, Enter to skip):").ask()
    mark_stale(state, device["device_id"], device["name"], note=note or None)
    save_stale_state(state, state_path)
    console.print("[green]✓ Marked as stale[/green]")


def _handle_ignore(device: dict[str, Any], state: StaleState, state_path: Path) -> None:
    mark_ignored(state, device["device_id"], device["name"])
    save_stale_state(state, state_path)
    console.print("[green]✓ Marked as ignored[/green]")


def _handle_clear(device: dict[str, Any], state: StaleState, state_path: Path) -> None:
    unmark(state, device["device_id"])
    save_stale_state(state, state_path)
    console.print("[green]✓ Status cleared[/green]")


def _handle_suppress(
    device: dict[str, Any],
    state: StaleState,
    state_path: Path,
    removed_ids: set[str],
) -> None:
    """Permanently hide this device from the picker in all future runs."""
    mark_suppressed(state, device["device_id"], device["name"])
    save_stale_state(state, state_path)
    console.print("[green]✓ Suppressed — will not appear again[/green]")
    removed_ids.add(device["device_id"])


# ---------------------------------------------------------------------------
# Device detail view
# ---------------------------------------------------------------------------


def _show_device_detail(
    device: dict[str, Any],
    state: StaleState,
    state_path: Path,
    removed_ids: set[str],
    ha_url: str,
    token: str,
    verify_ssl: bool,
) -> None:
    """Show device detail and action menu."""
    entry = state.devices.get(device["device_id"])

    area_str = f"  ·  Area: {device['area_name']}" if device["area_name"] else ""
    entity_summary = ", ".join(
        f"{eid} ({device['state_map'].get(eid, 'unknown')})" for eid in device["entity_ids"][:5]
    )
    if len(device["entity_ids"]) > 5:
        entity_summary += f" … (+{len(device['entity_ids']) - 5} more)"
    if not entity_summary:
        entity_summary = "(no entities)"

    console.print(
        f"\n[bold]{device['name']}[/bold]{area_str}\n"
        f"Integration: {device['integration']}  ·  Entities: {entity_summary}"
    )
    if entry and entry.note:
        console.print(f"[dim]Note: {entry.note}[/dim]")

    action_choices = [
        questionary.Choice("Remove from Home Assistant", value="remove"),
        questionary.Choice("Mark as stale (note for later)", value="stale"),
        questionary.Choice("Ignore (known offline, no action needed)", value="ignore"),
        questionary.Choice("Suppress (never show again)", value="suppress"),
    ]
    if entry is not None:
        action_choices.append(questionary.Choice("Clear status", value="clear"))
    action_choices.append(questionary.Choice("Back", value="back"))

    action = questionary.select(
        "What would you like to do?",
        choices=action_choices,
        style=_STYLE,
    ).ask()

    if action == "remove":
        _handle_remove(device, state, state_path, removed_ids, ha_url, token, verify_ssl)
    elif action == "stale":
        _handle_mark_stale(device, state, state_path)
    elif action == "ignore":
        _handle_ignore(device, state, state_path)
    elif action == "suppress":
        _handle_suppress(device, state, state_path, removed_ids)
    elif action == "clear":
        _handle_clear(device, state, state_path)
    # "back" or None → return to picker


# ---------------------------------------------------------------------------
# Command entry point
# ---------------------------------------------------------------------------


def stale_command(
    ha_url: str,
    token: str,
    verify_ssl: bool,
    state_path: Path | None = None,
) -> None:
    """Identify and interactively manage offline/stale HA devices."""
    if state_path is None:
        state_path = default_stale_path()

    console.print("\nFetching device data from Home Assistant...")
    try:
        offline = asyncio.run(_fetch_offline_devices(ha_url, token, verify_ssl))
    except (RuntimeError, OSError) as exc:
        console.print(f"[red]Error connecting to Home Assistant:[/red] {exc}")
        return

    if not offline:
        console.print("[green]No offline devices found.[/green]")
        return

    state = load_stale_state(state_path)

    # Record first-seen for all offline devices, then persist
    for device in offline:
        record_first_seen(state, device["device_id"], device["name"])

    # Prune state entries for devices that are no longer offline (resolved or removed from HA)
    current_ids = {d["device_id"] for d in offline}
    pruned = [did for did in list(state.devices) if did not in current_ids]
    for did in pruned:
        state.devices.pop(did)
    if pruned:
        n = len(pruned)
        console.print(
            f"[dim](Pruned {n} resolved entr{'y' if n == 1 else 'ies'} from stale.json)[/dim]"
        )

    save_stale_state(state, state_path)

    n_stale = sum(
        1
        for d in offline
        if d["device_id"] in state.devices
        and state.devices[d["device_id"]].status == StaleDeviceStatus.STALE
    )
    n_ignored = sum(
        1
        for d in offline
        if d["device_id"] in state.devices
        and state.devices[d["device_id"]].status == StaleDeviceStatus.IGNORED
    )
    console.print(
        f"\n[bold]{len(offline)} offline device(s) found[/bold]  "
        f"([dim]{n_stale} stale · {n_ignored} ignored[/dim])"
    )

    removed_ids: set[str] = set()

    while True:
        current_offline = [d for d in offline if d["device_id"] not in removed_ids]
        if not current_offline:
            console.print("[green]All offline devices have been handled.[/green]")
            break

        # Split suppressed devices out of the visible picker
        suppressed_offline = [
            d
            for d in current_offline
            if state.devices.get(d["device_id"]) is not None
            and state.devices[d["device_id"]].status == StaleDeviceStatus.SUPPRESSED
        ]
        visible_offline = [d for d in current_offline if d not in suppressed_offline]

        if suppressed_offline:
            n = len(suppressed_offline)
            console.print(
                f"[dim]({n} suppressed – use 'Clear status' on a device to un-suppress)[/dim]"
            )

        if not visible_offline:
            console.print("[green]All offline devices have been handled.[/green]")
            break

        choices = _build_picker_choices(visible_offline, state)
        selected = questionary.select(
            "Select a device to review:",
            choices=choices,
            use_indicator=True,
            style=_STYLE,
        ).ask()

        if selected is None or selected is _DONE:
            break

        _show_device_detail(selected, state, state_path, removed_ids, ha_url, token, verify_ssl)
