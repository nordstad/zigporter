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

# Per-hop LQI penalty for tree-building scoring.  Without this, small LQI
# fluctuations between scans cascade into wildly different tree depths because
# a 4-hop chain where every hop is LQI 160 beats a direct link at LQI 155.
# Subtracting HOP_LQI_PENALTY per hop from the candidate score makes the
# algorithm prefer shorter paths unless the longer path has a substantial
# LQI advantage.  Value of 10 means a depth-1 router must beat the direct
# coordinator link by at least 10 LQI to be chosen.
HOP_LQI_PENALTY = 10


# ---------------------------------------------------------------------------
# ZHA data normalization
# ---------------------------------------------------------------------------


def _zha_lqi(raw: str | int | None) -> int:
    """Convert a ZHA LQI value to int.

    HA serialises neighbor LQI as a string (``str(neighbor.lqi)``); the
    top-level device ``lqi`` field is an int or None.  This helper handles
    both forms and returns 0 on any parse failure.
    """
    if raw is None:
        return 0
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 0


def _build_zha_topology_from_devices(
    zha_devices: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Build routing topology from the neighbor tables embedded in ``zha/devices``.

    Each device in the ZHA devices list includes a ``neighbors`` list populated
    from the ZDO neighbor-table scan that ZHA runs periodically.  Each neighbor
    entry contains:

    * ``"ieee"``         — IEEE address of the neighbor (colon format)
    * ``"lqi"``          — LQI **as a string** (HA serialises it via ``str()``)
    * ``"relationship"`` — ZDO relationship name, e.g. ``"Child"``, ``"Neighbor"``,
                           ``"Parent"`` — **not** present when ZHA has no scan data

    A ``"Child"`` relationship means the *scanning* device is the parent of the
    neighbor.  Passing this through to ``_build_routing_tree`` as the link
    ``"relationship"`` field lets the tree builder prefer authoritative parent
    links over same-LQI coordinator overhear links.

    Falls back silently to an empty link list (flat view) when no device has
    neighbor data (e.g. ZHA has never completed a topology scan).
    """
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

        for neighbor in dev.get("neighbors", []):
            n_raw_ieee = neighbor.get("ieee", "")
            if not n_raw_ieee:
                continue
            n_ieee = normalize_ieee(n_raw_ieee)
            lqi = _zha_lqi(neighbor.get("lqi"))
            relationship = neighbor.get("relationship", "")
            # source=neighbor, target=scanner — matches Z2M link convention.
            # relationship="Child" means the scanner (dev) is the parent of n_ieee.
            links.append(
                {
                    "source": {"ieeeAddr": n_ieee},
                    "target": {"ieeeAddr": ieee},
                    "lqi": lqi,
                    "relationship": relationship,
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
        try:
            ha_client = HAClient(ha_url, token, verify_ssl)
            await ha_client.get_zha_devices()
        except Exception:  # noqa: BLE001
            console.print(
                "[red]Error:[/red] --backend zha requires ZHA to be installed and reachable."
            )
            console.print("  Run [bold]zigporter check[/bold] to diagnose connectivity.")
            return "none"
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
        if choice is None:
            console.print("\n[dim]Cancelled by user.[/dim]")
            return "none"
        return choice

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


def _is_ancestor(candidate: str, of: str, parent_map: dict[str, str | None]) -> bool:
    """Return True if `candidate` is an ancestor of `of` in the current tree."""
    cur = parent_map.get(of)
    while cur is not None:
        if cur == candidate:
            return True
        cur = parent_map.get(cur)
    return False


def _build_routing_tree(
    nodes: dict[str, dict[str, Any]],
    links: list[dict[str, Any]],
) -> tuple[dict[str, str | None], dict[str, int], dict[str, int]]:
    """Build a routing tree via iterative greedy BFS.

    For each non-coordinator node, the parent is the already-placed tree node
    with the best *bidirectional* LQI — min(lqi_out, lqi_in).  Using only the
    device's outgoing LQI is misleading because Zigbee links are asymmetric:
    SLZB-06P7 might report LQI 115 to the coordinator while the coordinator
    only reports LQI 29 back.  The weaker direction is the real bottleneck.

    Score = (is_child_rel, effective_lqi - HOP_LQI_PENALTY * candidate_depth).

    The depth penalty stabilises the tree across scans: Z2M network scans
    return different LQI values each time (RF noise, sleeping devices), and
    without the penalty, small fluctuations cascade into wildly different tree
    depths.  A penalty of 10 per hop means a 4-hop chain at LQI 160 scores
    120, losing to a direct link at LQI 155 — correct because the direct path
    is simpler and more reliable.

    is_child_rel=1 when the candidate router explicitly claims this device as its
    child via the ZHA ZDO relationship field (string "Child" or integer 1 from
    bellows/zigpy serialisation).  Putting is_child first means a "Child" link
    always beats a higher-LQI "Neighbor" link from the coordinator — correct
    because the coordinator sometimes overhears end-devices that have actually
    joined a range extender.

    "Parent" relationship edges are skipped: a link whose relationship is "Parent"
    means the *current* device is the parent of the candidate (i.e. the edge points
    in the wrong direction for BFS parent selection) and would create routing cycles.

    After the greedy loop, a depth-cascade pass ensures every device's depth_map
    entry is exactly parent.depth + 1.  Without this pass, a device whose parent
    was re-assigned to a deeper level mid-loop would retain its old (shallower)
    depth while its parent_map entry pointed to a deeper node — creating the visual
    inconsistency of an edge that appears to go from an inner ring to an outer ring.

    Returns:
        parent_map:  ieee → parent_ieee  (None for coordinator)
        lqi_map:     ieee → min(lqi_out, lqi_in) to parent
        depth_map:   ieee → hops from coordinator
    """
    # Build per-directed-pair LQI: (source, target) → lqi
    # outgoing: source → [(target, lqi, relationship)]
    pair_lqi: dict[tuple[str, str], int] = {}
    outgoing: dict[str, list[tuple[str, int, str | int]]] = {}
    for link in links:
        src = link["source"]["ieeeAddr"].lower()
        tgt = link["target"]["ieeeAddr"].lower()
        lqi = link.get("lqi", 0)
        relationship = link.get("relationship", "")
        pair_lqi[(src, tgt)] = lqi
        outgoing.setdefault(src, []).append((tgt, lqi, relationship))

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
    best_score_map: dict[str, tuple[int, int]] = {}

    changed = True
    while changed:
        changed = False
        for ieee in nodes:
            if nodes[ieee].get("type") == "Coordinator":
                continue
            best_parent: str | None = None
            best_score: tuple[int, int] = (-1, -1)
            best_real_lqi = 0
            for tgt, lqi_out, relationship in outgoing.get(ieee, []):
                if tgt not in visited:
                    continue
                # "Parent" means the current node is the parent of tgt — wrong direction.
                if relationship in ("Parent", "PreviousChild"):
                    continue
                # Use the weaker of the two directions — Zigbee links are asymmetric
                # and the bottleneck is whichever side has the lower receive quality.
                lqi_in = pair_lqi.get((tgt, ieee), lqi_out)
                effective_lqi = min(lqi_out, lqi_in)
                # Accept both string ("Child") and integer (1) from ZHA/bellows.
                is_child = 1 if relationship in ("Child", 1) else 0
                # Penalise deeper candidates so small LQI fluctuations between
                # scans don't cascade into wildly different tree depths.
                candidate_depth = depth_map[tgt]
                score = (is_child, effective_lqi - HOP_LQI_PENALTY * candidate_depth)
                if _is_ancestor(ieee, tgt, parent_map):
                    continue  # would create a cycle — skip
                if score > best_score:
                    best_score = score
                    best_parent = tgt
                    best_real_lqi = effective_lqi
            if best_parent is not None and best_score > best_score_map.get(ieee, (-1, -1)):
                best_score_map[ieee] = best_score
                parent_map[ieee] = best_parent
                lqi_map[ieee] = best_real_lqi
                depth_map[ieee] = depth_map[best_parent] + 1
                visited.add(ieee)
                changed = True

    # Orphaned nodes (no path found) — attach to coordinator with lqi=0
    for ieee in nodes:
        if ieee not in visited:
            parent_map[ieee] = coordinator_ieee
            lqi_map[ieee] = 0
            depth_map[ieee] = 1

    # Depth cascade: re-compute every device's depth as parent.depth + 1.
    # The greedy loop above can re-assign a node's parent mid-pass without
    # updating already-processed children, leaving depth_map inconsistent
    # with parent_map.  Iterate until stable so cascaded re-assignments
    # (where a parent's depth changes AFTER its children were processed) are
    # fully propagated.
    cascade_changed = True
    while cascade_changed:
        cascade_changed = False
        for ieee in nodes:
            p = parent_map.get(ieee)
            if p is not None:
                new_depth = depth_map.get(p, 0) + 1
                if depth_map.get(ieee) != new_depth:
                    depth_map[ieee] = new_depth
                    cascade_changed = True

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
    ieee: str,
    depth: int,
    coord_lqi_map: dict[str, int],
    warn_lqi: int,
    critical_lqi: int,
) -> str:
    """Return a Rich-markup annotation showing asymmetric or weak coordinator links.

    Depth 1 (direct to coordinator): always shows the uplink LQI (device → coordinator,
    measured by the coordinator).  The primary displayed LQI is min(up, down); showing
    the uplink separately lets users compare against the Z2M dashboard badge which only
    reports the uplink direction.

    Depth > 1 (routed): shows the direct-coordinator LQI only when it is below warn_lqi,
    flagging poor fallback connectivity if the routing parent fails.
    """
    if ieee not in coord_lqi_map:
        return ""
    clqi = coord_lqi_map[ieee]
    if depth == 1:
        return f"  [dim](up: {clqi})[/dim]"
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

    Calls ``zha/devices`` and builds the routing tree from the per-device neighbor
    tables (ZDO scan data) that HA already embeds in each ``zha_device_info``.
    Falls back to a flat single-hop view when no device has neighbor data yet.
    """
    ha_client = HAClient(ha_url, token, verify_ssl)

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
        t = progress.add_task("Fetching ZHA devices...", total=None)
        try:
            zha_devices = await ha_client.get_zha_devices()
            if not zha_devices:
                progress.update(t, description="Failed")
                console.print(
                    "\n[red]Error:[/red] No ZHA devices found.\n"
                    "[dim]Ensure ZHA is installed and has paired devices.[/dim]"
                )
                return None
        except Exception as exc:  # noqa: BLE001
            progress.update(t, description="Failed")
            console.print(f"\n[red]Error:[/red] Could not fetch ZHA data — {exc}")
            console.print(
                "[dim]Ensure ZHA is installed and connected.\n"
                "Run [bold]zigporter check[/bold] to diagnose connectivity.[/dim]"
            )
            return None
        progress.update(t, description="Done")

    # Use the neighbor tables embedded in each device's zha_device_info.
    # These come from ZHA's periodic ZDO topology scan and include the "relationship"
    # field that correctly resolves parent-child links even when the coordinator
    # overhears end-devices that have actually joined a range extender.
    # Fall back to a flat single-hop view only when no device has neighbour data
    # (e.g. ZHA has never run a topology scan on this installation).
    has_neighbors = any(dev.get("neighbors") for dev in zha_devices)
    if has_neighbors:
        nodes, links = _build_zha_topology_from_devices(zha_devices)
    else:
        nodes, links = _build_flat_zha_topology(zha_devices)
        console.print(
            "[dim]ZHA has no topology scan data yet — showing flat view.\n"
            "Trigger a scan from ZHA settings (Network visualisation → Scan) "
            "then re-run.[/dim]"
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
                coord_lqi_map=coord_lqi_map,
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
