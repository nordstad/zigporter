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
   The current mock topology produces 30 nodes across 4 hop rings. Quick
   grep sanity checks (each should return at least 1 match):
   ```bash
   grep -c "Hop 4"     docs/assets/network-map-demo.svg   # 4 rings present
   grep -c "glow-warn" docs/assets/network-map-demo.svg   # warn glows on Window Sensor, SMLIGHT Repeater, Stair Light, Bedroom Sensor, Sonoff Switch, Attic Sensor, Garden Light, Shed Plug
   grep -c "glow-crit" docs/assets/network-map-demo.svg   # crit glow on Porch Light
   ```
   Also confirm Aqara Temp and Nightlight (hop 3 behind Bedside Plug) appear in ring 3,
   and Garden Sensor / Shed Plug appear in ring 4.

4. Stage, commit, and push:
   ```bash
   git add docs/assets/network-map-demo.svg
   git commit -m "docs(network-map): regenerate demo SVG"
   git push origin main
   ```
