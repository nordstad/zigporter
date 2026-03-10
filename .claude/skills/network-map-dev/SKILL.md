# network-map-dev

Context skill for working on `src/zigporter/commands/network_map_svg.py`.
Load this before any SVG layout changes.

## Layout pipeline (order matters)

1. `_compute_ring_radii` — ring boundary radii from device counts per hop
2. `_subtree_weights` — angular weight per subtree (`max(ceil(sqrt(leaves)), depth)`)
3. `_assign_angles` — recursive sector subdivision, children inside parent's arc
4. Position computation — `(prev_r + curr_r) / 2` midpoint placement on ring
5. `_resolve_collisions` — Gauss-Seidel angular nudge within each ring
6. `_compute_layout` — orchestrates steps 1-5, returns `LayoutResult`
7. `render_svg` — pure SVG drawing using `LayoutResult` + `_draw_node` per device

## Traps — DO NOT re-attempt

- **`_normalize_ring_angles`** — deleted in commit `97e41bd`. Evenly redistributed nodes
  around 360-degree of each ring, destroying subtree locality and creating long cross-diagram
  edges. Any variant that spreads nodes to fill the ring will regress.
- **Bidirectional BFS for `min_depth`** — Z2M reports ALL RF-audible neighbors. The
  coordinator weakly hears depth-5 devices, so bidirectional BFS collapses everything
  to depth 1. Use the greedy highest-LQI parent from `_build_routing_tree` instead.
- **360-degree redistribution** in any form — if a ring looks clustered on one side,
  that is correct (connected devices are visually close). A rotation-only pass is
  acceptable; redistribution is not.

## Constant relationships

| Constant | Value | Constraint |
|---|---|---|
| `MIN_RING_GAP` | 200 | Must be > `NODE_R_ROUTER + LABEL_OFFSET + LABEL_ARC` (approx 196px) |
| `LABEL_ARC` | `MAX_LABEL_LEN * 6 + 10` = 142 | Used in `_compute_ring_radii`, `_assign_angles`, `_resolve_collisions` |
| `COLLISION_GAP` | 100 | Must clear the 34px label-offset zone |
| `ANGULAR_PADDING` | 50 | Added to `max(node_diameter, LABEL_ARC)` for arc_per_device |

If you change `MAX_LABEL_LEN`, `LABEL_ARC` updates automatically. If you change
`LABEL_ARC`'s formula, grep for all three usage sites.

## LQI semantics

**Two distinct LQI values per device:**

- **Routing path LQI** (`lqi_map`): `min(parent->device, device->parent)` from Z2M scan.
  Quality of the link the device actually uses.
- **Direct coordinator LQI** (`coord_lqi_map`): coordinator's measurement receiving
  directly from the device. Only annotated for depth > 1 when below `warn_lqi`.

**Depth-1 edges:** show `down_lqi up_lqi` (both directions).
**Depth-2+ edges:** show single min-path LQI.
**Node badges:** depth 1 = uplink LQI, depth 2+ = path-min LQI.
**Glow/stroke:** always `min(up, down)` for accurate health signal.

## Before/after verification

After any change to `network_map_svg.py`:

```bash
# 1. Tests pass
uv run pytest tests/commands/test_network_map.py -x -q

# 2. No lint errors
uv run ruff check src/zigporter/commands/network_map_svg.py

# 3. Demo SVG regenerates
uv run python scripts/gen_demo_svg.py

# 4. No visual change (for refactoring steps)
git diff docs/assets/network-map-demo.svg
```

## Key files

- `src/zigporter/commands/network_map_svg.py` — layout + rendering
- `src/zigporter/commands/network_map.py` — CLI command, tree building
- `tests/commands/test_network_map.py` — all tests (including `TestLayoutInvariants`)
- `scripts/gen_demo_svg.py` — 30-node demo topology for SVG generation
