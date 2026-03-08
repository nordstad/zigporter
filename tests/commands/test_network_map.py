"""Tests for the network-map command."""

import io
import math
import re
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from rich.console import Console

from zigporter.commands.network_map import (
    _build_routing_tree,
    _build_zha_topology_from_devices,
    run_network_map,
)
from zigporter.commands.network_map_svg import (
    ANGULAR_PADDING,
    COLLISION_GAP,
    EDGE_CRIT,
    EDGE_GOOD,
    EDGE_WARN,
    MAX_LABEL_LEN,
    MIN_RING_GAP,
    NODE_R_ROUTER,
    _compute_ring_radii,
    _edge_color,
    _label_anchor,
    _subtree_weights,
    render_svg,
)

# Pre-compute arc_per_device used by _compute_ring_radii for assertions
_LABEL_ARC = MAX_LABEL_LEN * 6 + 10
_ARC_PER_DEVICE = max(2 * NODE_R_ROUTER + COLLISION_GAP, _LABEL_ARC) + ANGULAR_PADDING


HA_URL = "https://ha.test"
TOKEN = "test-token"
Z2M_URL = "https://z2m.test"

# ---------------------------------------------------------------------------
# Shared fixture data
# ---------------------------------------------------------------------------

MOCK_NETWORK_MAP_RESPONSE: dict = {
    "data": {
        "nodes": [
            {
                "ieeeAddr": "0x0000000000000000",
                "friendlyName": "Coordinator",
                "type": "Coordinator",
            },
            {
                "ieeeAddr": "0x0000000000000001",
                "friendlyName": "Router Alpha",
                "type": "Router",
            },
            {
                "ieeeAddr": "0x0000000000000002",
                "friendlyName": "Router Beta",
                "type": "Router",
            },
            {
                "ieeeAddr": "0x0000000000000003",
                "friendlyName": "Router Gamma",
                "type": "Router",
            },
            {
                "ieeeAddr": "0x0000000000000004",
                "friendlyName": "Sensor A",
                "type": "EndDevice",
            },
            {
                "ieeeAddr": "0x0000000000000005",
                "friendlyName": "Sensor B",
                "type": "EndDevice",
            },
            {
                "ieeeAddr": "0x0000000000000006",
                "friendlyName": "Sensor C",
                "type": "EndDevice",
            },
            {
                "ieeeAddr": "0x0000000000000007",
                "friendlyName": "Sensor D",
                "type": "EndDevice",
            },
            {
                "ieeeAddr": "0x0000000000000008",
                "friendlyName": "Sensor E",
                "type": "EndDevice",
            },
            {
                "ieeeAddr": "0x0000000000000009",
                "friendlyName": "Sensor F",
                "type": "EndDevice",
            },
        ],
        "links": [
            # Router Alpha → coordinator  (lqi=255, depth 1)
            {
                "source": {"ieeeAddr": "0x0000000000000001", "networkAddress": 1},
                "target": {"ieeeAddr": "0x0000000000000000", "networkAddress": 0},
                "lqi": 255,
                "depth": 1,
            },
            # Router Beta → coordinator  (lqi=200, depth 1)
            {
                "source": {"ieeeAddr": "0x0000000000000002", "networkAddress": 2},
                "target": {"ieeeAddr": "0x0000000000000000", "networkAddress": 0},
                "lqi": 200,
                "depth": 1,
            },
            # Router Gamma → Router Alpha  (lqi=150, depth 2)
            {
                "source": {"ieeeAddr": "0x0000000000000003", "networkAddress": 3},
                "target": {"ieeeAddr": "0x0000000000000001", "networkAddress": 1},
                "lqi": 150,
                "depth": 2,
            },
            # Sensor A → Router Alpha  (lqi=187, healthy)
            {
                "source": {"ieeeAddr": "0x0000000000000004", "networkAddress": 4},
                "target": {"ieeeAddr": "0x0000000000000001", "networkAddress": 1},
                "lqi": 187,
                "depth": 2,
            },
            # Sensor B → Router Beta  (lqi=169, healthy)
            {
                "source": {"ieeeAddr": "0x0000000000000005", "networkAddress": 5},
                "target": {"ieeeAddr": "0x0000000000000002", "networkAddress": 2},
                "lqi": 169,
                "depth": 2,
            },
            # Sensor C → Router Gamma  (lqi=65, WEAK — below default warn_lqi=80)
            {
                "source": {"ieeeAddr": "0x0000000000000006", "networkAddress": 6},
                "target": {"ieeeAddr": "0x0000000000000003", "networkAddress": 3},
                "lqi": 65,
                "depth": 3,
            },
            # Sensor D → Router Gamma  (lqi=25, CRITICAL — below default critical_lqi=30)
            {
                "source": {"ieeeAddr": "0x0000000000000007", "networkAddress": 7},
                "target": {"ieeeAddr": "0x0000000000000003", "networkAddress": 3},
                "lqi": 25,
                "depth": 3,
            },
            # Sensor E → Router Alpha  (lqi=120, healthy)
            {
                "source": {"ieeeAddr": "0x0000000000000008", "networkAddress": 8},
                "target": {"ieeeAddr": "0x0000000000000001", "networkAddress": 1},
                "lqi": 120,
                "depth": 2,
            },
            # Sensor F → Router Beta  (lqi=90, healthy)
            {
                "source": {"ieeeAddr": "0x0000000000000009", "networkAddress": 9},
                "target": {"ieeeAddr": "0x0000000000000002", "networkAddress": 2},
                "lqi": 90,
                "depth": 2,
            },
        ],
    }
}

# ---------------------------------------------------------------------------
# Unit tests for _build_routing_tree
# ---------------------------------------------------------------------------


def _build_nodes() -> dict:
    data = MOCK_NETWORK_MAP_RESPONSE["data"]
    return {n["ieeeAddr"]: n for n in data["nodes"]}


def test_build_routing_tree_coordinator_has_no_parent():
    nodes = _build_nodes()
    links = MOCK_NETWORK_MAP_RESPONSE["data"]["links"]
    parent_map, _, _ = _build_routing_tree(nodes, links)
    assert parent_map["0x0000000000000000"] is None


def test_build_routing_tree_direct_children_of_coordinator():
    nodes = _build_nodes()
    links = MOCK_NETWORK_MAP_RESPONSE["data"]["links"]
    parent_map, lqi_map, depth_map = _build_routing_tree(nodes, links)

    # Router Alpha and Router Beta connect directly to coordinator
    assert parent_map["0x0000000000000001"] == "0x0000000000000000"
    assert parent_map["0x0000000000000002"] == "0x0000000000000000"
    assert depth_map["0x0000000000000001"] == 1
    assert depth_map["0x0000000000000002"] == 1
    assert lqi_map["0x0000000000000001"] == 255
    assert lqi_map["0x0000000000000002"] == 200


def test_build_routing_tree_nested_router():
    nodes = _build_nodes()
    links = MOCK_NETWORK_MAP_RESPONSE["data"]["links"]
    parent_map, lqi_map, depth_map = _build_routing_tree(nodes, links)

    # Router Gamma connects via Router Alpha at depth 2
    assert parent_map["0x0000000000000003"] == "0x0000000000000001"
    assert depth_map["0x0000000000000003"] == 2
    assert lqi_map["0x0000000000000003"] == 150


def test_build_routing_tree_end_devices_placed_correctly():
    nodes = _build_nodes()
    links = MOCK_NETWORK_MAP_RESPONSE["data"]["links"]
    parent_map, lqi_map, depth_map = _build_routing_tree(nodes, links)

    # Sensor D (CRITICAL) is under Router Gamma at depth 3
    assert parent_map["0x0000000000000007"] == "0x0000000000000003"
    assert depth_map["0x0000000000000007"] == 3
    assert lqi_map["0x0000000000000007"] == 25


def test_build_routing_tree_bidirectional_lqi_uses_minimum():
    """Asymmetric link: device reports LQI 115 to coordinator, coordinator reports 29 back.
    The recorded lqi_map value must be min(115, 29) = 29 — the real bottleneck."""
    nodes = {
        "0xcoord": {"ieeeAddr": "0xcoord", "friendlyName": "Coordinator", "type": "Coordinator"},
        "0xdev": {"ieeeAddr": "0xdev", "friendlyName": "SLZB-06P7", "type": "Router"},
    }
    links = [
        # Device reports coordinator at LQI 115 (device's perspective)
        {
            "source": {"ieeeAddr": "0xDEV"},
            "target": {"ieeeAddr": "0xCOORD"},
            "lqi": 115,
        },
        # Coordinator reports device at LQI 29 (coordinator's perspective)
        {
            "source": {"ieeeAddr": "0xCOORD"},
            "target": {"ieeeAddr": "0xDEV"},
            "lqi": 29,
        },
    ]
    parent_map, lqi_map, depth_map = _build_routing_tree(nodes, links)
    assert parent_map["0xdev"] == "0xcoord"
    assert lqi_map["0xdev"] == 29, f"Expected 29 (min of 115,29), got {lqi_map['0xdev']}"
    assert depth_map["0xdev"] == 1


def test_build_routing_tree_empty_links_orphans_attached_to_coordinator():
    nodes = _build_nodes()
    parent_map, lqi_map, depth_map = _build_routing_tree(nodes, [])

    coord = "0x0000000000000000"
    for ieee in nodes:
        if ieee == coord:
            continue
        assert parent_map[ieee] == coord
        assert lqi_map[ieee] == 0
        assert depth_map[ieee] == 1


# ---------------------------------------------------------------------------
# Integration tests via run_network_map (captured console output)
# ---------------------------------------------------------------------------


def _make_console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    con = Console(file=buf, highlight=False, markup=True, force_terminal=False, width=200)
    return con, buf


def _make_mock_progress():
    mock_progress = AsyncMock()
    mock_progress.__enter__ = lambda s: mock_progress
    mock_progress.__exit__ = lambda s, *a: False
    mock_progress.add_task = lambda *a, **kw: 0
    mock_progress.update = lambda *a, **kw: None
    return mock_progress


async def _run_with_capture(
    output_format: str = "tree",
    warn_lqi: int = 80,
    critical_lqi: int = 30,
):
    mock_client = AsyncMock()
    mock_client.get_network_map = AsyncMock(return_value=MOCK_NETWORK_MAP_RESPONSE)
    buf = io.StringIO()
    cap_console = Console(file=buf, highlight=False, markup=True, force_terminal=False, width=200)

    with (
        patch("zigporter.commands.network_map.Z2MClient", return_value=mock_client),
        patch("zigporter.commands.network_map.console", cap_console),
        patch("zigporter.commands.network_map.Progress", return_value=_make_mock_progress()),
    ):
        await run_network_map(
            HA_URL,
            TOKEN,
            Z2M_URL,
            verify_ssl=False,
            output_format=output_format,
            warn_lqi=warn_lqi,
            critical_lqi=critical_lqi,
            backend="z2m",
        )

    return buf.getvalue()


async def test_tree_output_contains_coordinator():
    output = await _run_with_capture()
    assert "Coordinator" in output


async def test_tree_output_shows_lqi():
    output = await _run_with_capture()
    assert "255" in output
    assert "25" in output


async def test_critical_device_flagged():
    output = await _run_with_capture()
    assert "CRITICAL" in output


async def test_weak_device_flagged():
    output = await _run_with_capture()
    assert "WEAK" in output


async def test_table_sorted_by_lqi_ascending():
    output = await _run_with_capture(output_format="table")
    # Sensor D (lqi=25) must appear before Sensor C (lqi=65) which must appear before Router Alpha (lqi=255)
    pos_d = output.find("Sensor D")
    pos_c = output.find("Sensor C")
    pos_alpha = output.find("Router Alpha")
    assert pos_d < pos_c < pos_alpha, (
        f"Expected Sensor D ({pos_d}) < Sensor C ({pos_c}) < Router Alpha ({pos_alpha})"
    )


async def test_summary_counts():
    output = await _run_with_capture()
    # 9 non-coordinator devices: 3 routers, 6 end-devices
    assert "9 devices" in output
    assert "3 routers" in output
    assert "6 end-devices" in output
    # 1 weak (Sensor C, lqi=65), 1 critical (Sensor D, lqi=25)
    assert "1 WEAK" in output
    assert "1 CRITICAL" in output


# ---------------------------------------------------------------------------
# SVG renderer tests
# ---------------------------------------------------------------------------


_SVG_NODES = {
    "0x0": {"type": "Coordinator", "friendlyName": "Coordinator"},
    "0x1": {"type": "Router", "friendlyName": "Short Name"},
    "0x2": {"type": "EndDevice", "friendlyName": "A Very Long Device Name That Exceeds Limit"},
}
_SVG_PARENT_MAP: dict[str, str | None] = {"0x0": None, "0x1": "0x0", "0x2": "0x1"}
_SVG_LQI_MAP = {"0x1": 200, "0x2": 150}
_SVG_DEPTH_MAP = {"0x0": 0, "0x1": 1, "0x2": 2}
_SVG_CHILDREN = {"0x0": ["0x1"], "0x1": ["0x2"]}


def test_svg_label_truncated_in_output():
    """Long names are truncated in the SVG text content."""
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
        out = Path(f.name)
    render_svg(_SVG_NODES, _SVG_PARENT_MAP, _SVG_LQI_MAP, _SVG_DEPTH_MAP, _SVG_CHILDREN, out)
    content = out.read_text()
    long_name = "A Very Long Device Name That Exceeds Limit"
    truncated = long_name[: MAX_LABEL_LEN - 1] + "…"
    assert truncated in content, "truncated label should appear in SVG"
    # The full name must not appear as a bare <text> node value

    bare_text_values = re.findall(r"<text\b[^>]*>([^<]+)</text>", content)
    assert long_name not in bare_text_values, "full name should not be a bare <text> value"


def test_svg_title_tooltip_contains_full_name():
    """Full name is preserved in a <title> tooltip for truncated labels."""
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
        out = Path(f.name)
    render_svg(_SVG_NODES, _SVG_PARENT_MAP, _SVG_LQI_MAP, _SVG_DEPTH_MAP, _SVG_CHILDREN, out)
    content = out.read_text()
    long_name = "A Very Long Device Name That Exceeds Limit"
    assert f"<title>{long_name}</title>" in content, "<title> tooltip should contain full name"


def test_svg_short_name_not_truncated():
    """Names within the limit are rendered verbatim with no tooltip."""
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
        out = Path(f.name)
    render_svg(_SVG_NODES, _SVG_PARENT_MAP, _SVG_LQI_MAP, _SVG_DEPTH_MAP, _SVG_CHILDREN, out)
    content = out.read_text()
    assert "Short Name" in content
    assert "<title>Short Name</title>" not in content


def test_subtree_weights_leaf_gets_one():
    """A pure leaf node has weight 1."""
    children: dict[str, list[str]] = {"root": ["child"], "child": []}
    weights = _subtree_weights("root", children)
    assert weights["child"] == 1


def test_subtree_weights_linear_chain_uses_depth():
    """A 3-hop linear chain (root→A→B→C) gets weight 3, not 1."""
    children: dict[str, list[str]] = {"root": ["a"], "a": ["b"], "b": ["c"], "c": []}
    weights = _subtree_weights("root", children)
    # 'a' subtree: leaf_count=1, depth=3 → weight=3
    assert weights["a"] == 3


def test_subtree_weights_wide_hub_uses_leaf_count():
    """A hub with 5 direct children keeps its leaf count as the weight."""
    children: dict[str, list[str]] = {
        "root": ["hub"],
        "hub": ["c1", "c2", "c3", "c4", "c5"],
        "c1": [],
        "c2": [],
        "c3": [],
        "c4": [],
        "c5": [],
    }
    weights = _subtree_weights("root", children)
    # hub: leaf_count=5, depth=2 → weight=5
    assert weights["hub"] == 5


# ── _edge_color helper ────────────────────────────────────────────────────────


def test_edge_color_good():
    assert _edge_color(200, 80, 30) == EDGE_GOOD


def test_edge_color_warn():
    assert _edge_color(60, 80, 30) == EDGE_WARN


def test_edge_color_crit():
    assert _edge_color(10, 80, 30) == EDGE_CRIT


# ── _label_anchor helper ─────────────────────────────────────────────────────


def test_label_anchor_east_returns_start():
    assert _label_anchor(math.pi / 2) == "start"  # due east, sin=1


def test_label_anchor_west_returns_end():
    assert _label_anchor(3 * math.pi / 2) == "end"  # due west, sin=-1


def test_label_anchor_north_returns_middle():
    assert _label_anchor(0.0) == "middle"  # due north, sin=0


# ── render_svg branch coverage ───────────────────────────────────────────────


def test_svg_no_coordinator_returns_early():
    """render_svg must return silently when no coordinator is present."""
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
        out = Path(f.name)
    nodes = {"0x1": {"type": "Router", "friendlyName": "Router"}}
    render_svg(nodes, {"0x1": None}, {"0x1": 200}, {"0x1": 1}, {}, out)
    # File should be empty / unchanged (early return before writing)
    assert out.stat().st_size == 0 or "svg" not in out.read_text().lower()


def test_svg_warn_and_crit_nodes_render_glow_filters():
    """Devices with LQI below warn/crit thresholds trigger glow filter markup."""
    nodes = {
        "0x0": {"type": "Coordinator", "friendlyName": "Coordinator"},
        "0x1": {"type": "Router", "friendlyName": "Weak Router"},
        "0x2": {"type": "Router", "friendlyName": "Critical Router"},
    }
    parent_map: dict[str, str | None] = {"0x0": None, "0x1": "0x0", "0x2": "0x0"}
    lqi_map = {"0x1": 60, "0x2": 20}  # 60 < warn=80; 20 < crit=30
    depth_map = {"0x0": 0, "0x1": 1, "0x2": 1}
    children = {"0x0": ["0x1", "0x2"]}
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
        out = Path(f.name)
    render_svg(nodes, parent_map, lqi_map, depth_map, children, out)
    content = out.read_text()
    assert "glow-warn" in content, "warn glow filter should be referenced"
    assert "glow-crit" in content, "crit glow filter should be referenced"


def test_svg_crowded_ring_triggers_arc_floor_scaling_and_collision_resolution():
    """15 end-device children crowd depth-1 ring, forcing arc-floor scaling
    (line 176) and collision nudging in _resolve_collisions (lines 228-244)."""
    n = 15
    nodes: dict = {"0x0": {"type": "Coordinator", "friendlyName": "Coordinator"}}
    parent_map: dict[str, str | None] = {"0x0": None}
    lqi_map: dict = {}
    depth_map: dict = {"0x0": 0}
    children: dict = {"0x0": []}
    for i in range(1, n + 1):
        ieee = f"0x{i:02x}"
        nodes[ieee] = {"type": "EndDevice", "friendlyName": f"Device {i}"}
        parent_map[ieee] = "0x0"
        lqi_map[ieee] = 200
        depth_map[ieee] = 1
        children["0x0"].append(ieee)
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
        out = Path(f.name)
    render_svg(nodes, parent_map, lqi_map, depth_map, children, out)
    content = out.read_text()
    assert "Device 1" in content
    assert "Device 15" in content


def test_svg_two_children_cover_start_and_end_pill_anchors():
    """Coordinator with two children places nodes on east and west sides."""
    nodes = {
        "0x0": {"type": "Coordinator", "friendlyName": "Coordinator"},
        "0x1": {"type": "Router", "friendlyName": "East Child"},
        "0x2": {"type": "Router", "friendlyName": "West Child"},
    }
    parent_map: dict[str, str | None] = {"0x0": None, "0x1": "0x0", "0x2": "0x0"}
    lqi_map = {"0x1": 200, "0x2": 200}
    depth_map = {"0x0": 0, "0x1": 1, "0x2": 1}
    children = {"0x0": ["0x1", "0x2"]}
    with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
        out = Path(f.name)
    render_svg(nodes, parent_map, lqi_map, depth_map, children, out)
    content = out.read_text()
    assert "East Child" in content
    assert "West Child" in content


# ── _compute_ring_radii tests ─────────────────────────────────────────────────


def _make_depth_map(counts: list[int]) -> dict[str, int]:
    """Build a flat depth_map from a list of device counts per hop."""
    dm: dict[str, int] = {"0xcoord": 0}
    idx = 1
    for hop, n in enumerate(counts, start=1):
        for _ in range(n):
            dm[f"0x{idx:04x}"] = hop
            idx += 1
    return dm


def _make_nodes(depth_map: dict[str, int]) -> dict[str, dict]:
    return {
        ieee: {"type": "Coordinator" if d == 0 else "Router", "friendlyName": ieee}
        for ieee, d in depth_map.items()
    }


def test_compute_ring_radii_single_device_uses_min_gap():
    """A single hop-1 device must still produce a ring at least MIN_RING_GAP wide."""
    dm = _make_depth_map([1])
    radii = _compute_ring_radii(dm, _make_nodes(dm))
    assert radii[1] >= MIN_RING_GAP


def test_compute_ring_radii_monotonically_increasing():
    """Each successive ring boundary must be strictly larger than the previous."""
    dm = _make_depth_map([10, 5, 2, 1])
    radii = _compute_ring_radii(dm, _make_nodes(dm))
    values = [radii[h] for h in sorted(radii)]
    assert all(values[i] < values[i + 1] for i in range(len(values) - 1))


def test_compute_ring_radii_node_placement_meets_label_arc():
    """Node placement radius (midpoint of ring band) gives enough circumference per device.

    Each device must have at least _ARC_PER_DEVICE px of arc at its placement radius.
    """
    import math

    n = 12
    dm = _make_depth_map([n])
    radii = _compute_ring_radii(dm, _make_nodes(dm))
    node_r = radii[1] / 2  # prev_r=0, midpoint = ring[1] / 2
    arc_available = 2 * math.pi * node_r / n
    assert arc_available >= _ARC_PER_DEVICE, (
        f"arc per device {arc_available:.1f}px < required {_ARC_PER_DEVICE}px"
    )


def test_compute_ring_radii_crowded_ring_grows_beyond_min_gap():
    """A ring with many devices must exceed MIN_RING_GAP to fit them all."""
    n = 20
    dm = _make_depth_map([n])
    radii = _compute_ring_radii(dm, _make_nodes(dm))
    assert radii[1] > MIN_RING_GAP, "crowded ring must grow beyond the floor"


def test_compute_ring_radii_sparse_outer_hops_respect_min_gap():
    """Outer hops with 1 device each must still be MIN_RING_GAP apart."""
    dm = _make_depth_map([8, 1, 1, 1])
    radii = _compute_ring_radii(dm, _make_nodes(dm))
    for h in range(2, 5):
        gap = radii[h] - radii[h - 1]
        assert gap >= MIN_RING_GAP, f"hop {h} gap {gap:.1f}px < MIN_RING_GAP {MIN_RING_GAP}"


MOCK_ZHA_DEVICES: list[dict] = [
    {"ieee": "00:00:00:00:00:00:00:00", "device_type": "Coordinator", "name": "Coordinator"},
    {"ieee": "00:00:00:00:00:00:00:01", "device_type": "Router", "name": "Router 1"},
    {"ieee": "00:00:00:00:00:00:00:02", "device_type": "Router", "name": "Router 2"},
    {"ieee": "00:00:00:00:00:00:00:03", "device_type": "EndDevice", "name": "Sensor"},
]


# ---------------------------------------------------------------------------
# _build_flat_zha_topology unit tests
# ---------------------------------------------------------------------------


def test_build_flat_zha_topology_node_count():
    from zigporter.commands.network_map import _build_flat_zha_topology  # noqa: PLC0415

    nodes, _ = _build_flat_zha_topology(MOCK_ZHA_DEVICES)
    assert len(nodes) == 4  # coordinator + 2 routers + 1 end device


def test_build_flat_zha_topology_coordinator_present():
    from zigporter.commands.network_map import _build_flat_zha_topology  # noqa: PLC0415

    nodes, _ = _build_flat_zha_topology(MOCK_ZHA_DEVICES)
    coord = nodes["0000000000000000"]
    assert coord["type"] == "Coordinator"


def test_build_flat_zha_topology_all_devices_at_depth1():
    """With flat topology, the routing tree places every device at depth 1."""
    from zigporter.commands.network_map import _build_flat_zha_topology  # noqa: PLC0415

    nodes, links = _build_flat_zha_topology(MOCK_ZHA_DEVICES)
    _, _, depth_map = _build_routing_tree(nodes, links)
    for ieee, depth in depth_map.items():
        if nodes[ieee]["type"] != "Coordinator":
            assert depth == 1, f"expected depth 1 for {ieee}, got {depth}"


def test_build_flat_zha_topology_lqi_from_device_field():
    """LQI in links comes from each device's lqi field."""
    from zigporter.commands.network_map import _build_flat_zha_topology  # noqa: PLC0415

    devices = [
        {"ieee": "00:00:00:00:00:00:00:00", "device_type": "Coordinator", "name": "Coord"},
        {
            "ieee": "00:00:00:00:00:00:00:01",
            "device_type": "EndDevice",
            "name": "Sensor",
            "lqi": 142,
        },
    ]
    _, links = _build_flat_zha_topology(devices)
    assert len(links) == 1
    assert links[0]["lqi"] == 142


def test_build_flat_zha_topology_missing_lqi_defaults_to_zero():
    from zigporter.commands.network_map import _build_flat_zha_topology  # noqa: PLC0415

    devices = [
        {"ieee": "00:00:00:00:00:00:00:00", "device_type": "Coordinator", "name": "Coord"},
        {"ieee": "00:00:00:00:00:00:00:01", "device_type": "EndDevice", "name": "Sensor"},
    ]
    _, links = _build_flat_zha_topology(devices)
    assert links[0]["lqi"] == 0


def test_build_flat_zha_topology_empty_devices():
    from zigporter.commands.network_map import _build_flat_zha_topology  # noqa: PLC0415

    nodes, links = _build_flat_zha_topology([])
    assert nodes == {}
    assert links == []


def test_build_flat_zha_topology_no_coordinator_skips_links():
    """Without a coordinator entry, no links are created (nothing to link to)."""
    from zigporter.commands.network_map import _build_flat_zha_topology  # noqa: PLC0415

    devices = [
        {"ieee": "00:00:00:00:00:00:00:01", "device_type": "Router", "name": "Router", "lqi": 200},
    ]
    nodes, links = _build_flat_zha_topology(devices)
    assert len(nodes) == 1
    assert links == []


# ---------------------------------------------------------------------------
# _build_zha_topology_from_devices unit tests
# ---------------------------------------------------------------------------

# ZHA devices as returned by zha/devices — lqi in neighbors is a STRING
MOCK_ZHA_DEVICES_WITH_NEIGHBORS: list[dict] = [
    {
        "ieee": "00:00:00:00:00:00:00:00",
        "device_type": "Coordinator",
        "name": "Coordinator",
        "neighbors": [
            {"ieee": "00:00:00:00:00:00:00:01", "lqi": "172", "relationship": "Neighbor"},
        ],
    },
    {
        "ieee": "00:00:00:00:00:00:00:01",
        "device_type": "Router",
        "name": "Range Extender",
        "neighbors": [
            {"ieee": "00:00:00:00:00:00:00:00", "lqi": "170", "relationship": "Neighbor"},
            # Router claims sensor as its Child — actual parent relationship
            {"ieee": "00:00:00:00:00:00:00:02", "lqi": "180", "relationship": "Child"},
        ],
    },
    {
        "ieee": "00:00:00:00:00:00:00:02",
        "device_type": "EndDevice",
        "name": "Temp Sensor",
        "neighbors": [
            # Sensor reports router as Parent
            {"ieee": "00:00:00:00:00:00:00:01", "lqi": "178", "relationship": "Parent"},
        ],
    },
]


def test_build_zha_topology_from_devices_node_count():

    nodes, _ = _build_zha_topology_from_devices(MOCK_ZHA_DEVICES_WITH_NEIGHBORS)
    assert len(nodes) == 3


def test_build_zha_topology_from_devices_lqi_string_converted():
    """Neighbor LQI strings are converted to int."""

    _, links = _build_zha_topology_from_devices(MOCK_ZHA_DEVICES_WITH_NEIGHBORS)
    for lk in links:
        assert isinstance(lk["lqi"], int), f"expected int LQI, got {type(lk['lqi'])}"


def test_build_zha_topology_from_devices_relationship_forwarded():
    """relationship field is forwarded to links."""

    _, links = _build_zha_topology_from_devices(MOCK_ZHA_DEVICES_WITH_NEIGHBORS)
    # Router's neighbor list entry for sensor has relationship="Child"
    # → link {source: sensor, target: router, relationship: "Child"}
    router_ieee = "0000000000000001"
    sensor_ieee = "0000000000000002"
    child_links = [
        lk
        for lk in links
        if lk["source"]["ieeeAddr"] == sensor_ieee and lk["target"]["ieeeAddr"] == router_ieee
    ]
    assert len(child_links) == 1
    assert child_links[0]["relationship"] == "Child"


def test_build_zha_topology_from_devices_sensor_at_depth2():
    """Sensor should be placed at depth 2 via the router, not depth 1 via coordinator.

    This is the core bug fix: coordinator also hears the sensor (Neighbor relationship),
    but the router has relationship='Child' for the sensor, making it the authoritative
    parent.  Even when coordinator is processed first in the BFS, re-placement ensures
    the sensor moves to depth 2 once the router joins the tree.
    """

    # Add a coordinator→sensor "Neighbor" link so the bug scenario is triggered:
    # coordinator overhears the sensor on the air even though sensor joined the router.
    devices = [
        {
            "ieee": "00:00:00:00:00:00:00:00",
            "device_type": "Coordinator",
            "name": "Coordinator",
            "neighbors": [
                {"ieee": "00:00:00:00:00:00:00:01", "lqi": "172", "relationship": "Neighbor"},
                # Coordinator overhears the sensor — NOT its direct child
                {"ieee": "00:00:00:00:00:00:00:02", "lqi": "176", "relationship": "Neighbor"},
            ],
        },
        {
            "ieee": "00:00:00:00:00:00:00:01",
            "device_type": "Router",
            "name": "Range Extender",
            "neighbors": [
                {"ieee": "00:00:00:00:00:00:00:00", "lqi": "170", "relationship": "Neighbor"},
                {"ieee": "00:00:00:00:00:00:00:02", "lqi": "180", "relationship": "Child"},
            ],
        },
        {
            "ieee": "00:00:00:00:00:00:00:02",
            "device_type": "EndDevice",
            "name": "Temp Sensor",
            "neighbors": [
                {"ieee": "00:00:00:00:00:00:00:01", "lqi": "178", "relationship": "Parent"},
            ],
        },
    ]
    nodes, links = _build_zha_topology_from_devices(devices)
    _, _, depth_map = _build_routing_tree(nodes, links)
    assert depth_map["0000000000000001"] == 1, "router should be at hop 1"
    assert depth_map["0000000000000002"] == 2, (
        "sensor should be at hop 2 via router, not hop 1 via coordinator"
    )


def test_build_routing_tree_replaces_node_when_better_parent_found():
    """Re-placement: a node initially placed at depth 1 via coordinator should be
    promoted to depth 2 via a router when the router joins the tree with a better
    (Child-relationship) link, even though the coordinator link has higher raw LQI."""
    coord = "0000000000000000"
    router = "0000000000000001"
    sensor = "0000000000000002"
    nodes = {
        coord: {"ieeeAddr": coord, "friendlyName": "Coordinator", "type": "Coordinator"},
        router: {"ieeeAddr": router, "friendlyName": "Range Extender", "type": "Router"},
        sensor: {"ieeeAddr": sensor, "friendlyName": "Temp Sensor", "type": "EndDevice"},
    }
    links = [
        # Coordinator overhears sensor with HIGH LQI — but sensor is not its child
        {
            "source": {"ieeeAddr": sensor},
            "target": {"ieeeAddr": coord},
            "lqi": 200,
            "relationship": "Neighbor",
        },
        # Coordinator ↔ router
        {
            "source": {"ieeeAddr": router},
            "target": {"ieeeAddr": coord},
            "lqi": 172,
            "relationship": "Neighbor",
        },
        {
            "source": {"ieeeAddr": coord},
            "target": {"ieeeAddr": router},
            "lqi": 170,
            "relationship": "Neighbor",
        },
        # Router claims sensor as Child — authoritative parent link (lower raw LQI)
        {
            "source": {"ieeeAddr": sensor},
            "target": {"ieeeAddr": router},
            "lqi": 180,
            "relationship": "Child",
        },
        {
            "source": {"ieeeAddr": router},
            "target": {"ieeeAddr": sensor},
            "lqi": 178,
            "relationship": "Parent",
        },
    ]
    _, _, depth_map = _build_routing_tree(nodes, links)
    assert depth_map[router] == 1
    assert depth_map[sensor] == 2, (
        "sensor should be at depth 2 via router (Child relationship) "
        "even though coordinator has higher raw LQI to sensor"
    )


# ---------------------------------------------------------------------------
# ZHA backend integration test via run_network_map
# ---------------------------------------------------------------------------


async def _run_zha_with_capture(devices: list) -> str:
    """Run ZHA network-map and return captured console output."""
    mock_ha_client = AsyncMock()
    mock_ha_client.get_zha_devices = AsyncMock(return_value=devices)

    buf = io.StringIO()
    cap_console = Console(file=buf, highlight=False, markup=True, force_terminal=False, width=200)

    with (
        patch("zigporter.commands.network_map.HAClient", return_value=mock_ha_client),
        patch("zigporter.commands.network_map.console", cap_console),
        patch("zigporter.commands.network_map.Progress", return_value=_make_mock_progress()),
    ):
        await run_network_map(
            HA_URL,
            TOKEN,
            Z2M_URL,
            verify_ssl=False,
            backend="zha",
        )

    return buf.getvalue()


async def test_zha_backend_shows_coordinator():
    output = await _run_zha_with_capture(MOCK_ZHA_DEVICES_WITH_NEIGHBORS)
    assert "Coordinator" in output


async def test_zha_backend_shows_device_name():
    """user_given_name is shown in output."""
    devices = [
        {
            "ieee": "00:00:00:00:00:00:00:00",
            "device_type": "Coordinator",
            "name": "Coordinator",
            "user_given_name": None,
            "neighbors": [
                {"ieee": "00:00:00:00:00:00:00:01", "lqi": "200", "relationship": "Neighbor"}
            ],
        },
        {
            "ieee": "00:00:00:00:00:00:00:01",
            "device_type": "Router",
            "name": "Router 1",
            "user_given_name": "Living Room Switch",
            "neighbors": [
                {"ieee": "00:00:00:00:00:00:00:00", "lqi": "195", "relationship": "Neighbor"}
            ],
        },
    ]
    output = await _run_zha_with_capture(devices)
    assert "Living Room Switch" in output


async def test_zha_backend_summary_label():
    output = await _run_zha_with_capture(MOCK_ZHA_DEVICES_WITH_NEIGHBORS)
    assert "ZHA" in output


async def test_zha_backend_uses_neighbor_data_for_routing():
    """Devices with neighbor data are placed at correct hop depth via the routing tree."""
    output = await _run_zha_with_capture(MOCK_ZHA_DEVICES_WITH_NEIGHBORS)
    # MOCK_ZHA_DEVICES_WITH_NEIGHBORS has Temp Sensor as a child of Range Extender (hop 2).
    # Coordinator and Range Extender are at hop 1.
    assert "Coordinator" in output
    assert "Range Extender" in output
    assert "Temp Sensor" in output


async def test_zha_backend_flat_fallback_when_no_neighbor_data():
    """When devices have no neighbour data, flat view is shown."""
    mock_ha_client = AsyncMock()
    mock_ha_client.get_zha_devices = AsyncMock(return_value=MOCK_ZHA_DEVICES)

    buf = io.StringIO()
    cap_console = Console(file=buf, highlight=False, markup=True, force_terminal=False, width=200)

    with (
        patch("zigporter.commands.network_map.HAClient", return_value=mock_ha_client),
        patch("zigporter.commands.network_map.console", cap_console),
        patch("zigporter.commands.network_map.Progress", return_value=_make_mock_progress()),
    ):
        await run_network_map(HA_URL, TOKEN, Z2M_URL, verify_ssl=False, backend="zha")

    output = buf.getvalue()
    assert "flat view" in output
    # MOCK_ZHA_DEVICES uses "name" field (no user_given_name), so names come from there
    assert "Router 1" in output


async def test_auto_backend_picks_zha_when_z2m_url_empty():
    """With no Z2M_URL and ZHA available, auto should resolve to ZHA."""
    mock_ha_client = AsyncMock()
    mock_ha_client.get_zha_devices = AsyncMock(return_value=MOCK_ZHA_DEVICES_WITH_NEIGHBORS)

    buf = io.StringIO()
    cap_console = Console(file=buf, highlight=False, markup=True, force_terminal=False, width=200)

    with (
        patch("zigporter.commands.network_map.HAClient", return_value=mock_ha_client),
        patch("zigporter.commands.network_map.console", cap_console),
        patch("zigporter.commands.network_map.Progress", return_value=_make_mock_progress()),
    ):
        await run_network_map(
            HA_URL,
            TOKEN,
            z2m_url="",  # no Z2M configured
            verify_ssl=False,
            backend="auto",
        )

    assert "ZHA" in buf.getvalue()


async def test_auto_backend_error_when_neither_available():
    """With no Z2M_URL and ZHA failing, auto should print an error and return."""
    mock_ha_client = AsyncMock()
    mock_ha_client.get_zha_devices = AsyncMock(side_effect=RuntimeError("ZHA not installed"))

    buf = io.StringIO()
    cap_console = Console(file=buf, highlight=False, markup=True, force_terminal=False, width=200)

    with (
        patch("zigporter.commands.network_map.HAClient", return_value=mock_ha_client),
        patch("zigporter.commands.network_map.console", cap_console),
    ):
        await run_network_map(HA_URL, TOKEN, z2m_url="", verify_ssl=False, backend="auto")

    assert "Neither" in buf.getvalue() or "not available" in buf.getvalue()


async def test_auto_backend_prompts_when_both_available_selects_z2m():
    """When both Z2M and ZHA are available, user is prompted; selecting z2m shows Z2M output."""
    mock_ha_client = AsyncMock()
    mock_ha_client.get_zha_devices = AsyncMock(return_value=MOCK_ZHA_DEVICES_WITH_NEIGHBORS)

    mock_z2m_client = AsyncMock()
    mock_z2m_client.get_network_map = AsyncMock(return_value=MOCK_NETWORK_MAP_RESPONSE)

    buf = io.StringIO()
    cap_console = Console(file=buf, highlight=False, markup=True, force_terminal=False, width=200)

    mock_select = AsyncMock(return_value="z2m")

    with (
        patch("zigporter.commands.network_map.HAClient", return_value=mock_ha_client),
        patch("zigporter.commands.network_map.Z2MClient", return_value=mock_z2m_client),
        patch("zigporter.commands.network_map.console", cap_console),
        patch("zigporter.commands.network_map.Progress", return_value=_make_mock_progress()),
        patch(
            "zigporter.commands.network_map.questionary.select",
            return_value=type("S", (), {"ask_async": mock_select})(),
        ),
    ):
        await run_network_map(
            HA_URL,
            TOKEN,
            Z2M_URL,
            verify_ssl=False,
            backend="auto",
        )

    output = buf.getvalue()
    assert "Z2M" in output


async def test_auto_backend_prompts_when_both_available_selects_zha():
    """When both Z2M and ZHA are available, user is prompted; selecting zha shows ZHA output."""
    mock_ha_client = AsyncMock()
    mock_ha_client.get_zha_devices = AsyncMock(return_value=MOCK_ZHA_DEVICES_WITH_NEIGHBORS)

    buf = io.StringIO()
    cap_console = Console(file=buf, highlight=False, markup=True, force_terminal=False, width=200)

    mock_select = AsyncMock(return_value="zha")

    with (
        patch("zigporter.commands.network_map.HAClient", return_value=mock_ha_client),
        patch("zigporter.commands.network_map.console", cap_console),
        patch("zigporter.commands.network_map.Progress", return_value=_make_mock_progress()),
        patch(
            "zigporter.commands.network_map.questionary.select",
            return_value=type("S", (), {"ask_async": mock_select})(),
        ),
    ):
        await run_network_map(
            HA_URL,
            TOKEN,
            Z2M_URL,
            verify_ssl=False,
            backend="auto",
        )

    output = buf.getvalue()
    assert "ZHA" in output
