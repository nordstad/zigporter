---
name: smart-rename
description: >
  Audits all Zigbee2MQTT devices and Home Assistant entities against a naming convention,
  generates AI-assisted rename suggestions, and applies only user-confirmed renames.
  Use this skill whenever the user wants to audit device or entity names, find naming
  violations, apply bulk renames, get AI-assisted name suggestions, clean up raw IEEE
  hex device names, fix _2/_3 suffix conflicts, or enforce a consistent naming pattern
  across Home Assistant / Zigbee2MQTT devices. Triggers on phrases like "rename my
  devices", "naming audit", "fix device names", "smart rename", "bulk rename", or
  "naming convention".
argument-hint: "[--convention '{Area} {Type} {Desc}'] [--area <room>]"
---

# Smart Rename Skill

Audit device/entity names against a convention, suggest corrected names, and apply
confirmed renames — all in one interactive workflow.

---

## Step 1 — Load convention

Read the saved convention file:

```bash
cat ~/.config/zigporter/naming-convention.json 2>/dev/null || echo "NOT_FOUND"
```

Decision tree:
- **`--convention` arg passed**: use it as the active pattern. Ask *immediately*:
  *"Save `{pattern}` as the default convention for future runs? (y/n)"*
  If yes, write `~/.config/zigporter/naming-convention.json` directly:
  ```json
  {"pattern": "...", "updated_at": "...", "examples": []}
  ```
- **File found**: load `pattern` and `examples`. Announce: *"Using saved convention:
  `{pattern}`"*. Proceed without prompting.
- **File missing + no arg**: do not block — proceed to Step 3 and infer the convention
  from the majority of well-named devices.

---

## Step 2 — Gather inventory

```bash
uv run zigporter list-devices
```

This lists **all** HA devices across every integration (z2m, zha, matter, zwave, hacs,
homekit, etc.) with their name, area, integration label, manufacturer, and model.
**Do not run `zigporter inspect`** — it is interactive-only and cannot be used in batch.

For each device returned (or only devices whose Area column matches `--area <room>` if
that flag was passed), collect:
- `name` (from the Name column)
- `area` (from the Area column — directly available, no inference needed)
- `integration` (z2m, zha, matter, etc.)
- `manufacturer` / `model` (device type hint)

Tag devices with an empty Area column as `⚠ area?`.

Skip the Z2M bridge / coordinator — it has no meaningful human name.

---

## Step 3 — Determine convention and flag violations

### If a convention was loaded / passed

The default convention is `{Area} {Type} {Desc}` — Title Case words separated by
spaces, three segments: area (room), type (device category), descriptor (what it
specifically does or controls).

Validate each friendly name for **structural** violations:

| Symptom | Example |
|---|---|
| Raw IEEE hex name | `0x00158d0001abc123` |
| `_2` / `_3` numeric suffix | `Kitchen Plug_2` |
| Wrong casing (all-caps or snake_case) | `OFFICE_LAMP`, `office_lamp` |
| Missing required segment (only 1–2 words) | `Attic Climate` (no desc), `Garage` |
| Missing area prefix | `Sensor Motion` |

Also flag **semantic** suggestions (lower confidence — mark with `?`):

| Symptom | Example |
|---|---|
| Likely wrong device-type word | `Kitchen Plug Coffee` where Plug → Socket (?) |
| Redundant word in name | `Downstairs Livingroom Room Left Plug` |
| Foreign-language words | `Ute Billadder Smart Kontakt` (Swedish) |
| Brand name in area position | `TRADFRI Outlet Kontor` |

### If inferring convention (no file, no arg)

Analyse all friendly names (excluding raw IEEE hex ones). Identify the dominant
structural pattern — the separator character, segment count, and casing used by the
majority. Announce: *"Inferred convention: `{pattern}` (N of M devices match)."*
Flag everything that deviates.

### If everything is already compliant

Report: *"All N devices are already compliant with `{pattern}`. Nothing to do."*
Then stop.

---

## Step 4 — Generate suggestions

For each flagged device, reason about:

1. **Device model** — vendor + model from `list-z2m` tells you the device category
   (light, plug/socket, motion sensor, climate sensor, smoke sensor, button…)
2. **Area** — infer from the name prefix; use `⚠ area?` if ambiguous
3. **Descriptor** — what makes it unique within the area? (Ceiling, Desk, Dishwasher,
   Motion, Door…). Translate foreign-language words to English.
4. **Peer names** — look at other devices in the same area for vocabulary consistency

Produce:
- `suggested_name` — corrected friendly name matching the convention
- `rationale` — one concise phrase

**Handling unknown area:** tag the device as `⚠ area?` and propose a name with
`Unknown` as the area prefix. Collect *all* `area?` devices and ask once, as a group,
before the confirmation table: *"These N devices have no area assigned — enter a room
for each, or press Enter to leave as `Unknown`."*

---

## Step 5 — Present confirmation table

Show the full table before doing anything:

```
 #  │ Current name                          │ Suggested name              │ Why
────┼───────────────────────────────────────┼─────────────────────────────┼────────────────────────────────
 1  │ 0x00158d0001abc123                     │ Living Room Light Ceiling   │ raw IEEE hex — no friendly name
 2  │ Downstairs Livingroom Room Left Plug   │ Downstairs Living Room Plug Left  │ "Livingroom Room" redundant
 3  │ Ute Billadder Smart Kontakt            │ Outside Plug Car Charger    │ Swedish throughout (?)
────
Already compliant: 12 devices
```

Then ask:

> **Apply all renames?**
> Enter `y` to apply all, `n` to cancel, or comma-separated numbers to skip
> (e.g. `2,4` skips items 2 and 4):

Parse the response:
- `y` → apply all items
- `n` → abort, print "No renames applied."
- `2,4` → skip those indices, apply the rest

---

## Step 6 — Apply confirmed renames

`rename-device` handles everything: it renames the HA device, cascades to all entity
IDs and config references, and (interactively) syncs the Z2M friendly name.

For each approved item, run **without** `--apply` so that the Z2M sync prompt is
included:

```bash
uv run zigporter rename-device "<old_friendly_name>" "<new_friendly_name>"
```

The command shows a dry-run preview and prompts:
1. **Apply HA rename? (y/n)** — answer `y`
2. **Also rename in Z2M? (y/n)** — answer `y` to keep Z2M in sync

Since the user already confirmed their selections in Step 5, they should expect these
per-device prompts and answer `y`/`y` for each.

After all items:

```
Summary: 3 renamed, 1 skipped.
```

If any command fails, report the error inline and continue with the remaining items.
