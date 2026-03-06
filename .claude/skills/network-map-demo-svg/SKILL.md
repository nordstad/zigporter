---
name: network-map-demo-svg
description: >
  Regenerate docs/assets/network-map-demo.svg from the mock topology script.
  Use this whenever network_map_svg.py was changed (visual constants, layout
  algorithm, colours, rendering logic), the mock topology in gen_demo_svg.py
  was updated, or the docs SVG looks stale or wrong. Also triggers on phrases
  like "update the demo SVG", "regenerate the network map image", "refresh
  docs/assets/network-map-demo.svg", or "the SVG in the docs is outdated".
---

## Steps

1. Run the generator script:
   ```bash
   uv run python scripts/gen_demo_svg.py
   ```

2. Lint the script to catch any issues:
   ```bash
   uv run ruff check scripts/gen_demo_svg.py
   ```

3. Verify the SVG at `docs/assets/network-map-demo.svg` looks correct.
   The current mock topology produces 12 nodes across 3 hop rings. Quick
   grep sanity checks (each should return at least 1 match):
   ```bash
   grep -c "Hop 3"     docs/assets/network-map-demo.svg   # 3 rings present
   grep -c "glow-warn" docs/assets/network-map-demo.svg   # warn glows on Hue Motion, Sonoff ZBMINI, Hall Outlet
   grep -c "glow-crit" docs/assets/network-map-demo.svg   # crit glow on Smoke Detector
   ```
   Also confirm path-min badges appear for the 4 devices behind a weak parent
   (Aqara Temp, Aqara Vibration, Bedroom Sensor, Living Motion).

4. Stage and commit:
   ```bash
   git add docs/assets/network-map-demo.svg
   git commit -m "docs(network-map): regenerate demo SVG"
   ```
