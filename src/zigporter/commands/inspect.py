import asyncio
from dataclasses import dataclass, field
from typing import Any

import questionary
from rich.console import Console
from rich.panel import Panel

from zigporter.entity_refs import collect_config_entity_ids
from zigporter.ha_client import HAClient
from zigporter.ui import QUESTIONARY_STYLE
from zigporter.utils import normalize_ieee

console = Console()

_STYLE = QUESTIONARY_STYLE


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class DashboardRef:
    dashboard_title: str
    view_title: str
    card_type: str
    card_title: str | None
    matched_entities: list[str] = field(default_factory=list)


@dataclass
class DeviceDeps:
    ieee: str
    name: str
    manufacturer: str | None
    model: str | None
    area_name: str | None
    entities: list[str]  # entity_ids
    automations: list[dict[str, Any]]
    scripts: list[dict[str, Any]]
    scenes: list[dict[str, Any]]
    dashboard_refs: list[DashboardRef]


# ---------------------------------------------------------------------------
# Lovelace walker
# ---------------------------------------------------------------------------


def _collect_lovelace_entities(node: Any) -> set[str]:
    """Recursively collect entity IDs from a Lovelace card/view tree."""
    ids: set[str] = set()
    if isinstance(node, str):
        if "." in node and not node.startswith("http"):
            ids.add(node)
    elif isinstance(node, dict):
        for key in ("entity", "entity_id"):
            val = node.get(key)
            if isinstance(val, str):
                ids.add(val)
            elif isinstance(val, list):
                ids.update(v for v in val if isinstance(v, str))
        # Recurse into all values (handles cards, elements, rows, etc.)
        for val in node.values():
            ids.update(_collect_lovelace_entities(val))
    elif isinstance(node, list):
        for item in node:
            ids.update(_collect_lovelace_entities(item))
    return ids


def _cards_from_view(view: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract top-level cards from a view regardless of layout type.

    HA has two layouts:
    - Classic: view.cards  (list of card dicts)
    - Sections (2024+): view.sections[*].cards  (cards nested inside sections)
    Both can exist on the same dashboard so we collect from both.
    """
    cards: list[dict[str, Any]] = list(view.get("cards", []))
    for section in view.get("sections", []):
        cards.extend(section.get("cards", []))
    return cards


def _scan_dashboard(
    config: dict[str, Any],
    dashboard_title: str,
    target_ids: set[str],
) -> list[DashboardRef]:
    """Walk a dashboard config and return one DashboardRef per matching card."""
    refs: list[DashboardRef] = []
    for view in config.get("views", []):
        view_title = view.get("title") or view.get("path") or "?"
        for card in _cards_from_view(view):
            matched = _collect_lovelace_entities(card) & target_ids
            if matched:
                refs.append(
                    DashboardRef(
                        dashboard_title=dashboard_title,
                        view_title=str(view_title),
                        card_type=card.get("type", "?"),
                        card_title=card.get("title"),
                        matched_entities=sorted(matched),
                    )
                )
    return refs


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------


async def fetch_all_data(ha_client: HAClient) -> dict[str, Any]:
    """Fetch all HA data needed for the inspect command in parallel."""
    ws_data = await ha_client.get_all_ws_data()

    panels_data, scripts, scenes = await asyncio.gather(
        ha_client.get_panels(),
        ha_client.get_scripts(),
        ha_client.get_scenes(),
    )

    # Discover Lovelace dashboards from the full panel registry.
    # get_panels() returns ALL registered panels regardless of storage/yaml mode,
    # unlike lovelace/dashboards which only returns storage-mode dashboards.
    dashboard_url_paths: list[str | None] = []
    dashboard_titles: dict[str | None, str] = {}

    for panel_key, panel in panels_data.items():
        if panel.get("component_name") != "lovelace":
            continue
        panel_url = panel.get("url_path") or panel_key
        # The default Lovelace dashboard is registered as "lovelace";
        # lovelace/config uses url_path=None to refer to it.
        if panel_url in ("lovelace", ""):
            lv_path: str | None = None
            title = panel.get("title") or "Default"
        else:
            lv_path = panel_url
            title = panel.get("title") or panel_url
        if lv_path not in dashboard_url_paths:
            dashboard_url_paths.append(lv_path)
            dashboard_titles[lv_path] = title

    # Always include the default dashboard even if panels returned nothing
    if None not in dashboard_url_paths:
        dashboard_url_paths.insert(0, None)
        dashboard_titles[None] = "Default"

    lovelace_configs = await asyncio.gather(
        *[ha_client.get_lovelace_config(p) for p in dashboard_url_paths]
    )

    return {
        **ws_data,
        "scripts": scripts,
        "scenes": scenes,
        "lovelace": list(zip(dashboard_url_paths, lovelace_configs, strict=True)),
        "dashboard_titles": dashboard_titles,
        "_panels_data": panels_data,
    }


async def show_migrate_inspect_summary(
    entity_ids: list[str],
    ha_client: HAClient,
) -> None:
    """Show entities and dashboard cards for a freshly migrated device.

    Called from the migration wizard before the validate step.
    Fetches only dashboard data — entity IDs are provided by the caller.
    """
    if not entity_ids:
        return

    target = set(entity_ids)

    panels_data = await ha_client.get_panels()

    dashboard_url_paths: list[str | None] = []
    dashboard_titles: dict[str | None, str] = {}

    for panel_key, panel in panels_data.items():
        if panel.get("component_name") != "lovelace":
            continue
        panel_url = panel.get("url_path") or panel_key
        if panel_url in ("lovelace", ""):
            lv_path: str | None = None
            title = panel.get("title") or "Default"
        else:
            lv_path = panel_url
            title = panel.get("title") or panel_url
        if lv_path not in dashboard_url_paths:
            dashboard_url_paths.append(lv_path)
            dashboard_titles[lv_path] = title

    if None not in dashboard_url_paths:
        dashboard_url_paths.insert(0, None)
        dashboard_titles[None] = "Default"

    lovelace_configs = await asyncio.gather(
        *[ha_client.get_lovelace_config(p) for p in dashboard_url_paths]
    )

    dashboard_refs: list[DashboardRef] = []
    for url_path, config in zip(dashboard_url_paths, lovelace_configs, strict=True):
        if config is None:
            continue
        title = dashboard_titles.get(url_path, url_path or "Default")
        dashboard_refs.extend(_scan_dashboard(config, title, target))

    console.print(f"\n[bold]Entities[/bold] ({len(entity_ids)})")
    for eid in sorted(entity_ids):
        console.print(f"  [dim]{eid}[/dim]")

    if dashboard_refs:
        console.print(f"\n[bold]Dashboards[/bold] ({len(dashboard_refs)} cards)")
        for ref in dashboard_refs:
            card_label = f"{ref.card_type} card"
            if ref.card_title:
                card_label += f' "{ref.card_title}"'
            console.print(
                f"  [cyan]□[/cyan]  {ref.dashboard_title} "
                f"[dim]›[/dim] {ref.view_title} "
                f"[dim]›[/dim] {card_label}"
            )
            for eid in ref.matched_entities:
                console.print(f"       [dim]{eid}[/dim]")
    else:
        console.print("\n  [dim]No dashboard cards found referencing these entities.[/dim]")


# ---------------------------------------------------------------------------
# Dependency builder
# ---------------------------------------------------------------------------


def build_deps(
    ieee: str,
    all_data: dict[str, Any],
) -> DeviceDeps | None:
    """Assemble a DeviceDeps for the given IEEE address from pre-fetched data."""
    norm = normalize_ieee(ieee)

    # Find ZHA device entry
    zha_device = next(
        (d for d in all_data["zha_devices"] if normalize_ieee(d.get("ieee", "")) == norm),
        None,
    )
    if zha_device is None:
        return None

    device_id = zha_device.get("device_reg_id", "")
    area_map = {a["area_id"]: a["name"] for a in all_data["area_registry"]}
    dr_entry = next((d for d in all_data["device_registry"] if d["id"] == device_id), {})
    area_name = area_map.get(dr_entry.get("area_id", ""))

    # Entities for this device
    entity_ids = [
        e["entity_id"] for e in all_data["entity_registry"] if e.get("device_id") == device_id
    ]
    target = set(entity_ids)

    # Automations
    automations = [
        a for a in all_data["automation_configs"] if collect_config_entity_ids(a) & target
    ]

    # Scripts
    scripts = [s for s in all_data["scripts"] if collect_config_entity_ids(s) & target]

    # Scenes
    scenes = [s for s in all_data["scenes"] if set(s.get("entities", {}).keys()) & target]

    # Lovelace dashboard refs
    dashboard_refs: list[DashboardRef] = []
    for url_path, config in all_data["lovelace"]:
        if config is None:
            continue
        title = all_data["dashboard_titles"].get(url_path, url_path or "Default")
        dashboard_refs.extend(_scan_dashboard(config, title, target))

    name = zha_device.get("user_given_name") or zha_device.get("name", ieee)

    return DeviceDeps(
        ieee=ieee,
        name=name,
        manufacturer=zha_device.get("manufacturer"),
        model=zha_device.get("model"),
        area_name=area_name,
        entities=sorted(entity_ids),
        automations=automations,
        scripts=scripts,
        scenes=scenes,
        dashboard_refs=dashboard_refs,
    )


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------


def show_report(deps: DeviceDeps) -> None:
    meta_parts = [f"IEEE: [dim]{deps.ieee}[/dim]"]
    if deps.area_name:
        meta_parts.append(f"Area: [bold]{deps.area_name}[/bold]")
    if deps.model:
        meta_parts.append(f"Model: {deps.model}")
    if deps.manufacturer:
        meta_parts.append(f"Manufacturer: {deps.manufacturer}")

    console.print(
        Panel(
            "   ".join(meta_parts),
            title=f"[bold]{deps.name}[/bold]",
            border_style="cyan",
        )
    )

    # Entities
    if deps.entities:
        console.print(f"\n[bold]Entities[/bold] ({len(deps.entities)})")
        for eid in deps.entities:
            console.print(f"  [dim]{eid}[/dim]")

    # Automations
    if deps.automations:
        console.print(f"\n[bold]Automations[/bold] ({len(deps.automations)})")
        for auto in deps.automations:
            alias = auto.get("alias") or auto.get("id", "?")
            refs = sorted(collect_config_entity_ids(auto) & set(deps.entities))
            console.print(f"  [cyan]□[/cyan]  {alias}")
            for eid in refs:
                console.print(f"       [dim]{eid}[/dim]")

    # Scripts
    if deps.scripts:
        console.print(f"\n[bold]Scripts[/bold] ({len(deps.scripts)})")
        for script in deps.scripts:
            name = script.get("alias") or script.get("id", "?")
            refs = sorted(collect_config_entity_ids(script) & set(deps.entities))
            console.print(f"  [cyan]□[/cyan]  {name}")
            for eid in refs:
                console.print(f"       [dim]{eid}[/dim]")

    # Scenes
    if deps.scenes:
        console.print(f"\n[bold]Scenes[/bold] ({len(deps.scenes)})")
        for scene in deps.scenes:
            name = scene.get("name") or scene.get("id", "?")
            refs = sorted(set(scene.get("entities", {}).keys()) & set(deps.entities))
            console.print(f"  [cyan]□[/cyan]  {name}")
            for eid in refs:
                console.print(f"       [dim]{eid}[/dim]")

    # Dashboards
    if deps.dashboard_refs:
        console.print(f"\n[bold]Dashboards[/bold] ({len(deps.dashboard_refs)} cards)")
        for ref in deps.dashboard_refs:
            card_label = f"{ref.card_type} card"
            if ref.card_title:
                card_label += f' "{ref.card_title}"'
            console.print(
                f"  [cyan]□[/cyan]  {ref.dashboard_title} "
                f"[dim]›[/dim] {ref.view_title} "
                f"[dim]›[/dim] {card_label}"
            )
            for eid in ref.matched_entities:
                console.print(f"       [dim]{eid}[/dim]")

    # Summary
    summary_parts = [f"[bold]{len(deps.entities)}[/bold] entities"]
    if deps.automations:
        summary_parts.append(f"[bold]{len(deps.automations)}[/bold] automations")
    if deps.scripts:
        summary_parts.append(f"[bold]{len(deps.scripts)}[/bold] scripts")
    if deps.scenes:
        summary_parts.append(f"[bold]{len(deps.scenes)}[/bold] scenes")
    if deps.dashboard_refs:
        summary_parts.append(f"[bold]{len(deps.dashboard_refs)}[/bold] dashboard cards")

    console.print()
    console.rule("   ".join(summary_parts))


# ---------------------------------------------------------------------------
# Device picker
# ---------------------------------------------------------------------------


async def _pick_device(
    zha_devices: list[dict[str, Any]],
    device_registry: list[dict[str, Any]],
    area_map: dict[str, str],
) -> str | None:
    """Interactive device picker grouped by area, matching the migrate picker style."""
    dr_by_id = {d["id"]: d for d in device_registry}

    # Enrich each ZHA device with its resolved area name
    enriched: list[tuple[dict[str, Any], str]] = []
    for dev in zha_devices:
        dr_entry = dr_by_id.get(dev.get("device_reg_id", ""), {})
        area_name = area_map.get(dr_entry.get("area_id", ""), "")
        enriched.append((dev, area_name))

    # Sort: area alphabetically (no-area → end), then device name within area
    enriched.sort(
        key=lambda x: (x[1] or "\xff", x[0].get("user_given_name") or x[0].get("name", ""))
    )

    if not enriched:
        console.print("[yellow]No ZHA devices found.[/yellow]")
        return None

    choices: list = []
    current_area: object = object()
    for dev, area_name in enriched:
        if area_name != current_area:
            current_area = area_name
            heading = f" {area_name or 'No area'} "
            choices.append(
                questionary.Separator(f"{'─' * 4}{heading}{'─' * max(0, 48 - len(heading))}")
            )
        name = dev.get("user_given_name") or dev.get("name", dev.get("ieee", "?"))
        model = dev.get("model") or ""
        label = f"  {name:<40} {model}"
        choices.append(questionary.Choice(title=label, value=dev.get("ieee")))

    return await questionary.select(
        "Select a device to inspect:",
        choices=choices,
        use_indicator=True,
        style=_STYLE,
    ).unsafe_ask_async()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _debug_lovelace(all_data: dict[str, Any]) -> None:
    """Print a diagnostic summary of what was fetched from Lovelace."""
    panels_data = all_data.get("_panels_data", {})
    lovelace_panels = {
        k: v for k, v in panels_data.items() if v.get("component_name") == "lovelace"
    }
    lovelace = all_data["lovelace"]

    console.print("\n[bold dim]Lovelace debug[/bold dim]")
    console.print(
        f"  get_panels: {len(panels_data)} total panels, "
        f"{len(lovelace_panels)} Lovelace dashboard(s)"
        + (f": {list(lovelace_panels)}" if lovelace_panels else "")
    )
    for url_path, config in lovelace:
        label = url_path or "Default"
        if config is None:
            console.print(f"  [red]✗[/red]  {label}  (fetch failed)")
        else:
            views = config.get("views", [])
            card_count = sum(len(_cards_from_view(v)) for v in views)
            console.print(
                f"  [green]✓[/green]  {label}  "
                f"({len(views)} view(s), {card_count} top-level card(s))"
            )
            for v in views:
                v_title = v.get("title") or v.get("path") or "?"
                cards = _cards_from_view(v)
                console.print(f"       view '{v_title}': {len(cards)} card(s)")
                for c in cards:
                    ids = _collect_lovelace_entities(c)
                    console.print(
                        f"         {c.get('type', '?')!r}  "
                        f"entities found: {sorted(ids) or '(none)'}"
                    )
    console.print()


async def run_inspect(ha_url: str, token: str, verify_ssl: bool, debug: bool = False) -> None:
    ha_client = HAClient(ha_url, token, verify_ssl)

    console.print("Fetching data from Home Assistant...", end=" ")
    all_data = await fetch_all_data(ha_client)
    console.print("[green]✓[/green]")

    if debug:
        _debug_lovelace(all_data)

    area_map = {a["area_id"]: a["name"] for a in all_data["area_registry"]}
    ieee = await _pick_device(all_data["zha_devices"], all_data["device_registry"], area_map)
    if ieee is None:
        return

    deps = build_deps(ieee, all_data)
    if deps is None:
        console.print("[red]Device not found.[/red]")
        return

    show_report(deps)


def inspect_command(ha_url: str, token: str, verify_ssl: bool, debug: bool = False) -> None:
    asyncio.run(run_inspect(ha_url, token, verify_ssl, debug))
