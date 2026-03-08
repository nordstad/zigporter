"""rename-device command — rename a Z2M device and cascade the change to all entities."""

import asyncio
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import questionary
from rich.console import Console
from rich.table import Table

from zigporter.config import load_z2m_config
from zigporter.ha_client import HAClient, is_yaml_mode
from zigporter.rename_plan import (
    CONTEXT_LABEL,
    RenamePlan,
    apply_location_update,
    build_rename_plan_from_snapshot,
    fetch_ha_snapshot,
)
from zigporter.ui import QUESTIONARY_STYLE
from zigporter.utils import device_display_name
from zigporter.z2m_client import Z2MClient

console = Console()

_STYLE = QUESTIONARY_STYLE


# ---------------------------------------------------------------------------
# Slug helper
# ---------------------------------------------------------------------------


def slugify(name: str) -> str:
    """Convert a device name to an entity-ID-style slug.

    Normalises Unicode via NFKD decomposition so accented characters are
    transliterated to their ASCII base letter (e.g. 'ü' → 'u') rather than
    dropped as underscores.
    """
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "_", ascii_name.lower()).strip("_")


# ---------------------------------------------------------------------------
# Device rename data structure
# ---------------------------------------------------------------------------


@dataclass
class DeviceRenamePlan:
    device_id: str
    old_device_name: str
    new_device_name: str
    plans: list[RenamePlan]
    scanned_names: dict[str, list[str]] = field(default_factory=dict)
    failed_dashboards: list[str] = field(default_factory=list)
    failed_dashboard_paths: list[str | None] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Device lookup (fuzzy)
# ---------------------------------------------------------------------------


def _ieee_from_ha_device(device: dict[str, Any]) -> str | None:
    """Extract IEEE address from an HA device dict's identifiers list.

    Looks for an ("mqtt", "zigbee2mqtt_0x...") identifier pair.
    """
    for pair in device.get("identifiers", []):
        if len(pair) == 2 and pair[0] == "mqtt":
            ident: str = pair[1].lower()
            if ident.startswith("zigbee2mqtt_"):
                return ident[len("zigbee2mqtt_") :]
    return None


_VALID_DEVICE_FILTERS = {"zigbee", "matter"}


def _is_zigbee_device(device: dict[str, Any]) -> bool:
    """Return True if the device is managed by ZHA or Zigbee2MQTT."""
    for pair in device.get("identifiers", []):
        if len(pair) >= 1:
            platform = pair[0]
            if platform == "zha":
                return True
            if platform == "mqtt" and len(pair) == 2:
                if pair[1].lower().startswith("zigbee2mqtt_"):
                    return True
    return False


def _is_matter_device(device: dict[str, Any]) -> bool:
    """Return True if the device is managed by the Matter integration (includes Thread devices)."""
    for pair in device.get("identifiers", []):
        if len(pair) >= 1 and pair[0] == "matter":
            return True
    return False


async def find_device(
    ha_client: HAClient,
    name: str,
    device_filter: str | None = None,
) -> dict[str, Any] | None:
    """Find a device by name. Exact match first, then substring. Prompts if ambiguous."""
    registry = await ha_client.get_device_registry()
    if device_filter == "zigbee":
        registry = [d for d in registry if _is_zigbee_device(d)]
    elif device_filter == "matter":
        registry = [d for d in registry if _is_matter_device(d)]
    name_lower = name.lower()

    exact = [d for d in registry if device_display_name(d).lower() == name_lower]
    if len(exact) == 1:
        return exact[0]

    partial = [d for d in registry if name_lower in device_display_name(d).lower()]
    if not partial:
        return None
    if len(partial) == 1:
        return partial[0]

    choices = [questionary.Choice(device_display_name(d), value=d) for d in partial]
    return await questionary.select(
        f"Multiple devices match '{name}'. Which one?",
        choices=choices,
        style=_STYLE,
    ).unsafe_ask_async()


async def pick_device_interactively(
    ha_client: HAClient,
    device_filter: str | None = None,
) -> dict[str, Any] | None:
    """Show a grouped device picker and return the selected device dict."""
    registry, area_registry = await asyncio.gather(
        ha_client.get_device_registry(),
        ha_client.get_area_registry(),
    )
    area_names: dict[str, str] = {a["area_id"]: a["name"] for a in area_registry}

    def _sort_key(d: dict[str, Any]) -> tuple[int, str, str]:
        area_id = d.get("area_id") or ""
        area_name = area_names.get(area_id, "")
        return (0 if area_id else 1, area_name.lower(), device_display_name(d).lower())

    named_devices = [d for d in registry if d.get("name_by_user") or d.get("name")]
    if device_filter == "zigbee":
        named_devices = [d for d in named_devices if _is_zigbee_device(d)]
    elif device_filter == "matter":
        named_devices = [d for d in named_devices if _is_matter_device(d)]
    if not named_devices:
        label = {"zigbee": "Zigbee ", "matter": "Matter "}.get(device_filter or "", "")
        console.print(f"[yellow]No {label}named devices found in the device registry.[/yellow]")
        return None

    sorted_devices = sorted(named_devices, key=_sort_key)

    choices: list[questionary.Choice | questionary.Separator] = []
    current_area: str = "__unset__"
    for device in sorted_devices:
        area_id = device.get("area_id") or ""
        area_label = area_names.get(area_id, "") if area_id else ""
        if area_label != current_area:
            current_area = area_label
            choices.append(questionary.Separator(f"── {area_label or 'No area'} ──"))
        choices.append(questionary.Choice(device_display_name(device), value=device))

    return await questionary.select(
        "Select a device to rename:",
        choices=choices,
        style=_STYLE,
    ).unsafe_ask_async()


# ---------------------------------------------------------------------------
# Entity pair computation
# ---------------------------------------------------------------------------


def compute_entity_pairs(
    entities: list[dict[str, Any]],
    old_slug: str,
    new_slug: str,
) -> tuple[list[tuple[str, str]], list[dict[str, Any]]]:
    """Split entities into (matched_pairs, odd_entities).

    matched_pairs: (old_entity_id, new_entity_id) where old slug was found in the suffix.
    odd_entities: full entity dicts where the slug wasn't found.

    Note:
        Uses ``str.replace(old_slug, new_slug, 1)`` — only the first occurrence of *old_slug*
        in the entity ID suffix is substituted. Entity IDs where the device slug appears more
        than once (e.g. ``light.kitchen_kitchen_lamp``) will only have the first instance
        replaced. This is rarely a problem in practice but is worth noting.
    """
    matched: list[tuple[str, str]] = []
    odd: list[dict[str, Any]] = []

    for entity in entities:
        eid = entity["entity_id"]
        domain, suffix = eid.split(".", 1)
        if old_slug and old_slug in suffix:
            new_suffix = suffix.replace(old_slug, new_slug, 1)
            matched.append((eid, f"{domain}.{new_suffix}"))
        else:
            odd.append(entity)

    return matched, odd


def _suggest_entity_id(entity: dict[str, Any], new_slug: str) -> str:
    """Propose a new entity ID for an entity that doesn't follow the device name pattern."""
    domain = entity["entity_id"].split(".", 1)[0]
    orig_name = entity.get("name") or entity.get("original_name") or ""
    orig_slug = slugify(orig_name) if orig_name else ""
    return f"{domain}.{new_slug}_{orig_slug}" if orig_slug else f"{domain}.{new_slug}"


# ---------------------------------------------------------------------------
# Device rename display
# ---------------------------------------------------------------------------


def display_device_plan(device_plan: DeviceRenamePlan) -> None:
    console.print(
        f"\n  Device [bold]{device_plan.old_device_name}[/bold]"
        f"  [dim]→[/dim]  "
        f"[bold cyan]{device_plan.new_device_name}[/bold cyan]\n"
    )

    # Entity renames table
    entity_table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    entity_table.add_column("Old entity ID", style="dim")
    entity_table.add_column("")
    entity_table.add_column("New entity ID", style="cyan")
    for plan in device_plan.plans:
        entity_table.add_row(plan.old_entity_id, "→", plan.new_entity_id)
    console.print(entity_table)

    # Build location details (non-registry locations that have entity references)
    location_details: dict[tuple[str, str], dict[str, Any]] = {}
    for plan in device_plan.plans:
        for loc in plan.locations:
            if loc.context == "registry":
                continue
            key = (loc.context, loc.item_id)
            if key not in location_details:
                location_details[key] = {
                    "context": loc.context,
                    "label": CONTEXT_LABEL.get(loc.context, loc.context),
                    "name": loc.name,
                    "pairs": [],
                }
            location_details[key]["pairs"].append((plan.old_entity_id, plan.new_entity_id))

    # --- Auto-updated locations ---
    if location_details:
        console.print(
            f"\n  [bold]Will be updated automatically ({len(location_details)}):[/bold]\n"
        )
        for detail in location_details.values():
            n = len(detail["pairs"])
            ref_s = "reference" if n == 1 else "references"
            console.print(
                f"    [green]✓[/green]  [dim]{detail['label']:12}[/dim]"
                f"  {detail['name']}  [dim]({n} {ref_s})[/dim]"
            )

        # Show scanned dashboards with 0 references as a footer
        auto_dashboard_names = {
            d["name"] for d in location_details.values() if d["context"] == "lovelace"
        }
        scanned = device_plan.scanned_names or {}
        for db_name in scanned.get("dashboards", []):
            if db_name not in auto_dashboard_names:
                console.print(
                    f"    [dim]–[/dim]  [dim]{'dashboard':12}[/dim]"
                    f"  {db_name}  [dim](0 references — scanned, no matches)[/dim]"
                )
        # Show YAML-mode dashboards inline so the user knows they were checked
        for db_name in device_plan.failed_dashboards:
            console.print(
                f"    [dim]–[/dim]  [dim]{'dashboard':12}[/dim]"
                f"  {db_name}  [yellow]⚠ YAML mode — see manual steps below[/yellow]"
            )

        total = sum(len(d["pairs"]) for d in location_details.values())
        console.print(
            f"\n  [dim]{len(device_plan.plans)} entity(ies) · "
            f"{len(location_details)} location(s) · {total} change(s) to apply[/dim]"
        )
    else:
        # Show scanned locations with no references found
        scanned = device_plan.scanned_names or {}
        other_counts: list[str] = []
        for kind, names in scanned.items():
            if kind != "dashboards" and names:
                other_counts.append(f"{len(names)} {kind}")
        scanned_dashboards = scanned.get("dashboards", [])
        console.print(f"\n  [dim]{len(device_plan.plans)} entities to rename[/dim]")
        if other_counts or scanned_dashboards:
            console.print("\n  [dim]Scanned — no references found:[/dim]")
            if other_counts:
                console.print(f"    [dim]{', '.join(other_counts)}[/dim]")
            for db_name in scanned_dashboards:
                console.print(f"    [dim]dashboard:  {db_name}[/dim]")

    console.print(
        f"  [dim]–[/dim]  [dim]{'energy':12}[/dim]"
        f"  [dim](auto-updated by HA on entity rename)[/dim]"
    )

    # --- Manual steps for YAML-mode / inaccessible dashboards ---
    if device_plan.failed_dashboards:
        n = len(device_plan.failed_dashboards)
        s = "s" if n != 1 else ""
        old_slug = slugify(device_plan.old_device_name)
        console.print(
            f"\n  [yellow bold]⚠  {n} dashboard{s} stored in YAML — cannot be updated automatically[/yellow bold]\n"
        )
        console.print(
            f"  [dim]Search your HA config files for [bold]{old_slug}[/bold]:[/dim]\n"
            f"  [dim]• Studio Code Server add-on → [bold]Ctrl+Shift+F[/bold][/dim]\n"
            f'  [dim]• SSH/terminal → [bold]grep -rn "{old_slug}" /config/ --include="*.yaml"[/bold][/dim]\n'
        )
        pairs = [(p.old_entity_id, p.new_entity_id) for p in device_plan.plans]
        paths = device_plan.failed_dashboard_paths or [None] * len(device_plan.failed_dashboards)
        for i, (name, url_path) in enumerate(zip(device_plan.failed_dashboards, paths), 1):
            url = f"/lovelace/{url_path}" if url_path else "/lovelace"
            console.print(f"  [yellow][ ] {i}.[/yellow]  [bold]{name}[/bold]  [dim]{url}[/dim]")
            console.print("\n  [dim]Find and replace:[/dim]\n")
            replace_table = Table(
                show_header=True, header_style="bold dim", box=None, padding=(0, 2)
            )
            replace_table.add_column("Find", style="dim")
            replace_table.add_column("")
            replace_table.add_column("Replace with", style="cyan")
            for old_id, new_id in pairs:
                replace_table.add_row(old_id, "→", new_id)
            console.print(replace_table)
            if i < len(device_plan.failed_dashboards):
                console.print()


# ---------------------------------------------------------------------------
# Device rename execution
# ---------------------------------------------------------------------------


async def execute_device_rename(
    ha_client: HAClient,
    device_plan: DeviceRenamePlan,
    *,
    z2m_client: Z2MClient | None = None,
    z2m_friendly_name: str | None = None,
) -> None:
    """Apply all changes from a device rename plan.

    Renames the device, all entities in the registry, then updates each affected
    automation/script/scene/dashboard exactly once — applying all entity substitutions
    in a single pass to avoid stale-config overwrites.
    """
    console.print("  Renaming device in HA registry...", end=" ")
    await ha_client.rename_device_name(device_plan.device_id, device_plan.new_device_name)
    console.print("[green]✓[/green]")

    for plan in device_plan.plans:
        console.print(
            f"  Renaming [dim]{plan.old_entity_id}[/dim] → [cyan]{plan.new_entity_id}[/cyan]...",
            end=" ",
        )
        await ha_client.rename_entity_id(plan.old_entity_id, plan.new_entity_id)
        console.print("[green]✓[/green]")

    # Collect all (old, new) pairs per location, apply all substitutions once
    location_updates: dict[tuple[str, str], dict[str, Any]] = {}
    for plan in device_plan.plans:
        for loc in plan.locations:
            if loc.context == "registry":
                continue
            key = (loc.context, loc.item_id)
            if key not in location_updates:
                location_updates[key] = {
                    "context": loc.context,
                    "item_id": loc.item_id,
                    "name": loc.name,
                    "config": loc.raw_config,
                    "pairs": [],
                }
            location_updates[key]["pairs"].append((plan.old_entity_id, plan.new_entity_id))

    for update in location_updates.values():
        context = update["context"]
        item_id = update["item_id"]
        label = CONTEXT_LABEL.get(context, context)
        console.print(f"  Updating {label} [dim]{update['name']!r}[/dim]...", end=" ")
        warning = await apply_location_update(
            ha_client, context, item_id, update["config"], update["pairs"]
        )
        if warning:
            console.print(f"[yellow]{warning}[/yellow]")
        else:
            console.print("[green]✓[/green]")

    if z2m_client and z2m_friendly_name:
        console.print("  Renaming device in Z2M...", end=" ")
        try:
            await z2m_client.rename_device(z2m_friendly_name, device_plan.new_device_name)
        except (RuntimeError, OSError) as exc:
            console.print(f"[yellow]⚠ skipped ({exc})[/yellow]")
        else:
            console.print("[green]✓[/green]")
            console.print("  Reloading Zigbee2MQTT integration in HA...", end=" ")
            try:
                entry_id = await ha_client.get_z2m_config_entry_id()
                if entry_id:
                    await ha_client.reload_config_entry(entry_id)
                    console.print("[green]✓[/green]")
                else:
                    console.print("[yellow]⚠ skipped (Z2M config entry not found)[/yellow]")
            except (RuntimeError, OSError) as exc:
                console.print(f"[yellow]⚠ skipped ({exc})[/yellow]")


# ---------------------------------------------------------------------------
# UI prompt functions (thin wrappers — mock these in tests)
# ---------------------------------------------------------------------------


async def _prompt_new_device_name(current_name: str) -> str | None:
    """Prompt for a new device name. Returns None if the user cancels."""
    return await questionary.text(
        "New device name:",
        default=current_name,
        style=_STYLE,
    ).unsafe_ask_async()


async def _prompt_odd_entity_action(eid: str, suggested: str) -> tuple[str, str | None]:
    """Ask how to handle one entity that doesn't follow the device name pattern.

    Returns (action, value) where action is "suggested" | "custom" | "skip".
    """
    return await questionary.select(
        "How should this entity be handled?",
        choices=[
            questionary.Choice(f"Rename to suggested: {suggested}", value=("suggested", suggested)),
            questionary.Choice("Enter a custom new entity ID", value=("custom", None)),
            questionary.Choice("Skip (keep current entity ID)", value=("skip", None)),
        ],
        style=_STYLE,
    ).unsafe_ask_async()


async def _prompt_custom_entity_id() -> str | None:
    """Prompt for a custom replacement entity ID. Returns None if the user cancels."""
    return await questionary.text("New entity ID:", style=_STYLE).unsafe_ask_async()


async def _prompt_apply_confirm() -> bool:
    """Ask whether to apply the device rename. Returns False if the user declines."""
    return (
        await questionary.confirm(
            "Apply these changes?", default=False, style=_STYLE
        ).unsafe_ask_async()
        or False
    )


async def _prompt_z2m_confirm(friendly_name: str) -> bool:
    """Ask whether to also rename the device in Z2M."""
    return (
        await questionary.confirm(
            f"Also rename in Z2M? (current friendly name: '{friendly_name}')",
            default=True,
            style=_STYLE,
        ).unsafe_ask_async()
        or False
    )


# ---------------------------------------------------------------------------
# Device rename entry point — private stage helpers
# ---------------------------------------------------------------------------


class _UserAbort(Exception):
    """Raised when the user explicitly aborts (e.g. accepts an empty new name prompt)."""


async def _resolve_device_and_name(
    ha_client: HAClient,
    old_name: str | None,
    new_name: str | None,
    device_filter: str | None,
) -> tuple[dict[str, Any], str, str]:
    """Resolve the device and the new name interactively or from arguments.

    Returns (device, actual_name, new_name).
    Raises _UserAbort when the user explicitly cancels the new-name prompt.
    Raises ValueError on unrecoverable errors (device not found, no TTY).
    Prints all user-facing output.
    """
    if old_name is None:
        if not sys.stdin.isatty():
            console.print(
                "[red]Error:[/red] stdin is not a TTY. "
                "Provide a device name as the first argument to run non-interactively."
            )
            raise ValueError("no_tty")
        device = await pick_device_interactively(ha_client, device_filter=device_filter)
        if device is None:
            raise ValueError("picker_cancelled")
        actual_name = device_display_name(device)
    else:
        console.print(f"\nSearching for device [bold]{old_name!r}[/bold]...", end=" ")
        device = await find_device(ha_client, old_name, device_filter=device_filter)
        if device is None:
            console.print(f"\n[red]Error:[/red] No device matching '{old_name}' found.")
            raise ValueError("device_not_found")
        actual_name = device_display_name(device)
        console.print("[green]✓[/green]")
        if actual_name.lower() != old_name.lower():
            console.print(f"  [dim]Matched: [bold]{actual_name}[/bold][/dim]")

    if new_name is None:
        if not sys.stdin.isatty():
            console.print(
                "[red]Error:[/red] stdin is not a TTY. "
                "Provide a new device name as the second argument to run non-interactively."
            )
            raise ValueError("no_tty_for_new_name")
        new_name = await _prompt_new_device_name(actual_name)
        if not new_name or not new_name.strip():
            console.print("[dim]Aborted.[/dim]")
            raise _UserAbort
        new_name = new_name.strip()

    return device, actual_name, new_name


async def resolve_odd_entities(
    odd_entities: list[dict[str, Any]],
    entity_pairs: list[tuple[str, str]],
    new_slug: str,
    *,
    apply: bool = False,
) -> list[tuple[str, str]]:
    """Interactively prompt the user to handle entities that don't follow the device name pattern.

    When *apply* is True and stdin is not a TTY, automatically accepts the suggested entity ID
    for each odd entity instead of skipping them.

    Returns an extended entity_pairs list (odd entities appended according to user choices).
    """
    console.print(
        f"\n  [yellow]{len(odd_entities)} entity(ies) don't follow the device name pattern:[/yellow]"
    )
    if not sys.stdin.isatty():
        if apply:
            result = list(entity_pairs)
            for entity in odd_entities:
                suggested = _suggest_entity_id(entity, new_slug)
                console.print(
                    f"  [dim]Auto-accepting suggested rename (--apply):[/dim] "
                    f"[bold]{entity['entity_id']}[/bold] → [cyan]{suggested}[/cyan]"
                )
                result.append((entity["entity_id"], suggested))
            return result
        console.print("  [dim]Skipping odd entities (no TTY for interactive prompt).[/dim]")
        return entity_pairs

    result = list(entity_pairs)
    for entity in odd_entities:
        eid = entity["entity_id"]
        suggested = _suggest_entity_id(entity, new_slug)
        console.print(f"\n  [dim]Entity:[/dim] [bold]{eid}[/bold]")
        action, _value = await _prompt_odd_entity_action(eid, suggested)
        if action == "suggested":
            result.append((eid, suggested))
        elif action == "custom":
            custom = await _prompt_custom_entity_id()
            if custom and custom.strip():
                result.append((eid, custom.strip()))
    return result


async def build_device_rename_plan(
    ha_client: HAClient,
    device: dict[str, Any],
    actual_name: str,
    new_name: str,
    entity_pairs: list[tuple[str, str]],
) -> DeviceRenamePlan:
    """Fetch HA config snapshot and build per-entity plans.

    Returns a DeviceRenamePlan ready for display and execution.
    Raises ValueError("no_valid_plans") when no entity renames can be applied.
    """
    console.print("\nScanning for references...", end=" ")
    snapshot = await fetch_ha_snapshot(ha_client)
    console.print("[green]✓[/green]")

    existing_ids = {e["entity_id"] for e in snapshot.entity_registry}
    plans: list[RenamePlan] = []
    for old_eid, new_eid in entity_pairs:
        if old_eid not in existing_ids:
            console.print(
                f"  [yellow]Warning:[/yellow] '{old_eid}' not found in registry — skipped."
            )
            continue
        if new_eid in existing_ids:
            console.print(f"  [yellow]Warning:[/yellow] '{new_eid}' already exists — skipped.")
            continue
        plans.append(build_rename_plan_from_snapshot(snapshot, old_eid, new_eid))

    if not plans:
        console.print("[red]No valid entity renames to apply.[/red]")
        raise ValueError("no_valid_plans")

    scanned_names: dict[str, list[str]] = {}
    if snapshot.automations:
        scanned_names["automations"] = [
            a.get("alias") or a.get("id", "?") for a in snapshot.automations
        ]
    if snapshot.scripts:
        scanned_names["scripts"] = [s.get("alias") or s.get("id", "?") for s in snapshot.scripts]
    if snapshot.scenes:
        scanned_names["scenes"] = [s.get("name") or s.get("id", "?") for s in snapshot.scenes]
    # Dashboards with real configs → scanned; YAML_MODE → failed (manual update needed);
    # None → not a Lovelace panel (silently dropped).
    dashboard_names = [
        snapshot.titles.get(p, p or "Default")
        for p, c in zip(snapshot.url_paths, snapshot.lovelace_configs)
        if c is not None and not is_yaml_mode(c)
    ]
    failed_dashboard_names = [
        snapshot.titles.get(p, p or "lovelace")
        for p, c in zip(snapshot.url_paths, snapshot.lovelace_configs)
        if is_yaml_mode(c)
    ]
    failed_dashboard_paths = [
        p for p, c in zip(snapshot.url_paths, snapshot.lovelace_configs) if is_yaml_mode(c)
    ]
    if dashboard_names:
        scanned_names["dashboards"] = dashboard_names

    return DeviceRenamePlan(
        device_id=device["id"],
        old_device_name=actual_name,
        new_device_name=new_name,
        plans=plans,
        scanned_names=scanned_names,
        failed_dashboards=failed_dashboard_names,
        failed_dashboard_paths=failed_dashboard_paths,
    )


async def _build_device_rename_plan(
    ha_client: HAClient,
    ha_url: str,
    token: str,
    verify_ssl: bool,
    device: dict[str, Any],
    actual_name: str,
    new_name: str,
    entity_pairs: list[tuple[str, str]],
    apply: bool,
) -> tuple[DeviceRenamePlan, "Z2MClient | None", str | None]:
    """Fetch HA config snapshot, build per-entity plans, and optionally prepare Z2M sync.

    Returns (device_plan, z2m_client, z2m_friendly_name).
    z2m_client / z2m_friendly_name are None when Z2M is not configured, unreachable,
    or when --apply was passed (Z2M sync requires interactive confirmation).
    """
    device_plan = await build_device_rename_plan(
        ha_client, device, actual_name, new_name, entity_pairs
    )

    # Optional Z2M sync — best-effort, skipped silently if Z2M is not configured
    z2m_client: Z2MClient | None = None
    z2m_friendly_name: str | None = None
    ieee = _ieee_from_ha_device(device)
    if ieee and not apply:
        try:
            z2m_url, mqtt_topic = load_z2m_config()
            z2m_client = Z2MClient(ha_url, token, z2m_url, verify_ssl, mqtt_topic)
            z2m_dev = await z2m_client.get_device_by_ieee(ieee)
            if z2m_dev:
                z2m_friendly_name = z2m_dev.get("friendly_name")
        except (ValueError, RuntimeError, OSError):
            pass  # Z2M not configured or unreachable — skip silently

    return device_plan, z2m_client, z2m_friendly_name


# ---------------------------------------------------------------------------
# Device rename entry point
# ---------------------------------------------------------------------------


async def run_rename_device(
    ha_url: str,
    token: str,
    verify_ssl: bool,
    old_name: str | None,
    new_name: str | None,
    apply: bool,
    device_filter: str | None = None,
) -> bool:
    ha_client = HAClient(ha_url, token, verify_ssl)

    if device_filter is not None and device_filter not in _VALID_DEVICE_FILTERS:
        console.print(
            f"[red]Error:[/red] Unknown filter {device_filter!r}. "
            f"Valid values: {', '.join(sorted(_VALID_DEVICE_FILTERS))}"
        )
        return False

    # Stage A: Resolve device + new name
    try:
        device, actual_name, new_name = await _resolve_device_and_name(
            ha_client, old_name, new_name, device_filter
        )
    except _UserAbort:
        return True
    except ValueError:
        return False

    # Stage B: Fetch entities and resolve odd ones interactively
    console.print("  Fetching entities...", end=" ")
    entities = await ha_client.get_entities_for_device(device["id"])
    console.print(f"[green]{len(entities)} found[/green]")

    if not entities:
        console.print("[yellow]No entities found for this device.[/yellow]")
        return True

    old_slug = slugify(actual_name)
    new_slug = slugify(new_name)
    entity_pairs, odd_entities = compute_entity_pairs(entities, old_slug, new_slug)

    if odd_entities:
        entity_pairs = await resolve_odd_entities(odd_entities, entity_pairs, new_slug, apply=apply)

    if not entity_pairs:
        console.print("\n[yellow]No entities to rename.[/yellow]")
        return True

    # Stage C: Build device rename plan (snapshot + per-entity plans + Z2M check)
    try:
        device_plan, z2m_client, z2m_friendly_name = await _build_device_rename_plan(
            ha_client, ha_url, token, verify_ssl, device, actual_name, new_name, entity_pairs, apply
        )
    except ValueError:
        return False

    # Stage D: Display plan + confirm + execute
    display_device_plan(device_plan)

    if not apply:
        if not sys.stdin.isatty():
            console.print(
                "[red]Error:[/red] stdin is not a TTY. "
                "Use [bold]--apply[/bold] to apply changes non-interactively."
            )
            return False

        if not await _prompt_apply_confirm():
            console.print("[dim]Aborted.[/dim]")
            return True

        if z2m_client and z2m_friendly_name:
            if not await _prompt_z2m_confirm(z2m_friendly_name):
                z2m_client = None
                z2m_friendly_name = None

    console.print()
    await execute_device_rename(
        ha_client,
        device_plan,
        z2m_client=z2m_client,
        z2m_friendly_name=z2m_friendly_name,
    )
    console.print(
        f"\n[green]✓[/green] Renamed device [bold]{actual_name}[/bold]"
        f" → [bold cyan]{new_name}[/bold cyan]"
        f" ([bold]{len(device_plan.plans)}[/bold] entities updated)"
    )

    return True


def rename_device_command(
    ha_url: str,
    token: str,
    verify_ssl: bool,
    old_name: str | None,
    new_name: str | None,
    apply: bool,
    device_filter: str | None = None,
) -> None:
    import typer  # noqa: PLC0415

    ok = asyncio.run(
        run_rename_device(ha_url, token, verify_ssl, old_name, new_name, apply, device_filter)
    )
    if not ok:
        raise typer.Exit(code=1)
