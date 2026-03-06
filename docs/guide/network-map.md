# network-map

Visualise your Zigbee mesh topology with signal-strength annotations.

```bash
zigporter network-map
```

## Output formats

| Flag | Description |
|---|---|
| `--format tree` | Indented routing tree (default) |
| `--format table` | Flat table sorted by LQI ascending (weakest links first) |
| `--svg <file>` | Also export an SVG diagram |

## LQI thresholds

| Flag | Default | Meaning |
|---|---|---|
| `--warn-lqi` | 80 | Below this → shown in yellow as `WEAK` |
| `--critical-lqi` | 30 | Below this → shown in red as `CRITICAL` |

## Reading the output

```
Coordinator
    ├── Hall Door Plug    [router]  LQI: 130  hops: 1
    │    └── SMLIGHT SLZB-06P7    [router]  LQI: 76  hops: 2  WEAK  (coord: 35)
    │        └── Ute Billadder    [end]     LQI: 99  hops: 3  (coord: 39)
```

### LQI (the main number)

The **routing path quality** — the bidirectional LQI between a device and its **routing
parent** in the tree.  Computed as `min(parent→device, device→parent)` using the Z2M
network-map scan data.  This reflects the actual link the device uses to forward traffic
and is the most relevant metric for understanding mesh health.

Using the minimum of both directions matters because Zigbee links are asymmetric: a
device may hear the coordinator at LQI 115 while the coordinator only hears the device
at LQI 29.  The weaker direction is the real bottleneck.

### `(coord: N)` annotation

Shown in yellow or red for **depth > 1 devices** (those that route through at least one
intermediate router) when their **direct coordinator link** is below `--warn-lqi`.

This is the LQI the coordinator measured when it received a frame directly from the
device during the network scan.  It is also the value shown in the Z2M device card
badge (`last_linkquality`).

The two numbers tell different stories:

| Value | What it means |
|---|---|
| `LQI: 76` | The device has a solid path through its routing parent (Hall Door Plug) |
| `(coord: 35)` | If that router disappears, the fallback direct link to the coordinator is weak |

A device with a good routing LQI but a low `coord` value is correctly routing around a
weak direct coordinator link — that is healthy mesh behaviour.  The annotation is there
so you know the fallback path is poor if the parent router ever fails.

### Why the Z2M badge and `network-map` LQI differ for routed devices

Z2M's device card shows `last_linkquality`, which is the LQI the coordinator measured
the last time it received a frame directly from that device (during a network scan probe
or a rare direct transmission).  For a device that routes through an intermediate router
this is the **direct coordinator link** quality, not the quality of the actual routing
path.

`network-map` shows the **routing path quality** (the edge in the tree), which is more
informative for diagnosing mesh connectivity.  The `(coord: N)` annotation exposes the
Z2M-badge value alongside it so you have both pieces of information in one place.

## Z2M 2.x notes

Z2M 2.x does not publish retained MQTT messages on device state topics, so the live
`last_linkquality` overlay (which would update the routing-path LQI with real-time
values) has no effect in practice.  All LQI values shown come from the Z2M network-map
scan itself.

HA may also disable the `Linkquality` diagnostic sensor entity by default
(`"disabled_by": "integration"`).  Even if enabled, the value reflects the same direct
coordinator link shown in the `(coord: N)` annotation, not the routing path quality.

## Example — table format

```bash
zigporter network-map --format table --warn-lqi 100
```

```
Device                          Role    Parent               LQI   Hops  Status
──────────────────────────────────────────────────────────────────────────────
Hall Coatroom Led Light         end     Coordinator            0      1   CRITICAL
Outside Front Climate           end     Coordinator            6      1   CRITICAL
Förråd Smart Kontakt            end     Downstairs Left Plug  48      4   WEAK  (coord: 1)
```
