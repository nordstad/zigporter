"""Zigbee mesh topology visualiser — tree and table views with LQI signal strength."""

import asyncio
import time
from pathlib import Path
from typing import Any

import questionary
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from zigporter.ha_client import HAClient
from zigporter.ui import QUESTIONARY_STYLE
from zigporter.utils import normalize_ieee
from zigporter.z2m_client import Z2MClient

console = Console()


# ---------------------------------------------------------------------------
# ZHA data normalization
# ---------------------------------------------------------------------------


def _normalize_zha_topology(
    topology: dict[str, Any],
    zha_devices: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Convert ZHA network topology to the (nodes, links) format used by the renderers.

    ZHA device type strings ("Coordinator", "Router", "EndDevice") match Z2M's
    convention, so no translation is needed.  IEEE addresses are in colon format
    in ZHA and are normalized to lowercase hex strings for consistency.

    Link convention (matching Z2M): source=neighbor (device being measured),
    target=scanning device (device doing the measuring),
    lqi=measured by scanning device.
    """
    # Build fallback name/type lookup from zha/devices
    device_info: dict[str, dict[str, Any]] = {}
    for dev in zha_devices:
        ieee = normalize_ieee(dev.get("ieee", ""))
        if ieee:
            device_info[ieee] = dev

    nodes: dict[str, dict[str, Any]] = {}
    links: list[dict[str, Any]] = []

    for raw_ieee, dev_data in topology.items():
        if not raw_ieee or not raw_ieee.strip():
            continue
        ieee = normalize_ieee(raw_ieee)

        name = (
            dev_data.get("user_given_name")
            or dev_data.get("name")
            or device_info.get(ieee, {}).get("user_given_name")
            or device_info.get(ieee, {}).get("name")
            or raw_ieee
        )

        device_type = (
            dev_data.get("device_type")
            or device_info.get(ieee, {}).get("device_type")
            or "EndDevice"
        )

        nodes[ieee] = {
            "ieeeAddr": ieee,
            "friendlyName": name,
            "type": device_type,
        }

        for neighbor in dev_data.get("neighbors", []):
            n_ieee = normalize_ieee(neighbor.get("ieee", ""))
            lqi = neighbor.get("lqi", 0)
            if n_ieee:
                # source=neighbor, target=scanner — matches Z2M link convention
                links.append(
                    {
                        "source": {"ieeeAddr": n_ieee},
                        "target": {"ieeeAddr": ieee},
                        "lqi": lqi,
                    }
                )

    return nodes, links


def _build_flat_zha_topology(
    zha_devices: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Build a single-hop topology from ZHA device list when no scan data is available.

    All devices appear at depth 1 under the coordinator.  LQI is taken from
    each device's ``lqi`` field (the most recently observed link quality
    reported by ZHA — typically the last-hop LQI, not necessarily a direct
    coordinator measurement).  No actual routing paths are shown.
    """
    coordinator_ieee: str | None = None
    for dev in zha_devices:
        if dev.get("device_type") == "Coordinator":
            ieee = normalize_ieee(dev.get("ieee", ""))
            if ieee:
                coordinator_ieee = ieee
                break

    nodes: dict[str, dict[str, Any]] = {}
    links: list[dict[str, Any]] = []

    for dev in zha_devices:
        raw_ieee = dev.get("ieee", "")
        if not raw_ieee:
            continue
        ieee = normalize_ieee(raw_ieee)
        name = dev.get("user_given_name") or dev.get("name") or raw_ieee
        device_type = dev.get("device_type") or "EndDevice"
        nodes[ieee] = {"ieeeAddr": ieee, "friendlyName": name, "type": device_type}

        if coordinator_ieee and ieee != coordinator_ieee:
            lqi = dev.get("lqi") or 0
            links.append(
                {
                    "source": {"ieeeAddr": ieee},
                    "target": {"ieeeAddr": coordinator_ieee},
                    "lqi": lqi,
                }
            )

    return nodes, links


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------


async def _resolve_backend(
    backend: str,
    ha_url: str,
    token: str,
    verify_ssl: bool,
    z2m_url: str,
) -> str:
    """Resolve the backend to use: returns 'z2m', 'zha', or 'none'.

    For 'auto': detects what is available and prompts when both are present.
    For explicit 'z2m'/'zha': validates that the chosen backend is usable.
    """
    if backend == "z2m":
        if not z2m_url.strip():
            console.print("[red]Error:[/red] --backend z2m requires Z2M_URL to be configured.")
            console.print("  Run [bold]zigporter setup[/bold] to configure Zigbee2MQTT.")
            return "none"
        return "z2m"

    if backend == "zha":
        return "zha"

    # auto — detect what's available
    z2m_available = bool(z2m_url.strip())
    zha_available = False
    try:
        ha_client = HAClient(ha_url, token, verify_ssl)
        await ha_client.get_zha_devices()
        zha_available = True
    except Exception:  # noqa: BLE001
        pass

    if z2m_available and zha_available:
        choice = await questionary.select(
            "Both Zigbee2MQTT and ZHA are available. Which backend?",
            choices=[
                questionary.Choice("Zigbee2MQTT (Z2M)", value="z2m"),
                questionary.Choice("ZHA (Zigbee Home Automation)", value="zha"),
            ],
            style=QUESTIONARY_STYLE,
        ).ask_async()
        return choice or "z2m"

    if z2m_available:
        return "z2m"
    if zha_available:
        return "zha"

    console.print("[red]Error:[/red] Neither Zigbee2MQTT nor ZHA is available.")
    console.print(
        "[dim]Configure Z2M_URL for Zigbee2MQTT, or ensure ZHA is installed.\n"
        "Run [bold]zigporter check[/bold] to diagnose connectivity.[/dim]"
    )
    return "none"


# ---------------------------------------------------------------------------
# Graph processing
# ---------------------------------------------------------------------------


def _build_routing_tree(
    nodes: dict[str, dict[str, Any]],
    links: list[dict[str, Any]],
) -> tuple[dict[str, str | None], dict[str, int], dict[str, int]]:
    """Build a routing tree via iterative BFS.

    For each non-coordinator node, the parent is the already-placed tree node
    with the best *bidirectional* LQI — min(lqi_out, lqi_in).  Using only the
    device's outgoing LQI is misleading because Zigbee links are asymmetric:
    SLZB-06P7 might report LQI 115 to the coordinator while the coordinator
    only reports LQI 29 back.  The weaker direction is the real bottleneck.

    Returns:
        parent_map:  ieee → parent_ieee  (None for coordinator)
        lqi_map:     ieee → min(lqi_out, lqi_in) to parent
        depth_map:   ieee → hops from coordinator
    """
    # Build per-directed-pair LQI: (source, target) → lqi
    pair_lqi: dict[tuple[str, str], int] = {}
    outgoing: dict[str, list[tuple[str, int]]] = {}
    for link in links:
        src = link["source"]["ieeeAddr"].lower()
        tgt = link["target"]["ieeeAddr"].lower()
        lqi = link.get("lqi", 0)
        pair_lqi[(src, tgt)] = lqi
        outgoing.setdefault(src, []).append((tgt, lqi))

    # Locate coordinator
    coordinator_ieee: str | None = None
    for ieee, node in nodes.items():
        if node.get("type") == "Coordinator":
            coordinator_ieee = ieee
            break

    if coordinator_ieee is None:
        return {}, {}, {}

    parent_map: dict[str, str | None] = {coordinator_ieee: None}
    lqi_map: dict[str, int] = {}
    depth_map: dict[str, int] = {coordinator_ieee: 0}
    visited: set[str] = {coordinator_ieee}

    # Iterative fixed-point: keep adding nodes whose best reachable neighbor is in tree
    changed = True
    while changed:
        changed = False
        for ieee in nodes:
            if ieee in visited:
                continue
            best_parent: str | None = None
            best_lqi = -1
            for tgt, lqi_out in outgoing.get(ieee, []):
                if tgt not in visited:
                    continue
                # Use the weaker of the two directions — Zigbee links are asymmetric
                # and the bottleneck is whichever side has the lower receive quality.
                lqi_in = pair_lqi.get((tgt, ieee), lqi_out)
                effective_lqi = min(lqi_out, lqi_in)
                if effective_lqi > best_lqi:
                    best_lqi = effective_lqi
                    best_parent = tgt
            if best_parent is not None:
                parent_map[ieee] = best_parent
                lqi_map[ieee] = best_lqi
                depth_map[ieee] = depth_map[best_parent] + 1
                visited.add(ieee)
                changed = True

    # Orphaned nodes (no path found) — attach to coordinator with lqi=0
    for ieee in nodes:
        if ieee not in visited:
            parent_map[ieee] = coordinator_ieee
            lqi_map[ieee] = 0
            depth_map[ieee] = 1

    return parent_map, lqi_map, depth_map


def _lqi_markup(lqi: int, warn_lqi: int, critical_lqi: int) -> str:
    if lqi < critical_lqi:
        return f"[red]LQI: {lqi:3d}[/red]"
    if lqi < warn_lqi:
        return f"[yellow]LQI: {lqi:3d}[/yellow]"
    return f"[green]LQI: {lqi:3d}[/green]"


def _status_markup(lqi: int, warn_lqi: int, critical_lqi: int) -> str:
    if lqi < critical_lqi:
        return "[red]CRITICAL[/red]"
    if lqi < warn_lqi:
        return "[yellow]WEAK[/yellow]"
    return ""


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _coord_annotation(
    ieee: str, depth: int, coord_lqi_map: dict[str, int], warn_lqi: int, critical_lqi: int
) -> str:
    """Return a Rich-markup annotation when a routed device has a weak direct coordinator link.

    Only emitted for depth > 1 nodes (devices not directly connected to the coordinator)
    whose direct-coordinator LQI is below warn_lqi.  This surfaces the "fallback path"
    quality without replacing the routing-path LQI shown on the tree edge.
    """
    if depth <= 1 or ieee not in coord_lqi_map:
        return ""
    clqi = coord_lqi_map[ieee]
    if clqi >= warn_lqi:
        return ""
    if clqi == 0:
        return "  [red][no direct path to coordinator][/red]"
    color = "red" if clqi < critical_lqi else "yellow"
    return f"  [{color}](direct coord: {clqi})[/{color}]"


def _render_tree(
    ieee: str,
    nodes: dict[str, dict[str, Any]],
    children: dict[str, list[str]],
    lqi_map: dict[str, int],
    depth_map: dict[str, int],
    coord_lqi_map: dict[str, int],
    warn_lqi: int,
    critical_lqi: int,
    out: Console,
    prefix: str = "",
    is_last: bool = True,
) -> None:
    node = nodes[ieee]
    name = node.get("friendlyName", ieee)
    node_type = node.get("type", "Unknown")
    is_coordinator = node_type == "Coordinator"
    is_router = node_type in ("Coordinator", "Router")

    node_children = children.get(ieee, [])

    if is_coordinator:
        out.print("[bold]Coordinator[/bold]")
    else:
        lqi = lqi_map.get(ieee, 0)
        depth = depth_map.get(ieee, 0)
        role = "router" if is_router else "end"
        connector = "└──" if is_last else "├──"
        lqi_str = _lqi_markup(lqi, warn_lqi, critical_lqi)
        status = _status_markup(lqi, warn_lqi, critical_lqi)
        children_info = f"  ({len(node_children)} children)" if node_children else ""
        status_str = f"  {status}" if status else ""
        coord_str = _coord_annotation(ieee, depth, coord_lqi_map, warn_lqi, critical_lqi)
        out.print(
            f"{prefix}{connector} {name}  [{role}]  {lqi_str}  hops: {depth}"
            f"{children_info}{status_str}{coord_str}"
        )

    child_list = sorted(node_children, key=lambda x: -lqi_map.get(x, 0))
    new_prefix = prefix + ("    " if is_last else "│    ")
    for i, child_ieee in enumerate(child_list):
        _render_tree(
            child_ieee,
            nodes,
            children,
            lqi_map,
            depth_map,
            coord_lqi_map,
            warn_lqi,
            critical_lqi,
            out,
            prefix=new_prefix,
            is_last=(i == len(child_list) - 1),
        )


def _render_table(
    nodes: dict[str, dict[str, Any]],
    parent_map: dict[str, str | None],
    lqi_map: dict[str, int],
    depth_map: dict[str, int],
    coord_lqi_map: dict[str, int],
    warn_lqi: int,
    critical_lqi: int,
    out: Console,
) -> None:
    from rich.table import Table  # noqa: PLC0415

    table = Table(show_header=True, header_style="bold")
    table.add_column("Device", no_wrap=True)
    table.add_column("Role")
    table.add_column("Parent", no_wrap=True)
    table.add_column("LQI", justify="right")
    table.add_column("Hops", justify="right")
    table.add_column("Status")

    rows: list[tuple[int, str, str, str, str, str, str]] = []
    for ieee, node in nodes.items():
        if node.get("type") == "Coordinator":
            continue
        lqi = lqi_map.get(ieee, 0)
        depth = depth_map.get(ieee, 0)
        name = node.get("friendlyName", ieee)
        role = "router" if node.get("type") == "Router" else "end"
        parent_ieee = parent_map.get(ieee)
        parent_name = ""
        if parent_ieee and parent_ieee in nodes:
            parent_node = nodes[parent_ieee]
            parent_name = parent_node.get("friendlyName", parent_ieee)
        status = _status_markup(lqi, warn_lqi, critical_lqi)
        coord_str = _coord_annotation(ieee, depth, coord_lqi_map, warn_lqi, critical_lqi)
        rows.append((lqi, name, role, parent_name, str(lqi), str(depth), status + coord_str))

    rows.sort(key=lambda r: r[0])
    for _, name, role, parent, lqi_str, hops, status in rows:
        table.add_row(name, role, parent, lqi_str, hops, status)

    out.print(table)


# ---------------------------------------------------------------------------
# Data fetching helpers
# ---------------------------------------------------------------------------


async def _fetch_z2m_data(
    ha_url: str,
    token: str,
    z2m_url: str,
    verify_ssl: bool,
    mqtt_topic: str,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]] | None:
    """Fetch network map from Z2M. Returns (nodes, links) or None on error."""
    client = Z2MClient(ha_url, token, z2m_url, verify_ssl, mqtt_topic)

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
        t = progress.add_task("Fetching Z2M network map...", total=None)
        start = time.monotonic()
        try:
            fetch = asyncio.create_task(client.get_network_map())
            while not fetch.done():
                await asyncio.sleep(1)
                elapsed = int(time.monotonic() - start)
                progress.update(t, description=f"Fetching Z2M network map... {elapsed}s")
            response = fetch.result()
        except Exception as exc:  # noqa: BLE001
            progress.update(t, description="Failed")
            console.print(f"\n[red]Error:[/red] Could not fetch Z2M network map — {exc}")
            console.print(
                "[dim]Ensure Z2M_URL is reachable and the Z2M add-on is running.\n"
                "Run [bold]zigporter check[/bold] to diagnose connectivity.[/dim]"
            )
            return None
        progress.update(t, description="Done")

    data = response.get("data", response)
    raw_nodes: list[dict[str, Any]] = data.get("nodes", [])
    links: list[dict[str, Any]] = data.get("links", [])

    nodes: dict[str, dict[str, Any]] = {}
    for n in raw_nodes:
        ieee = n.get("ieeeAddr", "").lower()
        if ieee:
            nodes[ieee] = n

    return nodes, links


async def _fetch_zha_data(
    ha_url: str,
    token: str,
    verify_ssl: bool,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]] | None:
    """Fetch network topology from ZHA. Returns (nodes, links) or None on error.

    Topology resolution order:
    1. ``zha/network_topology`` — cached scan result (some HA versions).
    2. ``zha/topology/scan_now`` — triggers a scan; some HA versions return the
       topology dict directly in the response.
    3. After scan, re-try ``zha/network_topology`` in case it was populated.
    4. Flat fallback: all devices at depth 1 using per-device LQI from
       ``zha/devices``.  Routing paths are not shown but link quality is.
    """
    ha_client = HAClient(ha_url, token, verify_ssl)

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
        t = progress.add_task("Fetching ZHA network topology...", total=None)
        start = time.monotonic()
        try:
            zha_devices = await ha_client.get_zha_devices()
            if not zha_devices:
                progress.update(t, description="Failed")
                console.print(
                    "\n[red]Error:[/red] No ZHA devices found.\n"
                    "[dim]Ensure ZHA is installed and has paired devices.[/dim]"
                )
                return None

            # 1. Try cached topology
            topology = await ha_client.get_zha_network_topology()

            if not topology:
                # 2. Trigger scan — may return topology directly or just ack {}
                progress.update(
                    t, description="Scanning ZHA network topology (may take up to 90s)..."
                )
                scan = asyncio.create_task(ha_client.run_zha_topology_scan())
                while not scan.done():
                    await asyncio.sleep(1)
                    elapsed = int(time.monotonic() - start)
                    progress.update(t, description=f"Scanning ZHA network topology... {elapsed}s")
                scan_result = scan.result()

                # scan_now returns topology directly in some HA versions
                if scan_result:
                    topology = scan_result
                else:
                    # 3. Re-fetch cached topology after scan
                    topology = await ha_client.get_zha_network_topology()

        except Exception as exc:  # noqa: BLE001
            progress.update(t, description="Failed")
            console.print(f"\n[red]Error:[/red] Could not fetch ZHA data — {exc}")
            console.print(
                "[dim]Ensure ZHA is installed and connected.\n"
                "Run [bold]zigporter check[/bold] to diagnose connectivity.[/dim]"
            )
            return None
        progress.update(t, description="Done")

    if topology:
        nodes, links = _normalize_zha_topology(topology, zha_devices)
    else:
        # 4. Flat fallback — device LQI only, no routing paths
        nodes, links = _build_flat_zha_topology(zha_devices)
        console.print(
            "[dim]ZHA topology scan not available in this HA version — "
            "showing flat view with per-device LQI.[/dim]"
        )

    return nodes, links


# ---------------------------------------------------------------------------
# Main async entry point
# ---------------------------------------------------------------------------


async def run_network_map(
    ha_url: str,
    token: str,
    z2m_url: str,
    verify_ssl: bool,
    mqtt_topic: str = "zigbee2mqtt",
    output_format: str = "tree",
    warn_lqi: int = 80,
    critical_lqi: int = 30,
    output_svg: Path | None = None,
    backend: str = "auto",
) -> None:
    resolved = await _resolve_backend(backend, ha_url, token, verify_ssl, z2m_url)
    if resolved == "none":
        return

    if resolved == "zha":
        result = await _fetch_zha_data(ha_url, token, verify_ssl)
    else:
        result = await _fetch_z2m_data(ha_url, token, z2m_url, verify_ssl, mqtt_topic)

    if result is None:
        return

    nodes, links = result

    parent_map, lqi_map, depth_map = _build_routing_tree(nodes, links)

    # Build direct-to-coordinator LQI map.
    # Z2M link convention: source=neighbor, target=scanning device, lqi=measured by scanner.
    # Links where target=coordinator give the LQI the coordinator measured from each device.
    # Used to annotate routed devices whose direct coordinator link is weaker than their
    # routing path — these would have poor fallback connectivity if their parent router fails.
    coordinator_ieee = next(
        (ieee for ieee, n in nodes.items() if n.get("type") == "Coordinator"), None
    )
    coord_lqi_map: dict[str, int] = {}
    if coordinator_ieee:
        for link in links:
            src = link["source"]["ieeeAddr"].lower()
            tgt = link["target"]["ieeeAddr"].lower()
            if tgt == coordinator_ieee and src != coordinator_ieee:
                coord_lqi_map[src] = link.get("lqi", 0)

    children: dict[str, list[str]] = {ieee: [] for ieee in nodes}
    for ieee, parent in parent_map.items():
        if parent is not None:
            children.setdefault(parent, []).append(ieee)

    non_coord = [n for n in nodes.values() if n.get("type") != "Coordinator"]
    router_count = sum(1 for n in non_coord if n.get("type") == "Router")
    end_count = len(non_coord) - router_count

    weak_count = sum(
        1
        for ieee, lqi in lqi_map.items()
        if critical_lqi <= lqi < warn_lqi and nodes[ieee].get("type") != "Coordinator"
    )
    critical_count = sum(
        1
        for ieee, lqi in lqi_map.items()
        if lqi < critical_lqi and nodes[ieee].get("type") != "Coordinator"
    )

    backend_label = "ZHA" if resolved == "zha" else "Z2M"
    summary = (
        f"Zigbee Network Map [{backend_label}]  "
        f"[{len(non_coord)} devices: {router_count} routers, {end_count} end-devices]"
    )
    if weak_count:
        summary += f"  [yellow]{weak_count} WEAK[/yellow]"
    if critical_count:
        summary += f"  [red]{critical_count} CRITICAL[/red]"

    console.print(f"\n{summary}\n")

    if output_format == "table":
        _render_table(
            nodes, parent_map, lqi_map, depth_map, coord_lqi_map, warn_lqi, critical_lqi, console
        )
    else:
        if coordinator_ieee:
            _render_tree(
                coordinator_ieee,
                nodes,
                children,
                lqi_map,
                depth_map,
                coord_lqi_map,
                warn_lqi,
                critical_lqi,
                console,
            )

    if output_svg is not None:
        from zigporter.commands.network_map_svg import render_svg  # noqa: PLC0415

        with Progress(
            SpinnerColumn(), TextColumn("{task.description}"), console=console
        ) as svg_progress:
            svg_task = svg_progress.add_task(f"Rendering SVG ({len(nodes)} nodes)…", total=None)
            render_svg(
                nodes=nodes,
                parent_map=parent_map,
                lqi_map=lqi_map,
                depth_map=depth_map,
                children=children,
                output_path=output_svg,
                warn_lqi=warn_lqi,
                critical_lqi=critical_lqi,
            )
            svg_progress.update(svg_task, description=f"SVG saved → {output_svg}")
        console.print(f"[dim]Saved to [bold]{output_svg}[/bold][/dim]")


def network_map_command(
    ha_url: str,
    token: str,
    z2m_url: str,
    verify_ssl: bool,
    mqtt_topic: str = "zigbee2mqtt",
    output_format: str = "tree",
    warn_lqi: int = 80,
    critical_lqi: int = 30,
    output_svg: Path | None = None,
    backend: str = "auto",
) -> None:
    asyncio.run(
        run_network_map(
            ha_url=ha_url,
            token=token,
            z2m_url=z2m_url,
            verify_ssl=verify_ssl,
            mqtt_topic=mqtt_topic,
            output_format=output_format,
            warn_lqi=warn_lqi,
            critical_lqi=critical_lqi,
            output_svg=output_svg,
            backend=backend,
        )
    )
