import asyncio
from dataclasses import dataclass, field
from typing import Any

import questionary
from rich.console import Console
from rich.panel import Panel

from zigporter.entity_refs import collect_config_entity_ids
from zigporter.ha_client import HAClient, is_yaml_mode
from zigporter.lovelace import cards_from_view as _cards_from_view
from zigporter.lovelace import discover_dashboards
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
    ieee: str | None  # ZHA or Z2M IEEE address; None for non-Zigbee devices
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

    dashboard_url_paths, dashboard_titles = discover_dashboards(panels_data)

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
    dashboard_url_paths, dashboard_titles = discover_dashboards(panels_data)

    lovelace_configs = await asyncio.gather(
        *[ha_client.get_lovelace_config(p) for p in dashboard_url_paths]
    )

    dashboard_refs: list[DashboardRef] = []
    for url_path, config in zip(dashboard_url_paths, lovelace_configs, strict=True):
        if config is None or is_yaml_mode(config):
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


def _extract_ieee(dr_entry: dict[str, Any]) -> str | None:
    """Extract an IEEE address from a device registry entry's identifiers, if present."""
    from zigporter.utils import parse_z2m_ieee_identifier

    for platform, identifier in dr_entry.get("identifiers", []):
        if platform == "zha":
            return identifier
        if platform == "mqtt":
            ieee = parse_z2m_ieee_identifier(identifier)
            if ieee:
                return f"0x{ieee}"
    return None


def build_deps(
    device_id: str,
    all_data: dict[str, Any],
) -> DeviceDeps | None:
    """Assemble a DeviceDeps for the given HA device ID from pre-fetched data."""
    dr_entry = next((d for d in all_data["device_registry"] if d["id"] == device_id), None)
    if dr_entry is None:
        return None

    area_map = {a["area_id"]: a["name"] for a in all_data["area_registry"]}
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
        if config is None or is_yaml_mode(config):
            continue
        title = all_data["dashboard_titles"].get(url_path, url_path or "Default")
        dashboard_refs.extend(_scan_dashboard(config, title, target))

    # Prefer ZHA user_given_name > device registry name_by_user > device registry name
    zha_device = next(
        (d for d in all_data["zha_devices"] if d.get("device_reg_id") == device_id),
        None,
    )
    if zha_device:
        name = zha_device.get("user_given_name") or zha_device.get("name") or device_id
        manufacturer = zha_device.get("manufacturer") or dr_entry.get("manufacturer")
        model = zha_device.get("model") or dr_entry.get("model")
    else:
        name = dr_entry.get("name_by_user") or dr_entry.get("name") or device_id
        manufacturer = dr_entry.get("manufacturer")
        model = dr_entry.get("model")

    ieee = _extract_ieee(dr_entry) or (zha_device.get("ieee") if zha_device else None)

    return DeviceDeps(
        ieee=ieee,
        name=name,
        manufacturer=manufacturer,
        model=model,
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
    meta_parts = [f"IEEE: [dim]{deps.ieee}[/dim]"] if deps.ieee else []
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
    all_data: dict[str, Any],
    backend: str,
) -> str | None:
    """Interactive device picker grouped by area.  Returns an HA device ID."""
    device_registry = all_data["device_registry"]
    zha_devices = all_data["zha_devices"]
    area_map = {a["area_id"]: a["name"] for a in all_data["area_registry"]}

    candidates = _filter_by_backend(device_registry, zha_devices, backend)
    zha_by_reg_id = {d.get("device_reg_id"): d for d in zha_devices}

    if not candidates:
        label = {"zha": "ZHA", "z2m": "Zigbee2MQTT", "all": "HA"}.get(backend, backend)
        console.print(f"[yellow]No {label} devices found.[/yellow]")
        return None

    def _name(dr: dict[str, Any]) -> str:
        zha = zha_by_reg_id.get(dr["id"])
        if zha:
            return zha.get("user_given_name") or zha.get("name") or dr.get("name") or dr["id"]
        return dr.get("name_by_user") or dr.get("name") or dr["id"]

    def _model(dr: dict[str, Any]) -> str:
        zha = zha_by_reg_id.get(dr["id"])
        if zha:
            return zha.get("model") or dr.get("model") or ""
        return dr.get("model") or ""

    enriched = [(dr, area_map.get(dr.get("area_id", ""), "")) for dr in candidates]
    enriched.sort(key=lambda x: (x[1] or "\xff", _name(x[0]).lower()))

    choices: list = []
    current_area: object = object()
    for dr, area_name in enriched:
        if area_name != current_area:
            current_area = area_name
            heading = f" {area_name or 'No area'} "
            choices.append(
                questionary.Separator(f"{'─' * 4}{heading}{'─' * max(0, 48 - len(heading))}")
            )
        label = f"  {_name(dr):<40} {_model(dr)}"
        choices.append(questionary.Choice(title=label, value=dr["id"]))

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
        elif is_yaml_mode(config):
            console.print(f"  [yellow]~[/yellow]  {label}  (YAML mode — skipped)")
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


def _filter_by_backend(
    device_registry: list[dict[str, Any]],
    zha_devices: list[dict[str, Any]],
    backend: str,
) -> list[dict[str, Any]]:
    """Return device registry entries visible to the given backend."""
    if backend == "all":
        return device_registry
    if backend == "zha":
        zha_reg_ids = {d.get("device_reg_id") for d in zha_devices}
        return [d for d in device_registry if d["id"] in zha_reg_ids]
    if backend == "z2m":
        from zigporter.utils import parse_z2m_ieee_identifier

        return [
            d
            for d in device_registry
            if any(
                platform == "mqtt" and parse_z2m_ieee_identifier(identifier) is not None
                for platform, identifier in d.get("identifiers", [])
            )
        ]
    return device_registry


def _resolve_device_arg(device_str: str, all_data: dict[str, Any], backend: str) -> list[str]:
    """Resolve a CLI device arg to a list of matching HA device IDs.

    Accepts an entity ID (``domain.name``), an IEEE address (hex with optional
    ``0x`` prefix or colon separators), or a partial device name.  Returns an
    empty list when nothing matches and multiple entries when the name is
    ambiguous.

    Returns ``["__not_backend__"]`` when the device exists but belongs to a
    different integration than the requested backend.
    """
    entity_registry = all_data["entity_registry"]
    device_registry = all_data["device_registry"]
    zha_devices = all_data["zha_devices"]
    backend_devices = _filter_by_backend(device_registry, zha_devices, backend)
    backend_ids = {d["id"] for d in backend_devices}
    all_ids = {d["id"] for d in device_registry}

    # 0. Direct HA device ID — 32 lowercase hex chars (no separators)
    _s_raw = device_str.lower().replace("-", "")
    if len(_s_raw) == 32 and all(c in "0123456789abcdef" for c in _s_raw):
        if _s_raw in backend_ids:
            return [_s_raw]
        if _s_raw in all_ids:
            return ["__not_backend__"]
        return []

    # 1. Entity ID — identified by a domain separator not starting with 0x
    if "." in device_str and not device_str.startswith("0x"):
        entity = next((e for e in entity_registry if e["entity_id"] == device_str), None)
        if entity:
            device_id = entity.get("device_id")
            if device_id in backend_ids:
                return [device_id]
            if any(d["id"] == device_id for d in device_registry):
                return ["__not_backend__"]
        return []

    # 2. IEEE address — strip 0x prefix / colons / dashes, check for 16 hex chars
    _s = device_str.lower().replace(":", "").replace("-", "")
    stripped = _s.removeprefix("0x")
    if len(stripped) == 16 and all(c in "0123456789abcdef" for c in stripped):
        norm = normalize_ieee(device_str)
        # Check ZHA devices first
        zha_dev = next((d for d in zha_devices if normalize_ieee(d.get("ieee", "")) == norm), None)
        if zha_dev:
            dev_id = zha_dev.get("device_reg_id")
            return [dev_id] if dev_id in backend_ids else ["__not_backend__"]
        # Check Z2M / other MQTT identifiers
        for dr in device_registry:
            ieee = _extract_ieee(dr)
            if ieee and normalize_ieee(ieee) == norm:
                return [dr["id"]] if dr["id"] in backend_ids else ["__not_backend__"]
        return []

    # 3. Partial name match (case-insensitive substring)
    lower = device_str.lower()

    # Build name for each device: prefer ZHA user_given_name
    zha_by_reg_id = {d.get("device_reg_id"): d for d in zha_devices}

    def _device_name(dr: dict[str, Any]) -> str:
        zha = zha_by_reg_id.get(dr["id"])
        if zha:
            return zha.get("user_given_name") or zha.get("name") or dr.get("name") or ""
        # For Z2M and other integrations: HA stores the friendly name as name_by_user
        # (if the user renamed it in HA) or name (set by the integration on pairing).
        # Also check the Z2M friendly_name via the entity registry as a last resort.
        return dr.get("name_by_user") or dr.get("name") or ""

    backend_matches = [d["id"] for d in backend_devices if lower in _device_name(d).lower()]
    if backend_matches:
        return backend_matches

    # Check if the name matches a device in a different backend
    other_match = next(
        (
            d
            for d in device_registry
            if d["id"] not in backend_ids and lower in _device_name(d).lower()
        ),
        None,
    )
    if other_match:
        return ["__not_backend__"]
    return []


async def run_inspect(
    ha_url: str,
    token: str,
    verify_ssl: bool,
    debug: bool = False,
    device: str | None = None,
    backend: str = "zha",
    json_output: bool = False,
) -> None:
    _VALID_BACKENDS = {"zha", "z2m", "all"}
    if backend not in _VALID_BACKENDS:
        console.print(
            f"[red]Error:[/red] Unknown backend {backend!r}. "
            f"Valid values: {', '.join(sorted(_VALID_BACKENDS))}"
        )
        return

    ha_client = HAClient(ha_url, token, verify_ssl)

    if not json_output:
        console.print("Fetching data from Home Assistant...", end=" ")
    all_data = await fetch_all_data(ha_client)
    if not json_output:
        console.print("[green]✓[/green]")

    if debug and not json_output:
        _debug_lovelace(all_data)

    backend_label = {"zha": "ZHA", "z2m": "Zigbee2MQTT", "all": "HA"}.get(backend, backend)

    if device is not None:
        matches = _resolve_device_arg(device, all_data, backend)
        if not matches:
            console.print(f"[red]{backend_label} device not found:[/red] {device!r}")
            return
        if matches == ["__not_backend__"]:
            if backend == "all":
                console.print(f"[red]Device not found:[/red] {device!r}")
            else:
                console.print(
                    f"[red]Not a {backend_label} device:[/red] {device!r}  "
                    f"[dim](use --backend all to search all integrations)[/dim]"
                )
            return
        if len(matches) > 1:
            console.print(
                f"[red]Ambiguous — {len(matches)} {backend_label} devices match {device!r}:[/red]"
            )
            zha_by_reg_id = {d.get("device_reg_id"): d for d in all_data["zha_devices"]}
            dr_by_id = {d["id"]: d for d in all_data["device_registry"]}
            for dev_id in matches:
                dr = dr_by_id.get(dev_id, {})
                zha = zha_by_reg_id.get(dev_id)
                name = (
                    (zha.get("user_given_name") or zha.get("name"))
                    if zha
                    else (dr.get("name_by_user") or dr.get("name") or dev_id)
                )
                console.print(f"  {name}  ({dev_id})")
            return
        device_id = matches[0]
    else:
        device_id = await _pick_device(all_data, backend)
        if device_id is None:
            return

    deps = build_deps(device_id, all_data)
    if deps is None:
        console.print("[red]Device not found.[/red]")
        return

    if json_output:
        import json
        from dataclasses import asdict

        print(json.dumps(asdict(deps), indent=2))
    else:
        show_report(deps)


def inspect_command(
    ha_url: str,
    token: str,
    verify_ssl: bool,
    debug: bool = False,
    device: str | None = None,
    backend: str = "zha",
    json_output: bool = False,
) -> None:
    asyncio.run(run_inspect(ha_url, token, verify_ssl, debug, device, backend, json_output))
