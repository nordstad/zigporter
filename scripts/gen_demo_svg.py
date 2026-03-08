"""Generate docs/assets/network-map-demo.svg from hardcoded mock data.

Run with:
    uv run python scripts/gen_demo_svg.py

Topology (30 devices, 4 hop rings) is designed to look like a realistic home mesh:
  - Good links  (LQI >= 80):   main routers and most end devices near their parent
  - Warn glow   (LQI < 80):    distant/battery devices and the SMLIGHT branch
  - Crit glow   (LQI < 30):    Porch Light (outside, far from coordinator)
  - 4 hop rings reflecting realistic home-network depth
"""

from pathlib import Path

from zigporter.commands.network_map_svg import render_svg

# ── IEEEs ─────────────────────────────────────────────────────────────────────

COORD = "0x0000000000000000"

# Hop 1 — direct coordinator children
LIVING_PLUG = "0xaabbccddeeff0001"
KITCHEN_PLUG = "0xaabbccddeeff0002"
HALLWAY_PLUG = "0xaabbccddeeff0003"
BEDROOM_PLUG = "0xaabbccddeeff0004"
OFFICE_PLUG = "0xaabbccddeeff0005"
BATHROOM_SENSOR = "0xaabbccddeeff0006"
FRONT_DOOR = "0xaabbccddeeff0007"
PORCH_LIGHT = "0xaabbccddeeff0008"

# Hop 2 — children of Living Room Plug
TV_PLUG = "0xaabbccddeeff0010"
LIVING_MOTION = "0xaabbccddeeff0011"
SMART_BULB = "0xaabbccddeeff0012"

# Hop 2 — children of Kitchen Plug
DISHWASHER_PLUG = "0xaabbccddeeff0020"
WINDOW_SENSOR = "0xaabbccddeeff0021"
FRIDGE_SENSOR = "0xaabbccddeeff0022"

# Hop 2 — children of Hallway Plug
SMLIGHT = "0xaabbccddeeff0030"
STAIR_LIGHT = "0xaabbccddeeff0031"

# Hop 2 — children of Bedroom Plug
BEDSIDE_PLUG = "0xaabbccddeeff0040"
BEDROOM_SENSOR = "0xaabbccddeeff0041"

# Hop 2 — children of Office Plug
DESK_PLUG = "0xaabbccddeeff0050"
SONOFF = "0xaabbccddeeff0051"

# Hop 3 — children of TV Plug
GAMING_PLUG = "0xaabbccddeeff0060"

# Hop 3 — children of SMLIGHT Repeater
GARAGE_PLUG = "0xaabbccddeeff0070"
ATTIC_SENSOR = "0xaabbccddeeff0071"
GARDEN_LIGHT = "0xaabbccddeeff0072"

# Hop 3 — children of Bedside Plug
AQARA_TEMP = "0xaabbccddeeff0080"
NIGHTLIGHT = "0xaabbccddeeff0081"

# Hop 4 — children of Garage Plug
GARDEN_SENSOR = "0xaabbccddeeff0090"
SHED_PLUG = "0xaabbccddeeff0091"

# ── Topology ──────────────────────────────────────────────────────────────────
#
# Coordinator                                              hop 0
# ├── Living Room Plug   router  hop 1  LQI 198
# │    ├── TV Plug            router  hop 2  LQI 185
# │    │    └── Gaming Plug        end  hop 3  LQI 141
# │    ├── Living Motion      end  hop 2  LQI 163
# │    └── Smart Bulb         end  hop 2  LQI 145
# ├── Kitchen Plug       router  hop 1  LQI 172
# │    ├── Dishwasher Plug    end  hop 2  LQI 92
# │    ├── Window Sensor      end  hop 2  LQI 71   WEAK
# │    └── Fridge Sensor      end  hop 2  LQI 85
# ├── Hallway Plug       router  hop 1  LQI 155
# │    ├── SMLIGHT Repeater   router  hop 2  LQI 76  WEAK
# │    │    ├── Garage Plug        router  hop 3  LQI 105
# │    │    │    ├── Garden Sensor      end  hop 4  LQI 84
# │    │    │    └── Shed Plug          end  hop 4  LQI 46  WEAK
# │    │    ├── Attic Sensor       end  hop 3  LQI 62  WEAK
# │    │    └── Garden Light       end  hop 3  LQI 55  WEAK
# │    └── Stair Light        end  hop 2  LQI 75  WEAK
# ├── Bedroom Plug       router  hop 1  LQI 143
# │    ├── Bedside Plug       router  hop 2  LQI 162
# │    │    ├── Aqara Temp         end  hop 3  LQI 118
# │    │    └── Nightlight         end  hop 3  LQI 103
# │    └── Bedroom Sensor     end  hop 2  LQI 77  WEAK
# ├── Office Plug        router  hop 1  LQI 138
# │    ├── Desk Plug          end  hop 2  LQI 131
# │    └── Sonoff Switch      end  hop 2  LQI 68  WEAK
# ├── Bathroom Sensor    end     hop 1  LQI 112
# ├── Front Door Sensor  end     hop 1  LQI 98
# └── Porch Light        end     hop 1  LQI 22   CRITICAL

nodes = {
    COORD: {"friendlyName": "Coordinator", "type": "Coordinator"},
    # hop 1
    LIVING_PLUG: {"friendlyName": "Living Room Plug", "type": "Router"},
    KITCHEN_PLUG: {"friendlyName": "Kitchen Plug", "type": "Router"},
    HALLWAY_PLUG: {"friendlyName": "Hallway Plug", "type": "Router"},
    BEDROOM_PLUG: {"friendlyName": "Bedroom Plug", "type": "Router"},
    OFFICE_PLUG: {"friendlyName": "Office Plug", "type": "Router"},
    BATHROOM_SENSOR: {"friendlyName": "Bathroom Sensor", "type": "EndDevice"},
    FRONT_DOOR: {"friendlyName": "Front Door Sensor", "type": "EndDevice"},
    PORCH_LIGHT: {"friendlyName": "Porch Light", "type": "EndDevice"},
    # hop 2 — Living Room Plug children
    TV_PLUG: {"friendlyName": "TV Plug", "type": "Router"},
    LIVING_MOTION: {"friendlyName": "Living Motion", "type": "EndDevice"},
    SMART_BULB: {"friendlyName": "Smart Bulb", "type": "EndDevice"},
    # hop 2 — Kitchen Plug children
    DISHWASHER_PLUG: {"friendlyName": "Dishwasher Plug", "type": "EndDevice"},
    WINDOW_SENSOR: {"friendlyName": "Window Sensor", "type": "EndDevice"},
    FRIDGE_SENSOR: {"friendlyName": "Fridge Sensor", "type": "EndDevice"},
    # hop 2 — Hallway Plug children
    SMLIGHT: {"friendlyName": "SMLIGHT Repeater", "type": "Router"},
    STAIR_LIGHT: {"friendlyName": "Stair Light", "type": "EndDevice"},
    # hop 2 — Bedroom Plug children
    BEDSIDE_PLUG: {"friendlyName": "Bedside Plug", "type": "Router"},
    BEDROOM_SENSOR: {"friendlyName": "Bedroom Sensor", "type": "EndDevice"},
    # hop 2 — Office Plug children
    DESK_PLUG: {"friendlyName": "Desk Plug", "type": "EndDevice"},
    SONOFF: {"friendlyName": "Sonoff Switch", "type": "EndDevice"},
    # hop 3
    GAMING_PLUG: {"friendlyName": "Gaming Plug", "type": "EndDevice"},
    GARAGE_PLUG: {"friendlyName": "Garage Plug", "type": "Router"},
    ATTIC_SENSOR: {"friendlyName": "Attic Sensor", "type": "EndDevice"},
    GARDEN_LIGHT: {"friendlyName": "Garden Light", "type": "EndDevice"},
    AQARA_TEMP: {"friendlyName": "Aqara Temp", "type": "EndDevice"},
    NIGHTLIGHT: {"friendlyName": "Nightlight", "type": "EndDevice"},
    # hop 4
    GARDEN_SENSOR: {"friendlyName": "Garden Sensor", "type": "EndDevice"},
    SHED_PLUG: {"friendlyName": "Shed Plug", "type": "EndDevice"},
}

parent_map: dict[str, str | None] = {
    COORD: None,
    # hop 1
    LIVING_PLUG: COORD,
    KITCHEN_PLUG: COORD,
    HALLWAY_PLUG: COORD,
    BEDROOM_PLUG: COORD,
    OFFICE_PLUG: COORD,
    BATHROOM_SENSOR: COORD,
    FRONT_DOOR: COORD,
    PORCH_LIGHT: COORD,
    # hop 2
    TV_PLUG: LIVING_PLUG,
    LIVING_MOTION: LIVING_PLUG,
    SMART_BULB: LIVING_PLUG,
    DISHWASHER_PLUG: KITCHEN_PLUG,
    WINDOW_SENSOR: KITCHEN_PLUG,
    FRIDGE_SENSOR: KITCHEN_PLUG,
    SMLIGHT: HALLWAY_PLUG,
    STAIR_LIGHT: HALLWAY_PLUG,
    BEDSIDE_PLUG: BEDROOM_PLUG,
    BEDROOM_SENSOR: BEDROOM_PLUG,
    DESK_PLUG: OFFICE_PLUG,
    SONOFF: OFFICE_PLUG,
    # hop 3
    GAMING_PLUG: TV_PLUG,
    GARAGE_PLUG: SMLIGHT,
    ATTIC_SENSOR: SMLIGHT,
    GARDEN_LIGHT: SMLIGHT,
    AQARA_TEMP: BEDSIDE_PLUG,
    NIGHTLIGHT: BEDSIDE_PLUG,
    # hop 4
    GARDEN_SENSOR: GARAGE_PLUG,
    SHED_PLUG: GARAGE_PLUG,
}

lqi_map: dict[str, int] = {
    COORD: 255,
    # hop 1
    LIVING_PLUG: 198,
    KITCHEN_PLUG: 172,
    HALLWAY_PLUG: 155,
    BEDROOM_PLUG: 143,
    OFFICE_PLUG: 138,
    BATHROOM_SENSOR: 112,
    FRONT_DOOR: 98,
    PORCH_LIGHT: 22,  # CRITICAL — outside, far from coordinator
    # hop 2 — Living Room Plug branch
    TV_PLUG: 185,
    LIVING_MOTION: 163,
    SMART_BULB: 145,
    # hop 2 — Kitchen Plug branch
    DISHWASHER_PLUG: 92,
    WINDOW_SENSOR: 71,  # WEAK
    FRIDGE_SENSOR: 85,
    # hop 2 — Hallway Plug branch
    SMLIGHT: 76,  # WEAK
    STAIR_LIGHT: 75,  # WEAK
    # hop 2 — Bedroom Plug branch
    BEDSIDE_PLUG: 162,
    BEDROOM_SENSOR: 77,  # WEAK
    # hop 2 — Office Plug branch
    DESK_PLUG: 131,
    SONOFF: 68,  # WEAK
    # hop 3
    GAMING_PLUG: 141,
    GARAGE_PLUG: 105,
    ATTIC_SENSOR: 62,  # WEAK
    GARDEN_LIGHT: 55,  # WEAK
    AQARA_TEMP: 118,
    NIGHTLIGHT: 103,
    # hop 4
    GARDEN_SENSOR: 84,
    SHED_PLUG: 46,  # WEAK
}

depth_map: dict[str, int] = {
    COORD: 0,
    LIVING_PLUG: 1,
    KITCHEN_PLUG: 1,
    HALLWAY_PLUG: 1,
    BEDROOM_PLUG: 1,
    OFFICE_PLUG: 1,
    BATHROOM_SENSOR: 1,
    FRONT_DOOR: 1,
    PORCH_LIGHT: 1,
    TV_PLUG: 2,
    LIVING_MOTION: 2,
    SMART_BULB: 2,
    DISHWASHER_PLUG: 2,
    WINDOW_SENSOR: 2,
    FRIDGE_SENSOR: 2,
    SMLIGHT: 2,
    STAIR_LIGHT: 2,
    BEDSIDE_PLUG: 2,
    BEDROOM_SENSOR: 2,
    DESK_PLUG: 2,
    SONOFF: 2,
    GAMING_PLUG: 3,
    GARAGE_PLUG: 3,
    ATTIC_SENSOR: 3,
    GARDEN_LIGHT: 3,
    AQARA_TEMP: 3,
    NIGHTLIGHT: 3,
    GARDEN_SENSOR: 4,
    SHED_PLUG: 4,
}

children: dict[str, list[str]] = {
    COORD: [
        LIVING_PLUG,
        KITCHEN_PLUG,
        HALLWAY_PLUG,
        BEDROOM_PLUG,
        OFFICE_PLUG,
        BATHROOM_SENSOR,
        FRONT_DOOR,
        PORCH_LIGHT,
    ],
    LIVING_PLUG: [TV_PLUG, LIVING_MOTION, SMART_BULB],
    KITCHEN_PLUG: [DISHWASHER_PLUG, WINDOW_SENSOR, FRIDGE_SENSOR],
    HALLWAY_PLUG: [SMLIGHT, STAIR_LIGHT],
    BEDROOM_PLUG: [BEDSIDE_PLUG, BEDROOM_SENSOR],
    OFFICE_PLUG: [DESK_PLUG, SONOFF],
    BATHROOM_SENSOR: [],
    FRONT_DOOR: [],
    PORCH_LIGHT: [],
    TV_PLUG: [GAMING_PLUG],
    LIVING_MOTION: [],
    SMART_BULB: [],
    DISHWASHER_PLUG: [],
    WINDOW_SENSOR: [],
    FRIDGE_SENSOR: [],
    SMLIGHT: [GARAGE_PLUG, ATTIC_SENSOR, GARDEN_LIGHT],
    STAIR_LIGHT: [],
    BEDSIDE_PLUG: [AQARA_TEMP, NIGHTLIGHT],
    BEDROOM_SENSOR: [],
    DESK_PLUG: [],
    SONOFF: [],
    GAMING_PLUG: [],
    GARAGE_PLUG: [GARDEN_SENSOR, SHED_PLUG],
    ATTIC_SENSOR: [],
    GARDEN_LIGHT: [],
    AQARA_TEMP: [],
    NIGHTLIGHT: [],
    GARDEN_SENSOR: [],
    SHED_PLUG: [],
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
