"""rename command — rename an HA entity ID and cascade the change everywhere."""

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


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class RenameLocation:
    context: str  # "registry", "automation", "script", "scene", "lovelace"
    name: str  # human label
    item_id: str  # automation/script/scene ID, or lovelace url_path ("" = default dashboard)
    occurrences: int
    raw_config: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class RenamePlan:
    old_entity_id: str
    new_entity_id: str
    locations: list[RenameLocation]
    scanned_dashboard_names: list[str] = field(default_factory=list)
    yaml_mode_dashboard_names: list[str] = field(default_factory=list)
    yaml_mode_dashboard_paths: list[str | None] = field(default_factory=list)

    @property
    def total_occurrences(self) -> int:
        return sum(loc.occurrences for loc in self.locations)


# ---------------------------------------------------------------------------
# Tree walkers
# ---------------------------------------------------------------------------


def _count_occurrences(node: Any, target_id: str) -> int:
    """Count exact string matches of target_id in a nested dict/list structure.

    Checks both dict keys and values to handle scene entity dicts where the
    entity ID is used as a key.
    """
    if isinstance(node, str):
        return 1 if node == target_id else 0
    if isinstance(node, dict):
        key_hits = sum(1 for k in node if k == target_id)
        val_hits = sum(_count_occurrences(v, target_id) for v in node.values())
        return key_hits + val_hits
    if isinstance(node, list):
        return sum(_count_occurrences(item, target_id) for item in node)
    return 0


def _deep_replace(node: Any, old_id: str, new_id: str) -> Any:
    """Recursively replace all exact occurrences of old_id with new_id (keys and values)."""
    if isinstance(node, str):
        return new_id if node == old_id else node
    if isinstance(node, dict):
        return {
            (new_id if k == old_id else k): _deep_replace(v, old_id, new_id)
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [_deep_replace(item, old_id, new_id) for item in node]
    return node


# ---------------------------------------------------------------------------
# Dashboard discovery
# ---------------------------------------------------------------------------

# HA panels that are never Lovelace dashboards — skip without attempting a config fetch.
_NON_LOVELACE_PANELS = frozenset(
    {
        "energy",
        "history",
        "logbook",
        "map",
        "developer-tools",
        "profile",
        "config",
        "hacs",
        "notifications",
        "todo",
    }
)


def _discover_dashboards(
    panels: dict[str, Any],
) -> tuple[list[str | None], dict[str | None, str]]:
    """Return (url_paths, titles) for all potential Lovelace dashboards from panels.

    Excludes known non-Lovelace panels by URL. The component_name check is intentionally
    omitted so that HACS/custom frontend panels (e.g. dashboard-mushroom) are included;
    panels without a valid lovelace config are silently dropped when the fetch returns None.
    """
    url_paths: list[str | None] = []
    titles: dict[str | None, str] = {}

    for panel_key, panel in panels.items():
        panel_url = panel.get("url_path") or panel_key
        if panel_url in _NON_LOVELACE_PANELS:
            continue
        if panel_url in ("lovelace", ""):
            lv_path: str | None = None
            title = panel.get("title") or "Overview"
        else:
            lv_path = panel_url
            title = panel.get("title") or panel_url
        if lv_path not in url_paths:
            url_paths.append(lv_path)
            titles[lv_path] = title

    if None not in url_paths:
        url_paths.insert(0, None)
        titles[None] = "Overview"

    return url_paths, titles


# ---------------------------------------------------------------------------
# Plan builder
# ---------------------------------------------------------------------------


async def build_rename_plan(
    ha_client: HAClient,
    old_entity_id: str,
    new_entity_id: str,
) -> RenamePlan:
    """Scan all HA data and build a plan for renaming old_entity_id → new_entity_id."""
    entity_registry = await ha_client.get_entity_registry()
    existing_ids = {e["entity_id"] for e in entity_registry}

    if old_entity_id not in existing_ids:
        raise ValueError(f"Entity '{old_entity_id}' not found in the HA entity registry.")
    if new_entity_id in existing_ids:
        raise ValueError(f"Entity '{new_entity_id}' already exists in the HA entity registry.")

    automations, scripts, scenes, panels = await asyncio.gather(
        ha_client.get_automation_configs(),
        ha_client.get_scripts(),
        ha_client.get_scenes(),
        ha_client.get_panels(),
    )

    url_paths, titles = _discover_dashboards(panels)
    lovelace_configs = await asyncio.gather(*[ha_client.get_lovelace_config(p) for p in url_paths])

    locations: list[RenameLocation] = []

    # Registry rename is always included
    locations.append(
        RenameLocation(
            context="registry",
            name="HA entity registry",
            item_id=old_entity_id,
            occurrences=1,
        )
    )

    for auto in automations:
        count = _count_occurrences(auto, old_entity_id)
        if count:
            locations.append(
                RenameLocation(
                    context="automation",
                    name=auto.get("alias") or auto.get("id", "?"),
                    item_id=str(auto.get("id", "")),
                    occurrences=count,
                    raw_config=auto,
                )
            )

    for script in scripts:
        count = _count_occurrences(script, old_entity_id)
        if count:
            locations.append(
                RenameLocation(
                    context="script",
                    name=script.get("alias") or script.get("id", "?"),
                    item_id=str(script.get("id", "")),
                    occurrences=count,
                    raw_config=script,
                )
            )

    for scene in scenes:
        count = _count_occurrences(scene, old_entity_id)
        if count:
            locations.append(
                RenameLocation(
                    context="scene",
                    name=scene.get("name") or scene.get("id", "?"),
                    item_id=str(scene.get("id", "")),
                    occurrences=count,
                    raw_config=scene,
                )
            )

    scanned_dashboard_names: list[str] = []
    yaml_mode_dashboard_names: list[str] = []
    yaml_mode_dashboard_paths: list[str | None] = []

    for url_path, config in zip(url_paths, lovelace_configs, strict=True):
        if config is None:
            continue  # not a lovelace panel — silently drop
        title = titles.get(url_path, url_path or "Overview")
        if is_yaml_mode(config):
            yaml_mode_dashboard_names.append(title)
            yaml_mode_dashboard_paths.append(url_path)
            continue
        scanned_dashboard_names.append(title)
        count = _count_occurrences(config, old_entity_id)
        if count:
            locations.append(
                RenameLocation(
                    context="lovelace",
                    name=title,
                    item_id=url_path or "",
                    occurrences=count,
                    raw_config=config,
                )
            )

    return RenamePlan(
        old_entity_id=old_entity_id,
        new_entity_id=new_entity_id,
        locations=locations,
        scanned_dashboard_names=scanned_dashboard_names,
        yaml_mode_dashboard_names=yaml_mode_dashboard_names,
        yaml_mode_dashboard_paths=yaml_mode_dashboard_paths,
    )


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

_CONTEXT_LABEL: dict[str, str] = {
    "registry": "registry",
    "automation": "automation",
    "script": "script",
    "scene": "scene",
    "lovelace": "dashboard",
    "config_entry": "helper",
}


def display_plan(plan: RenamePlan) -> None:
    """Render the rename plan as a rich table."""
    console.print(
        f"\n  [bold]{plan.old_entity_id}[/bold]"
        f"  [dim]→[/dim]  "
        f"[bold cyan]{plan.new_entity_id}[/bold cyan]\n"
    )

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("Type", style="dim", width=12)
    table.add_column("Name")
    table.add_column("Hits", justify="right", style="dim")

    for loc in plan.locations:
        label = _CONTEXT_LABEL.get(loc.context, loc.context)
        table.add_row(label, loc.name, str(loc.occurrences))

    console.print(table)

    # Dashboard scan footer — 0-ref scanned dashboards and YAML-mode dashboards
    auto_dashboard_names = {loc.name for loc in plan.locations if loc.context == "lovelace"}
    for db_name in plan.scanned_dashboard_names:
        if db_name not in auto_dashboard_names:
            console.print(
                f"  [dim]–[/dim]  [dim]{'dashboard':12}[/dim]"
                f"  {db_name}  [dim](0 references — scanned, no matches)[/dim]"
            )
    for db_name in plan.yaml_mode_dashboard_names:
        console.print(
            f"  [dim]–[/dim]  [dim]{'dashboard':12}[/dim]"
            f"  {db_name}  [yellow]⚠ YAML mode — see manual steps below[/yellow]"
        )
    console.print(
        f"  [dim]–[/dim]  [dim]{'energy':12}[/dim]"
        f"  [dim](auto-updated by HA on entity rename)[/dim]"
    )

    non_registry = [loc for loc in plan.locations if loc.context != "registry"]
    total_refs = sum(loc.occurrences for loc in non_registry)
    console.print(
        f"\n  [dim]{len(non_registry)} location(s) · {total_refs} reference(s) to update[/dim]"
    )

    # Manual steps for YAML-mode dashboards
    if plan.yaml_mode_dashboard_names:
        n = len(plan.yaml_mode_dashboard_names)
        s = "s" if n != 1 else ""
        console.print(
            f"\n  [yellow bold]⚠  {n} dashboard{s} stored in YAML — cannot be updated automatically[/yellow bold]\n"
        )
        console.print(
            f"  [dim]Search your HA config files for [bold]{plan.old_entity_id}[/bold]:[/dim]\n"
            f"  [dim]• Studio Code Server add-on → [bold]Ctrl+Shift+F[/bold][/dim]\n"
            f'  [dim]• SSH/terminal → [bold]grep -rn "{plan.old_entity_id}" /config/ --include="*.yaml"[/bold][/dim]\n'
        )
        paths = plan.yaml_mode_dashboard_paths or [None] * len(plan.yaml_mode_dashboard_names)
        for i, (name, url_path) in enumerate(zip(plan.yaml_mode_dashboard_names, paths), 1):
            url = f"/lovelace/{url_path}" if url_path else "/lovelace"
            console.print(f"  [yellow][ ] {i}.[/yellow]  [bold]{name}[/bold]  [dim]{url}[/dim]")
            console.print("\n  [dim]Find and replace:[/dim]\n")
            replace_table = Table(
                show_header=True, header_style="bold dim", box=None, padding=(0, 2)
            )
            replace_table.add_column("Find", style="dim")
            replace_table.add_column("")
            replace_table.add_column("Replace with", style="cyan")
            replace_table.add_row(plan.old_entity_id, "→", plan.new_entity_id)
            console.print(replace_table)
            if i < len(plan.yaml_mode_dashboard_names):
                console.print()


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


async def execute_rename(ha_client: HAClient, plan: RenamePlan) -> None:
    """Apply all changes from the rename plan."""
    old = plan.old_entity_id
    new = plan.new_entity_id

    for loc in plan.locations:
        if loc.context == "registry":
            console.print("  Renaming entity in HA registry...", end=" ")
            await ha_client.rename_entity_id(old, new)
            console.print("[green]✓[/green]")

        elif loc.context == "automation":
            console.print(f"  Updating automation [dim]{loc.name!r}[/dim]...", end=" ")
            patched = _deep_replace(loc.raw_config, old, new)
            await ha_client.update_automation(loc.item_id, patched)
            console.print("[green]✓[/green]")

        elif loc.context == "script":
            console.print(f"  Updating script [dim]{loc.name!r}[/dim]...", end=" ")
            patched = _deep_replace(loc.raw_config, old, new)
            await ha_client.update_script(loc.item_id, patched)
            console.print("[green]✓[/green]")

        elif loc.context == "scene":
            console.print(f"  Updating scene [dim]{loc.name!r}[/dim]...", end=" ")
            patched = _deep_replace(loc.raw_config, old, new)
            await ha_client.update_scene(loc.item_id, patched)
            console.print("[green]✓[/green]")

        elif loc.context == "lovelace":
            url_path = loc.item_id or None
            console.print(f"  Updating dashboard [dim]{loc.name!r}[/dim]...", end=" ")
            patched = _deep_replace(loc.raw_config, old, new)
            await ha_client.save_lovelace_config(patched, url_path)
            console.print("[green]✓[/green]")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run_rename(
    ha_url: str,
    token: str,
    verify_ssl: bool,
    old_entity_id: str,
    new_entity_id: str,
    apply: bool,
) -> bool:
    ha_client = HAClient(ha_url, token, verify_ssl)

    console.print(f"\nScanning for references to [bold]{old_entity_id}[/bold]...", end=" ")
    try:
        plan = await build_rename_plan(ha_client, old_entity_id, new_entity_id)
    except ValueError as exc:
        console.print(f"\n[red]Error:[/red] {exc}")
        return False
    console.print("[green]✓[/green]")

    display_plan(plan)

    if not apply:
        if not sys.stdin.isatty():
            console.print(
                "[red]Error:[/red] stdin is not a TTY. "
                "Use [bold]--apply[/bold] to apply changes non-interactively."
            )
            return False

        confirmed = await questionary.confirm(
            "Apply these changes?",
            default=False,
            style=_STYLE,
        ).unsafe_ask_async()

        if not confirmed:
            console.print("[dim]Aborted.[/dim]")
            return True

    console.print()
    await execute_rename(ha_client, plan)
    console.print(
        f"\n[green]✓[/green] Renamed [bold]{old_entity_id}[/bold]"
        f" → [bold cyan]{new_entity_id}[/bold cyan]"
    )
    return True


def rename_command(
    ha_url: str,
    token: str,
    verify_ssl: bool,
    old_entity_id: str,
    new_entity_id: str,
    apply: bool,
) -> None:
    import typer  # noqa: PLC0415

    ok = asyncio.run(run_rename(ha_url, token, verify_ssl, old_entity_id, new_entity_id, apply))
    if not ok:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# Device-level rename helpers
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
# HA snapshot (fetches all config data in one pass for device rename)
# ---------------------------------------------------------------------------


@dataclass
class HASnapshot:
    entity_registry: list[dict[str, Any]]
    automations: list[dict[str, Any]]
    scripts: list[dict[str, Any]]
    scenes: list[dict[str, Any]]
    url_paths: list[str | None]
    titles: dict[str | None, str]
    lovelace_configs: list[dict[str, Any] | None]
    config_entries: list[dict[str, Any]]


async def fetch_ha_snapshot(ha_client: HAClient) -> HASnapshot:
    """Fetch all HA config data in parallel."""
    entity_registry, automations, scripts, scenes, panels, config_entries = await asyncio.gather(
        ha_client.get_entity_registry(),
        ha_client.get_automation_configs(),
        ha_client.get_scripts(),
        ha_client.get_scenes(),
        ha_client.get_panels(),
        ha_client.get_config_entries(),
    )
    url_paths, titles = _discover_dashboards(panels)
    lovelace_configs = list(
        await asyncio.gather(*[ha_client.get_lovelace_config(p) for p in url_paths])
    )
    return HASnapshot(
        entity_registry=entity_registry,
        automations=automations,
        scripts=scripts,
        scenes=scenes,
        url_paths=url_paths,
        titles=titles,
        lovelace_configs=lovelace_configs,
        config_entries=config_entries,
    )


def build_rename_plan_from_snapshot(
    snapshot: HASnapshot,
    old_entity_id: str,
    new_entity_id: str,
) -> RenamePlan:
    """Build a RenamePlan from pre-fetched HA data."""
    existing_ids = {e["entity_id"] for e in snapshot.entity_registry}
    if old_entity_id not in existing_ids:
        raise ValueError(f"Entity '{old_entity_id}' not found in the HA entity registry.")
    if new_entity_id in existing_ids:
        raise ValueError(f"Entity '{new_entity_id}' already exists in the HA entity registry.")

    locations: list[RenameLocation] = [
        RenameLocation(
            context="registry",
            name="HA entity registry",
            item_id=old_entity_id,
            occurrences=1,
        )
    ]

    for auto in snapshot.automations:
        count = _count_occurrences(auto, old_entity_id)
        if count:
            locations.append(
                RenameLocation(
                    context="automation",
                    name=auto.get("alias") or auto.get("id", "?"),
                    item_id=str(auto.get("id", "")),
                    occurrences=count,
                    raw_config=auto,
                )
            )

    for script in snapshot.scripts:
        count = _count_occurrences(script, old_entity_id)
        if count:
            locations.append(
                RenameLocation(
                    context="script",
                    name=script.get("alias") or script.get("id", "?"),
                    item_id=str(script.get("id", "")),
                    occurrences=count,
                    raw_config=script,
                )
            )

    for scene in snapshot.scenes:
        count = _count_occurrences(scene, old_entity_id)
        if count:
            locations.append(
                RenameLocation(
                    context="scene",
                    name=scene.get("name") or scene.get("id", "?"),
                    item_id=str(scene.get("id", "")),
                    occurrences=count,
                    raw_config=scene,
                )
            )

    for url_path, config in zip(snapshot.url_paths, snapshot.lovelace_configs, strict=True):
        if config is None or is_yaml_mode(config):
            continue
        count = _count_occurrences(config, old_entity_id)
        if count:
            title = snapshot.titles.get(url_path, url_path or "Overview")
            locations.append(
                RenameLocation(
                    context="lovelace",
                    name=title,
                    item_id=url_path or "",
                    occurrences=count,
                    raw_config=config,
                )
            )

    for entry in snapshot.config_entries:
        options = entry.get("options") or {}
        count = _count_occurrences(options, old_entity_id)
        if count:
            locations.append(
                RenameLocation(
                    context="config_entry",
                    name=entry.get("title") or entry.get("entry_id", "?"),
                    item_id=entry["entry_id"],
                    occurrences=count,
                    raw_config=options,
                )
            )

    return RenamePlan(
        old_entity_id=old_entity_id,
        new_entity_id=new_entity_id,
        locations=locations,
    )


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


def _device_display_name(device: dict[str, Any]) -> str:
    return device.get("name_by_user") or device.get("name") or device["id"]


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


async def find_device(
    ha_client: HAClient,
    name: str,
) -> dict[str, Any] | None:
    """Find a device by name. Exact match first, then substring. Prompts if ambiguous."""
    registry = await ha_client.get_device_registry()
    name_lower = name.lower()

    exact = [d for d in registry if _device_display_name(d).lower() == name_lower]
    if len(exact) == 1:
        return exact[0]

    partial = [d for d in registry if name_lower in _device_display_name(d).lower()]
    if not partial:
        return None
    if len(partial) == 1:
        return partial[0]

    choices = [questionary.Choice(_device_display_name(d), value=d) for d in partial]
    return await questionary.select(
        f"Multiple devices match '{name}'. Which one?",
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
                    "label": _CONTEXT_LABEL.get(loc.context, loc.context),
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
        config = update["config"]
        for old_eid, new_eid in update["pairs"]:
            config = _deep_replace(config, old_eid, new_eid)

        context = update["context"]
        item_id = update["item_id"]
        label = _CONTEXT_LABEL.get(context, context)
        console.print(f"  Updating {label} [dim]{update['name']!r}[/dim]...", end=" ")

        if context == "automation":
            await ha_client.update_automation(item_id, config)
        elif context == "script":
            await ha_client.update_script(item_id, config)
        elif context == "scene":
            await ha_client.update_scene(item_id, config)
        elif context == "lovelace":
            await ha_client.save_lovelace_config(config, item_id or None)
        elif context == "config_entry":
            await ha_client.update_config_entry_options(item_id, config)

        console.print("[green]✓[/green]")

    if z2m_client and z2m_friendly_name:
        console.print("  Renaming device in Z2M...", end=" ")
        try:
            await z2m_client.rename_device(z2m_friendly_name, device_plan.new_device_name)
            console.print("[green]✓[/green]")
        except Exception as exc:
            console.print(f"[yellow]⚠ skipped ({exc})[/yellow]")


# ---------------------------------------------------------------------------
# Device rename entry point
# ---------------------------------------------------------------------------


async def run_rename_device(
    ha_url: str,
    token: str,
    verify_ssl: bool,
    old_name: str,
    new_name: str,
    apply: bool,
) -> bool:
    ha_client = HAClient(ha_url, token, verify_ssl)

    # 1. Find device
    console.print(f"\nSearching for device [bold]{old_name!r}[/bold]...", end=" ")
    device = await find_device(ha_client, old_name)
    if device is None:
        console.print(f"\n[red]Error:[/red] No device matching '{old_name}' found.")
        return False
    actual_name = _device_display_name(device)
    console.print("[green]✓[/green]")
    if actual_name.lower() != old_name.lower():
        console.print(f"  [dim]Matched: [bold]{actual_name}[/bold][/dim]")

    # 2. Fetch entities for this device
    console.print("  Fetching entities...", end=" ")
    entities = await ha_client.get_entities_for_device(device["id"])
    console.print(f"[green]{len(entities)} found[/green]")

    if not entities:
        console.print("[yellow]No entities found for this device.[/yellow]")
        return True

    # 3. Compute entity pairs via slug substitution
    old_slug = slugify(actual_name)
    new_slug = slugify(new_name)
    entity_pairs, odd_entities = compute_entity_pairs(entities, old_slug, new_slug)

    # 4. Interactively resolve odd entities
    if odd_entities:
        console.print(
            f"\n  [yellow]{len(odd_entities)} entity(ies) don't follow the device name pattern:[/yellow]"
        )
        if not sys.stdin.isatty():
            console.print("  [dim]Skipping odd entities (no TTY for interactive prompt).[/dim]")
        else:
            for entity in odd_entities:
                eid = entity["entity_id"]
                suggested = _suggest_entity_id(entity, new_slug)
                console.print(f"\n  [dim]Entity:[/dim] [bold]{eid}[/bold]")
                choice = await questionary.select(
                    "How should this entity be handled?",
                    choices=[
                        questionary.Choice(
                            f"Rename to suggested: {suggested}", value=("suggested", suggested)
                        ),
                        questionary.Choice("Enter a custom new entity ID", value=("custom", None)),
                        questionary.Choice("Skip (keep current entity ID)", value=("skip", None)),
                    ],
                    style=_STYLE,
                ).unsafe_ask_async()
                action, value = choice
                if action == "suggested":
                    entity_pairs.append((eid, suggested))
                elif action == "custom":
                    custom = await questionary.text(
                        "New entity ID:", style=_STYLE
                    ).unsafe_ask_async()
                    if custom and custom.strip():
                        entity_pairs.append((eid, custom.strip()))

    if not entity_pairs:
        console.print("\n[yellow]No entities to rename.[/yellow]")
        return True

    # 5. Fetch all HA config data once and build per-entity plans
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
        return False

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

    device_plan = DeviceRenamePlan(
        device_id=device["id"],
        old_device_name=actual_name,
        new_device_name=new_name,
        plans=plans,
        scanned_names=scanned_names,
        failed_dashboards=failed_dashboard_names,
        failed_dashboard_paths=failed_dashboard_paths,
    )

    # Optional Z2M sync — best-effort, skipped silently if Z2M is not configured
    z2m_client: Z2MClient | None = None
    z2m_friendly_name: str | None = None
    ieee = _ieee_from_ha_device(device)
    if ieee:
        try:
            z2m_url, mqtt_topic = load_z2m_config()
            z2m_client = Z2MClient(ha_url, token, z2m_url, verify_ssl, mqtt_topic)
            z2m_dev = await z2m_client.get_device_by_ieee(ieee)
            if z2m_dev:
                z2m_friendly_name = z2m_dev.get("friendly_name")
        except Exception:
            pass  # Z2M not configured or unreachable — skip silently

    # 6. Display plan
    display_device_plan(device_plan)

    # 7. Confirm HA changes
    if not apply:
        if not sys.stdin.isatty():
            console.print(
                "[red]Error:[/red] stdin is not a TTY. "
                "Use [bold]--apply[/bold] to apply changes non-interactively."
            )
            return False
        confirmed = await questionary.confirm(
            "Apply these changes?", default=False, style=_STYLE
        ).unsafe_ask_async()
        if not confirmed:
            console.print("[dim]Aborted.[/dim]")
            return True

        # 7b. Separately ask about Z2M sync
        if z2m_client and z2m_friendly_name:
            rename_z2m = await questionary.confirm(
                f"Also rename in Z2M? (current friendly name: '{z2m_friendly_name}')",
                default=True,
                style=_STYLE,
            ).unsafe_ask_async()
            if not rename_z2m:
                z2m_client = None
                z2m_friendly_name = None
    else:
        # --apply mode: skip Z2M (cannot prompt)
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
        f" ([bold]{len(plans)}[/bold] entities updated)"
    )

    return True


def rename_device_command(
    ha_url: str,
    token: str,
    verify_ssl: bool,
    old_name: str,
    new_name: str,
    apply: bool,
) -> None:
    import typer  # noqa: PLC0415

    ok = asyncio.run(run_rename_device(ha_url, token, verify_ssl, old_name, new_name, apply))
    if not ok:
        raise typer.Exit(code=1)
