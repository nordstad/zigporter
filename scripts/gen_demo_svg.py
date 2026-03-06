"""Generate docs/assets/network-map-demo.svg from hardcoded mock data.

Run with:
    uv run python scripts/gen_demo_svg.py

Topology is intentionally designed to exercise every visual feature:
  - Warn glow  (LQI < 80):  Sonoff ZBMINI, Hue Motion, Hall Outlet
  - Crit glow  (LQI < 30):  Smoke Detector
  - Path-min badge (upstream bottleneck):
      Aqara Temp & Aqara Vibration  → parent Sonoff ZBMINI is weak (65)
      Bedroom Sensor & Living Motion → grandparent Hall Outlet is weak (70)
  - 3 hop rings for visual depth
"""

from pathlib import Path

from zigporter.commands.network_map_svg import render_svg

# ── IEEEs ─────────────────────────────────────────────────────────────────────

COORD = "0x0000000000000000"
IKEA = "0xaabbccddeeff0001"
AQARA_DOOR = "0xaabbccddeeff0002"
HUE_MOTION = "0xaabbccddeeff0003"
SONOFF = "0xaabbccddeeff0004"
AQARA_TEMP = "0xaabbccddeeff0005"
AQARA_VIB = "0xaabbccddeeff0006"
TRADFRI = "0xaabbccddeeff0007"
SMOKE = "0xaabbccddeeff0008"
HALL_OUTLET = "0xaabbccddeeff0009"
BEDROOM = "0xaabbccddeeff000a"
LIVING_MOTION = "0xaabbccddeeff000b"

# ── Topology ──────────────────────────────────────────────────────────────────
#
# Coordinator                              hop 0
# ├── IKEA Outlet        router  hop 1  LQI 212
# │    ├── Aqara Door Sensor  end hop 2  LQI 185  (no badge — path_min = own)
# │    └── Hue Motion         end hop 2  LQI  58  WEAK
# ├── Sonoff ZBMINI      router  hop 1  LQI  65   WEAK
# │    ├── Aqara Temp         end hop 2  LQI 176  badge "65" (path_min=65 < own=176, 65<80)
# │    └── Aqara Vibration    end hop 2  LQI 142  badge "65"
# ├── TRADFRI Bulb       router  hop 1  LQI 155
# │    └── Hall Outlet         router  hop 2  LQI  70  WEAK
# │         ├── Bedroom Sensor   end  hop 3  LQI 195  badge "70" (path_min=70<195, 70<80)
# │         └── Living Motion    end  hop 3  LQI 210  badge "70"
# └── Smoke Detector     end     hop 1  LQI  22   CRITICAL

nodes = {
    COORD: {"friendlyName": "Coordinator", "type": "Coordinator"},
    IKEA: {"friendlyName": "IKEA Outlet", "type": "Router"},
    AQARA_DOOR: {"friendlyName": "Aqara Door Sensor", "type": "EndDevice"},
    HUE_MOTION: {"friendlyName": "Hue Motion", "type": "EndDevice"},
    SONOFF: {"friendlyName": "Sonoff ZBMINI", "type": "Router"},
    AQARA_TEMP: {"friendlyName": "Aqara Temp", "type": "EndDevice"},
    AQARA_VIB: {"friendlyName": "Aqara Vibration", "type": "EndDevice"},
    TRADFRI: {"friendlyName": "TRADFRI Bulb", "type": "Router"},
    SMOKE: {"friendlyName": "Smoke Detector", "type": "EndDevice"},
    HALL_OUTLET: {"friendlyName": "Hall Outlet", "type": "Router"},
    BEDROOM: {"friendlyName": "Bedroom Sensor", "type": "EndDevice"},
    LIVING_MOTION: {"friendlyName": "Living Motion", "type": "EndDevice"},
}

parent_map: dict[str, str | None] = {
    COORD: None,
    IKEA: COORD,
    AQARA_DOOR: IKEA,
    HUE_MOTION: IKEA,
    SONOFF: COORD,
    AQARA_TEMP: SONOFF,
    AQARA_VIB: SONOFF,
    TRADFRI: COORD,
    SMOKE: COORD,
    HALL_OUTLET: TRADFRI,
    BEDROOM: HALL_OUTLET,
    LIVING_MOTION: HALL_OUTLET,
}

lqi_map: dict[str, int] = {
    COORD: 255,
    IKEA: 212,
    AQARA_DOOR: 185,
    HUE_MOTION: 58,  # WEAK  (58 < warn=80)
    SONOFF: 65,  # WEAK  (65 < warn=80) → creates badges on its children
    AQARA_TEMP: 176,
    AQARA_VIB: 142,
    TRADFRI: 155,
    SMOKE: 22,  # CRITICAL  (22 < crit=30)
    HALL_OUTLET: 70,  # WEAK  (70 < warn=80) → creates badges on its children
    BEDROOM: 195,
    LIVING_MOTION: 210,
}

depth_map: dict[str, int] = {
    COORD: 0,
    IKEA: 1,
    AQARA_DOOR: 2,
    HUE_MOTION: 2,
    SONOFF: 1,
    AQARA_TEMP: 2,
    AQARA_VIB: 2,
    TRADFRI: 1,
    SMOKE: 1,
    HALL_OUTLET: 2,
    BEDROOM: 3,
    LIVING_MOTION: 3,
}

children: dict[str, list[str]] = {
    COORD: [IKEA, SONOFF, TRADFRI, SMOKE],
    IKEA: [AQARA_DOOR, HUE_MOTION],
    SONOFF: [AQARA_TEMP, AQARA_VIB],
    TRADFRI: [HALL_OUTLET],
    SMOKE: [],
    AQARA_DOOR: [],
    HUE_MOTION: [],
    AQARA_TEMP: [],
    AQARA_VIB: [],
    HALL_OUTLET: [BEDROOM, LIVING_MOTION],
    BEDROOM: [],
    LIVING_MOTION: [],
}

output_path = Path("docs/assets/network-map-demo.svg")
output_path.parent.mkdir(parents=True, exist_ok=True)

render_svg(
    nodes=nodes,
    parent_map=parent_map,
    lqi_map=lqi_map,
    depth_map=depth_map,
    children=children,
    output_path=output_path,
    warn_lqi=80,
    critical_lqi=30,
)

print(f"SVG written to {output_path}")
