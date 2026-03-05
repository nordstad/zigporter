"""Tests for the network-map command."""

import io
from unittest.mock import AsyncMock, patch

from rich.console import Console

from zigporter.commands.network_map import (
    _build_routing_tree,
    run_network_map,
)


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
    con = Console(file=buf, highlight=False, markup=True, force_terminal=False)
    return con, buf


async def _run_with_capture(
    output_format: str = "tree", warn_lqi: int = 80, critical_lqi: int = 30
):
    mock_client = AsyncMock()
    mock_client.get_network_map = AsyncMock(return_value=MOCK_NETWORK_MAP_RESPONSE)
    buf = io.StringIO()
    cap_console = Console(file=buf, highlight=False, markup=True, force_terminal=False)

    with (
        patch("zigporter.commands.network_map.Z2MClient", return_value=mock_client),
        patch("zigporter.commands.network_map.console", cap_console),
        patch("zigporter.commands.network_map.Progress") as mock_progress_cls,
    ):
        mock_progress = AsyncMock()
        mock_progress.__enter__ = lambda s: mock_progress
        mock_progress.__exit__ = lambda s, *a: False
        mock_progress.add_task = lambda *a, **kw: 0
        mock_progress.update = lambda *a, **kw: None
        mock_progress_cls.return_value = mock_progress

        await run_network_map(
            HA_URL,
            TOKEN,
            Z2M_URL,
            verify_ssl=False,
            output_format=output_format,
            warn_lqi=warn_lqi,
            critical_lqi=critical_lqi,
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
