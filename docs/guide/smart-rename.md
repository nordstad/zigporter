# `/smart-rename` skill

`/smart-rename` is a Claude Code skill bundled with the project. It audits all your
Zigbee2MQTT devices against a naming convention, generates AI-assisted rename suggestions,
and applies only the renames you confirm — all in one interactive session.

It is **not** a CLI command. It runs inside [Claude Code](https://claude.ai/code) and
calls `rename-device` and `rename-entity` on your behalf.

---

## Prerequisites

- [Claude Code](https://claude.ai/code) installed and open in the zigporter repo
- zigporter configured (`zigporter check` passes)

---

## Usage

Open Claude Code in this repo and type:

```
/smart-rename
```

Optional arguments:

```
/smart-rename --convention '{area}_{type}_{desc}'
/smart-rename --area kitchen
/smart-rename --convention '{area}_{type}_{desc}' --area living_room
```

---

## What it does

### 1. Load convention

The skill reads `~/.config/zigporter/naming-convention.json` for a saved pattern. If
none exists and no `--convention` flag is passed, it infers the pattern from the
majority of your existing device names.

When you pass `--convention`, it asks whether to save it for future runs.

### 2. Gather inventory

Runs `zigporter list-z2m` then `zigporter inspect` for each device (or only devices in
the specified `--area`) to collect friendly names, IEEE addresses, model info, HA areas,
and entity IDs.

### 3. Flag violations

With a loaded/passed convention, the skill checks for:

| Symptom | Example |
|---|---|
| Raw IEEE hex name | `0x00158d0001abc123` |
| `_2` / `_3` numeric suffix | `kitchen_plug_2` |
| Wrong casing | `Office_Lamp`, `livingRoomLight` |
| Missing segment | `kitchen_plug` (no descriptor) |
| No area prefix | `motion_sensor_hallway` |

Semantic suggestions (lower confidence, marked `?`) are shown separately — things like
vocabulary mismatches (`plug` where `socket` is the project standard).

### 4. Generate suggestions

For each violation the skill reasons about the device model, its HA area, and the naming
patterns already used by similar devices in the same area, then proposes a corrected name
with a one-line rationale.

### 5. Confirm renames

The suggestions are shown as a table before any change is made:

```
 #  │ Current name                      │ Suggested name              │ Why
────┼───────────────────────────────────┼─────────────────────────────┼──────────────────────────────
 1  │ 0x00158d0001abc123  (light)        │ living_room_light_ceiling   │ raw IEEE hex — no friendly name
 2  │ kitchen_plug_2                     │ kitchen_socket_dishwasher   │ _2 suffix; plug → socket
 3  │ Office_Lamp                        │ office_light_desk           │ uppercase + type mismatch (?)
────
Already compliant: 12 devices
```

You then choose:

- **`y`** — apply all
- **`n`** — cancel, nothing changes
- **`2,4`** — skip items 2 and 4, apply the rest

### 6. Apply

For each approved item the skill runs `rename-device` (which also triggers a Z2M
config-entry reload) followed by `rename-entity` for each entity whose ID changed.
It reports a summary at the end:

```
Summary: 3 renamed, 1 skipped.
```

---

## Convention file

The naming convention is stored in `~/.config/zigporter/naming-convention.json`:

```json
{
  "pattern": "{area}_{type}_{desc}",
  "updated_at": "2026-03-09T10:00:00+00:00",
  "examples": ["living_room_light_ceiling", "kitchen_socket_dishwasher"]
}
```

You can edit this file directly or let the skill write it for you.

---

## Scope limiting with `--area`

Pass `--area` to audit only one room at a time:

```
/smart-rename --area kitchen
```

Useful for large installs where you want to work room by room rather than reviewing
every device at once.
