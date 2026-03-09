"""SVG export for the network-map command — radial layout with LQI visual encoding."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import svgwrite

# ── Visual constants ──────────────────────────────────────────────────────────

MIN_RING_GAP = 200  # minimum px separation between consecutive ring boundaries
ANGULAR_PADDING = 50  # extra arc per device beyond collision minimum
LABEL_OFFSET = 30  # padding so node midpoint sits inside its ring boundary
LABEL_MARGIN = 340  # extra canvas padding beyond outermost ring (for labels)

HOP_COLORS = [
    "#facc15",  # yellow   (hop 1)
    "#4ade80",  # green    (hop 2)
    "#60a5fa",  # blue     (hop 3)
    "#f472b6",  # pink     (hop 4)
    "#fb923c",  # orange   (hop 5)
    "#a78bfa",  # violet   (hop 6)
]

NODE_R_COORD = 28
NODE_R_ROUTER = 20
NODE_R_END = 14

BG = "#0f172a"

COORD_FILL = "#f59e0b"
ROUTER_FILL = "#0ea5e9"
END_FILL = "#475569"

TEXT_PRIMARY = "#e2e8f0"
TEXT_DIM = "#64748b"

EDGE_GOOD = "#22c55e"
EDGE_WARN = "#f59e0b"
EDGE_CRIT = "#ef4444"
EDGE_OPACITY = 0.55

LABEL_FS = "11px"
DIM_FS = "11px"
LEGEND_FS = "11px"

COLLISION_GAP = 100  # px padding between node edges after nudge (must clear label zones)
COLLISION_ITERS = 200  # max angle-nudge iterations before giving up

MAX_LABEL_LEN = 22  # truncate long labels; full name available via SVG <title> tooltip


# ── Helpers ───────────────────────────────────────────────────────────────────


def _edge_color(lqi: int, warn: int, crit: int) -> str:
    if lqi < crit:
        return EDGE_CRIT
    if lqi < warn:
        return EDGE_WARN
    return EDGE_GOOD


def _edge_width(lqi: int) -> float:
    return round(0.8 + (lqi / 255) * 2.8, 2)


def _lerp_color(t: float, near: str, far: str) -> str:
    """Linearly interpolate between two hex colours. t=0 → near, t=1 → far."""
    r1, g1, b1 = int(near[1:3], 16), int(near[3:5], 16), int(near[5:7], 16)
    r2, g2, b2 = int(far[1:3], 16), int(far[3:5], 16), int(far[5:7], 16)
    r = round(r1 + (r2 - r1) * t)
    g = round(g1 + (g2 - g1) * t)
    b = round(b1 + (b2 - b1) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def _compute_ring_radii(
    depth_map: dict[str, int], nodes: dict[str, dict[str, Any]]
) -> dict[int, float]:
    """Compute per-hop ring boundary radii so each ring has enough circumference for its devices."""
    max_hops = max((d for d in depth_map.values()), default=1)
    count_at_depth: dict[int, int] = {}
    for ieee, depth in depth_map.items():
        if depth > 0:
            count_at_depth[depth] = count_at_depth.get(depth, 0) + 1

    ring_radii: dict[int, float] = {}
    prev_r = 0.0
    for h in range(1, max_hops + 1):
        n = count_at_depth.get(h, 1)
        # Label width dominates over node diameter — use it as the arc floor.
        # MAX_LABEL_LEN chars × ~6px/char + pill padding matches the pill_w formula below.
        label_arc = MAX_LABEL_LEN * 6 + 10
        arc_per_device = max(2 * NODE_R_ROUTER + COLLISION_GAP, label_arc) + ANGULAR_PADDING
        # Nodes are placed at the midpoint: (prev_r + ring_radii[h]) / 2.
        # Invert to find ring_radii[h] that gives the required node-placement radius.
        required_node_r = (n * arc_per_device) / (2 * math.pi)
        min_for_content = 2 * required_node_r - prev_r + LABEL_OFFSET
        ring_radii[h] = max(min_for_content, prev_r + MIN_RING_GAP)
        prev_r = ring_radii[h]
    return ring_radii


def _node_fill(node_type: str) -> str:
    if node_type == "Coordinator":
        return COORD_FILL
    if node_type == "Router":
        return ROUTER_FILL
    return END_FILL


def _node_radius(node_type: str) -> int:
    if node_type == "Coordinator":
        return NODE_R_COORD
    if node_type == "Router":
        return NODE_R_ROUTER
    return NODE_R_END


# ── Angular layout ────────────────────────────────────────────────────────────


def _subtree_weights(ieee: str, children: dict[str, list[str]]) -> dict[str, int]:
    """Angular weight = max(ceil(√leaf_count), subtree_depth).

    Pure leaf-count under-allocates linear chains (a 3-hop chain gets weight 1
    even though its nodes span 3 rings) and over-allocates wide hubs (a subtree
    with 15 leaves would consume 75% of the parent's arc, leaving 25% for all
    siblings).  Square-root compression keeps deep chains well-weighted while
    preventing any single large subtree from dominating the layout.
    """
    weights: dict[str, int] = {}

    def _calc(n: str) -> tuple[int, int]:  # (leaf_count, depth)
        kids = children.get(n, [])
        if not kids:
            weights[n] = 1
            return 1, 1
        results = [_calc(k) for k in kids]
        leaves = sum(lc for lc, _ in results)
        depth = max(d for _, d in results) + 1
        weights[n] = max(math.ceil(math.sqrt(leaves)), depth)
        return leaves, depth

    _calc(ieee)
    return weights


def _compute_path_min_lqi(
    parent_map: dict[str, str | None],
    lqi_map: dict[str, int],
) -> dict[str, int]:
    """Min LQI along the full chain from coordinator to each device.

    Iterative to avoid RecursionError on deep chains or cycles in parent_map.
    A `seen` set breaks any cycle that may exist in the data.
    """
    cache: dict[str, int] = {}

    def _min(ieee: str) -> int:
        if ieee in cache:
            return cache[ieee]
        path: list[str] = []
        seen: set[str] = set()
        cur: str | None = ieee
        while cur is not None and cur not in cache and cur not in seen:
            seen.add(cur)
            path.append(cur)
            cur = parent_map.get(cur)
        base = cache[cur] if cur in cache else 255
        for node in reversed(path):
            if node in lqi_map:
                base = min(lqi_map[node], base)
            cache[node] = base
        return cache.get(ieee, 0)

    for ieee in parent_map:
        _min(ieee)
    return cache


def _assign_angles(
    ieee: str,
    children: dict[str, list[str]],
    leaf_counts: dict[str, int],
    angles: dict[str, float],
    start: float,
    end: float,
    depth_map: dict[str, int],
    nodes: dict[str, dict[str, Any]],
    ring_radii: dict[int, float],
) -> None:
    """Recursively assign angular midpoints using leaf-count-proportional slices.

    The minimum angle floor is geometry-aware: each child gets at least enough
    arc so that its circle diameter plus ``COLLISION_GAP`` fits at its ring
    radius, preventing the initial placement from creating impossible overlaps.
    """
    angles[ieee] = (start + end) / 2
    kids = children.get(ieee, [])
    if not kids:
        return

    # Alternate large/small children for visual balance
    sorted_kids = sorted(kids, key=lambda k: -leaf_counts.get(k, 1))
    total = sum(leaf_counts.get(k, 1) for k in sorted_kids)
    span = end - start

    # Geometry-aware per-child minimum angle: enough arc for the node circle plus
    # label pill. Use the same label_arc floor as _compute_ring_radii so the
    # initial placement already respects label-width separation.
    child_depth = depth_map.get(ieee, 0) + 1
    prev_r = ring_radii.get(child_depth - 1, 0.0)
    curr_r = ring_radii.get(child_depth, prev_r + MIN_RING_GAP)
    r_at_depth = max((prev_r + curr_r) / 2, 1.0)
    label_arc = MAX_LABEL_LEN * 6 + 10
    min_angles = [
        max(2 * _node_radius(nodes.get(k, {}).get("type", "EndDevice")) + COLLISION_GAP, label_arc)
        / r_at_depth
        for k in sorted_kids
    ]

    # First pass: compute raw proportional spans
    raw_spans = [span * leaf_counts.get(k, 1) / total for k in sorted_kids]

    # Second pass: apply geometry-aware minimum floor per child
    floored = [max(raw, mn) for raw, mn in zip(raw_spans, min_angles)]
    floored_total = sum(floored)
    if floored_total > span:
        # Scale down so they still fit in the allotted range
        floored = [s * span / floored_total for s in floored]

    cursor = start
    for kid, kid_span in zip(sorted_kids, floored):
        _assign_angles(
            kid,
            children,
            leaf_counts,
            angles,
            cursor,
            cursor + kid_span,
            depth_map,
            nodes,
            ring_radii,
        )
        cursor += kid_span


# ── Collision resolution ──────────────────────────────────────────────────────


def _resolve_collisions(
    positions: dict[str, tuple[float, float]],
    angles: dict[str, float],
    depth_map: dict[str, int],
    nodes: dict[str, dict[str, Any]],
    cx: float,
    cy: float,
    ring_radii: dict[int, float],
) -> None:
    """Push overlapping nodes apart by nudging their angles within their hop ring.

    Nodes stay on their ring radius — only the angle changes. Uses a Gauss-Seidel
    approach (positions updated immediately after each pair nudge) for fast convergence.
    Iterates up to COLLISION_ITERS times; exits early when no overlap remains.
    """
    by_depth: dict[int, list[str]] = {}
    for ieee, depth in depth_map.items():
        if depth > 0:
            by_depth.setdefault(depth, []).append(ieee)

    ring_r: dict[str, float] = {
        ieee: (ring_radii.get(depth - 1, 0.0) + ring_radii.get(depth, depth * MIN_RING_GAP)) / 2
        for ieee, depth in depth_map.items()
        if depth > 0
    }

    for _ in range(COLLISION_ITERS):
        moved = False
        for depth_nodes in by_depth.values():
            n = len(depth_nodes)
            for i in range(n):
                a = depth_nodes[i]
                for j in range(i + 1, n):
                    b = depth_nodes[j]
                    ax, ay = positions[a]
                    bx, by = positions[b]
                    dist = math.hypot(ax - bx, ay - by)
                    ra = _node_radius(nodes[a].get("type", "EndDevice"))
                    rb = _node_radius(nodes[b].get("type", "EndDevice"))
                    label_arc = MAX_LABEL_LEN * 6 + 10
                    min_dist = max(ra + rb + COLLISION_GAP, label_arc)
                    if dist >= min_dist:
                        continue
                    moved = True
                    # Convert linear overlap to angular nudge at the average ring radius
                    r = (ring_r[a] + ring_r[b]) / 2
                    angular_overlap = (min_dist - dist) / max(r, 1.0)
                    # Push apart: determine which direction by signed angle difference
                    diff = (angles[b] - angles[a] + math.pi) % (2 * math.pi) - math.pi
                    nudge = angular_overlap / 2
                    if diff >= 0:
                        angles[a] -= nudge
                        angles[b] += nudge
                    else:
                        angles[a] += nudge
                        angles[b] -= nudge
                    # Recompute positions on their fixed ring radii
                    rra, rrb = ring_r[a], ring_r[b]
                    positions[a] = (cx + rra * math.sin(angles[a]), cy - rra * math.cos(angles[a]))
                    positions[b] = (cx + rrb * math.sin(angles[b]), cy - rrb * math.cos(angles[b]))
        if not moved:
            break


# ── SVG drawing helpers ───────────────────────────────────────────────────────


def _label_anchor(angle: float) -> str:
    """Text anchor based on which half of the circle the node is on."""
    # angle=0 → north, increases clockwise
    x_component = math.sin(angle)
    if abs(x_component) < 0.25:
        return "middle"
    return "start" if x_component > 0 else "end"


def _add_defs_filters(dwg: svgwrite.Drawing) -> None:
    """Inject glow filters for WEAK and CRITICAL nodes into <defs>.

    Each filter blurs the source alpha, floods it with the glow colour, and
    merges the result behind the original shape.
    """
    for fid, color, std_dev in [
        ("glow-warn", EDGE_WARN, "6"),
        ("glow-crit", EDGE_CRIT, "8"),
    ]:
        f = dwg.filter(id=fid, x="-60%", y="-60%", width="220%", height="220%")
        f.feGaussianBlur(in_="SourceAlpha", stdDeviation=std_dev, result="blur")
        f.feFlood(flood_color=color, flood_opacity="0.85", result="flood")
        f.feComposite(in_="flood", in2="blur", operator="in", result="glow")
        f.feMerge(layernames=["glow", "SourceGraphic"])
        dwg.defs.add(f)


def _draw_legend(
    dwg: svgwrite.Drawing,
    canvas: int,
    warn_lqi: int,
    critical_lqi: int,
) -> None:
    lx, ly = 20, 20
    lw, lh = 280, 290
    row = 24

    g = dwg.g(id="legend")
    g.add(
        dwg.rect(
            insert=(lx, ly),
            size=(lw, lh),
            rx=8,
            fill="#1e293b",
            stroke="#334155",
            stroke_width=1,
        )
    )
    g.add(
        dwg.text(
            "Legend",
            insert=(lx + lw // 2, ly + 18),
            fill=TEXT_PRIMARY,
            font_size="12px",
            text_anchor="middle",
            font_weight="bold",
        )
    )

    y = ly + 42

    # Node types
    for fill, r, label in [
        (COORD_FILL, 9, "Coordinator"),
        (ROUTER_FILL, 7, "Router"),
        (END_FILL, 5, "End device"),
    ]:
        g.add(dwg.circle(center=(lx + 16, y - 3), r=r, fill=fill))
        g.add(dwg.text(label, insert=(lx + 30, y), fill=TEXT_PRIMARY, font_size=LEGEND_FS))
        y += row

    y += 6

    # Glow indicators for problem nodes
    g.add(
        dwg.circle(
            center=(lx + 16, y - 3),
            r=7,
            fill=ROUTER_FILL,
            stroke=EDGE_WARN,
            stroke_width=2,
            filter="url(#glow-warn)",
        )
    )
    g.add(
        dwg.text(
            f"Weak node  (LQI < {warn_lqi})",
            insert=(lx + 30, y),
            fill=EDGE_WARN,
            font_size=LEGEND_FS,
        )
    )
    y += row

    g.add(
        dwg.circle(
            center=(lx + 16, y - 3),
            r=7,
            fill=ROUTER_FILL,
            stroke=EDGE_CRIT,
            stroke_width=2,
            filter="url(#glow-crit)",
        )
    )
    g.add(
        dwg.text(
            f"Critical node  (LQI < {critical_lqi})",
            insert=(lx + 30, y),
            fill=EDGE_CRIT,
            font_size=LEGEND_FS,
        )
    )
    y += row + 6

    # In-circle LQI explanation: small annotated circle
    ex = lx + 16
    ey = y - 3
    g.add(dwg.circle(center=(ex, ey), r=7, fill=ROUTER_FILL))
    badge_w_ex = 14
    g.add(
        dwg.rect(
            insert=(ex - badge_w_ex / 2, ey - 5),
            size=(badge_w_ex, 10),
            rx=3,
            fill="#0f172a",
            opacity="0.82",
        )
    )
    g.add(
        dwg.text(
            "42",
            insert=(ex, ey + 3),
            fill=EDGE_GOOD,
            font_size="8px",
            font_weight="bold",
            text_anchor="middle",
        )
    )
    g.add(
        dwg.text(
            "path min LQI (worst hop)",
            insert=(lx + 30, y),
            fill=TEXT_DIM,
            font_size=LEGEND_FS,
        )
    )
    y += row

    # Edge quality
    for color, label in [
        (EDGE_GOOD, f"LQI \u2265 {warn_lqi}  (good)"),
        (EDGE_WARN, f"LQI {critical_lqi}\u2013{warn_lqi}  (weak)"),
        (EDGE_CRIT, f"LQI < {critical_lqi}  (critical)"),
    ]:
        g.add(dwg.line(start=(lx + 8, y - 4), end=(lx + 26, y - 4), stroke=color, stroke_width=2))
        g.add(dwg.text(label, insert=(lx + 32, y), fill=TEXT_PRIMARY, font_size=LEGEND_FS))
        y += row - 4

    dwg.add(g)


# ── Public entry point ────────────────────────────────────────────────────────


def render_svg(
    nodes: dict[str, dict[str, Any]],
    parent_map: dict[str, str | None],
    lqi_map: dict[str, int],
    depth_map: dict[str, int],
    children: dict[str, list[str]],
    output_path: Path,
    warn_lqi: int = 80,
    critical_lqi: int = 30,
    coord_lqi_map: dict[str, int] | None = None,
) -> None:
    """Render a radial Zigbee network map to *output_path* as SVG."""
    coordinator_ieee = next(
        (ieee for ieee, n in nodes.items() if n.get("type") == "Coordinator"), None
    )
    if coordinator_ieee is None:
        return

    # ── Layout geometry ───────────────────────────────────────────────────────
    max_hops = max(depth_map.values(), default=1)
    ring_radii = _compute_ring_radii(depth_map, nodes)
    half = max(ring_radii.values()) + LABEL_MARGIN
    canvas = int(half * 2)
    cx, cy = half, half

    leaf_counts = _subtree_weights(coordinator_ieee, children)
    angles: dict[str, float] = {}
    _assign_angles(
        coordinator_ieee,
        children,
        leaf_counts,
        angles,
        0.0,
        2 * math.pi,
        depth_map,
        nodes,
        ring_radii,
    )
    path_min_lqi = _compute_path_min_lqi(parent_map, lqi_map)

    positions: dict[str, tuple[float, float]] = {}
    for ieee, angle in angles.items():
        depth = depth_map.get(ieee, 0)
        if depth > 0:
            prev_r = ring_radii.get(depth - 1, 0.0)
            curr_r = ring_radii.get(depth, depth * MIN_RING_GAP)
            r = (prev_r + curr_r) / 2
        else:
            r = 0.0
        positions[ieee] = (
            cx + r * math.sin(angle),
            cy - r * math.cos(angle),
        )

    _resolve_collisions(positions, angles, depth_map, nodes, cx, cy, ring_radii)

    # ── Drawing ───────────────────────────────────────────────────────────────
    dwg = svgwrite.Drawing(
        str(output_path),
        size=(canvas, canvas),
        profile="full",
        **{"font-family": "system-ui, -apple-system, sans-serif"},
    )
    _add_defs_filters(dwg)
    dwg.add(dwg.rect(insert=(0, 0), size=(canvas, canvas), fill=BG))

    # Ring band fills (outermost → innermost so each disc overwrites its interior)
    ring_fill_group = dwg.g(id="ring-fills")
    for h in range(max_hops, 0, -1):
        t = (h - 1) / max(max_hops - 1, 1)
        band_fill = _lerp_color(t, "#0d2420", "#201018")
        ring_fill_group.add(
            dwg.circle(
                center=(cx, cy),
                r=ring_radii[h],
                fill=band_fill,
                stroke="none",
            )
        )
    # Restore the coordinator centre area to background
    ring_fill_group.add(dwg.circle(center=(cx, cy), r=NODE_R_COORD + 10, fill=BG, stroke="none"))
    dwg.add(ring_fill_group)

    # Ring guides
    ring_group = dwg.g(id="rings")
    for h in range(1, max_hops + 1):
        t = (h - 1) / max(max_hops - 1, 1)  # 0.0 at hop 1, 1.0 at outermost
        ring_stroke = _lerp_color(t, "#1e4035", "#3d1e2e")
        ring_label_c = HOP_COLORS[(h - 1) % len(HOP_COLORS)]
        ring_r = ring_radii[h]
        ring_group.add(
            dwg.circle(
                center=(cx, cy),
                r=ring_r,
                fill="none",
                stroke=ring_stroke,
                stroke_width=1,
                stroke_dasharray="5,4",
            )
        )
        ring_group.add(
            dwg.text(
                f"Hop {h}",
                insert=(cx, cy - ring_r + 14),
                fill=ring_label_c,
                font_size="12px",
                text_anchor="middle",
                font_weight="bold",
                letter_spacing="0.5",
            )
        )
    dwg.add(ring_group)

    # Edges + LQI pill badges
    _coord_lqi = coord_lqi_map or {}
    edge_group = dwg.g(id="edges", opacity=str(EDGE_OPACITY))
    lqi_label_group = dwg.g(id="lqi-labels")
    for ieee, parent_ieee in parent_map.items():
        if parent_ieee is None:
            continue
        x1, y1 = positions[ieee]
        x2, y2 = positions[parent_ieee]
        lqi = lqi_map.get(ieee, 0)
        color = _edge_color(lqi, warn_lqi, critical_lqi)
        edge_group.add(
            dwg.line(
                start=(round(x1, 1), round(y1, 1)),
                end=(round(x2, 1), round(y2, 1)),
                stroke=color,
                stroke_width=_edge_width(lqi),
            )
        )
        # For depth-1 devices with asymmetric links, show both directions:
        # ↓N = downlink (coordinator → device), ↑N = uplink (device → coordinator)
        up_lqi = _coord_lqi.get(ieee)
        if depth_map.get(ieee, 0) == 1 and up_lqi is not None and up_lqi != lqi:
            lqi_text = f"\u2193{lqi} \u2191{up_lqi}"
        else:
            lqi_text = str(lqi)
        mx, my = x1 * 0.3 + x2 * 0.7, y1 * 0.3 + y2 * 0.7
        badge_w = len(lqi_text) * 7 + 10
        lqi_label_group.add(
            dwg.rect(
                insert=(round(mx - badge_w / 2, 1), round(my - 9, 1)),
                size=(badge_w, 13),
                rx=4,
                fill="#0f172a",
                opacity="0.85",
            )
        )
        lqi_label_group.add(
            dwg.text(
                lqi_text,
                insert=(round(mx, 1), round(my + 1, 1)),
                fill=color,
                font_size=DIM_FS,
                text_anchor="middle",
                opacity="0.95",
            )
        )
    dwg.add(edge_group)

    # Nodes + labels
    node_group = dwg.g(id="nodes")
    label_group = dwg.g(id="labels")

    for ieee, (x, y) in positions.items():
        node = nodes[ieee]
        node_type = node.get("type", "EndDevice")
        name = node.get("friendlyName", ieee)
        lqi = lqi_map.get(ieee, 0)
        fill = _node_fill(node_type)
        nr = _node_radius(node_type)
        is_coord = node_type == "Coordinator"

        # Status ring + glow filter on problem nodes
        stroke_color = fill
        stroke_w = 0
        glow_filter: str | None = None
        if not is_coord:
            if lqi < critical_lqi:
                stroke_color = EDGE_CRIT
                stroke_w = 3
                glow_filter = "url(#glow-crit)"
            elif lqi < warn_lqi:
                stroke_color = EDGE_WARN
                stroke_w = 2
                glow_filter = "url(#glow-warn)"

        circle_attrs: dict[str, Any] = dict(
            center=(round(x, 1), round(y, 1)),
            r=nr,
            fill=fill,
            stroke=stroke_color,
            stroke_width=stroke_w,
        )
        if glow_filter:
            circle_attrs["filter"] = glow_filter
        node_group.add(dwg.circle(**circle_attrs))

        # Path-min LQI badge — shown inside every non-coordinator device node.
        # Displays the worst-hop LQI on the path from coordinator to this device.
        path_lqi = path_min_lqi.get(ieee, 0)
        if not is_coord:
            lqi_color = _edge_color(path_lqi, warn_lqi, critical_lqi)
            is_router = node_type == "Router"
            badge_fs = "9px" if is_router else "8px"
            char_w = 6 if is_router else 5
            badge_h = 11 if is_router else 10
            badge_w = len(str(path_lqi)) * char_w + 8
            node_group.add(
                dwg.rect(
                    insert=(round(x - badge_w / 2, 1), round(y - badge_h / 2, 1)),
                    size=(badge_w, badge_h),
                    rx=3,
                    fill="#0f172a",
                    opacity="0.82",
                )
            )
            node_group.add(
                dwg.text(
                    str(path_lqi),
                    insert=(round(x, 1), round(y + badge_h * 0.3, 1)),
                    fill=lqi_color,
                    font_size=badge_fs,
                    font_weight="bold",
                    text_anchor="middle",
                )
            )

        # Label: radially offset outward from center
        angle = angles.get(ieee, 0.0)
        if is_coord:
            lx, ly_label = x, y + nr + 16
            anchor = "middle"
        else:
            offset = nr + 14
            lx = x + math.sin(angle) * offset
            ly_label = y - math.cos(angle) * offset
            anchor = _label_anchor(angle)

        # Pill background behind name
        display_name = (name[: MAX_LABEL_LEN - 1] + "…") if len(name) > MAX_LABEL_LEN else name
        pill_h = 16
        pill_w = len(display_name) * 6 + 10
        if anchor == "start":
            pill_x = lx - 4
        elif anchor == "end":
            pill_x = lx - pill_w + 4
        else:  # middle
            pill_x = lx - pill_w / 2
        pill_y = ly_label - 13
        label_group.add(
            dwg.rect(
                insert=(round(pill_x, 1), round(pill_y, 1)),
                size=(pill_w, pill_h),
                rx=5,
                fill="#0f172a",
                opacity="0.7",
            )
        )

        lbl = dwg.text(
            display_name,
            insert=(round(lx, 1), round(ly_label, 1)),
            fill=TEXT_PRIMARY,
            font_size=LABEL_FS,
            text_anchor=anchor,
        )
        if display_name != name:
            lbl.set_desc(title=name)  # renders as <title> child for SVG tooltip
        label_group.add(lbl)

    dwg.add(node_group)
    dwg.add(label_group)
    dwg.add(lqi_label_group)

    # Legend
    _draw_legend(dwg, canvas, warn_lqi, critical_lqi)

    dwg.save()
