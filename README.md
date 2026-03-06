<div align="center">
  <img src="https://raw.githubusercontent.com/nordstad/zigporter/main/docs/assets/mesh_house_pulse_256.gif" alt="zigporter" width="128">
  <h1>zigporter</h1>
  <a href="https://github.com/nordstad/zigporter/actions/workflows/ci.yml"><img src="https://github.com/nordstad/zigporter/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://codecov.io/gh/nordstad/zigporter"><img src="https://codecov.io/gh/nordstad/zigporter/branch/main/graph/badge.svg" alt="codecov"></a>
  <a href="https://nordstad.github.io/zigporter"><img src="https://img.shields.io/badge/docs-zensical-blue" alt="Documentation"></a>
  <a href="https://pypi.org/project/zigporter/"><img src="https://img.shields.io/pypi/v/zigporter?label=PyPI" alt="PyPI - Version"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12+-blue.svg" alt="Python 3.12+"></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
  <a href="https://pepy.tech/projects/zigporter"><img src="https://static.pepy.tech/personalized-badge/zigporter?period=total&units=INTERNATIONAL_SYSTEM&left_color=BLACK&right_color=GREEN&left_text=downloads" alt="PyPI Downloads"></a>
  <p>Home Assistant device management from the command line — migrate from ZHA to Zigbee2MQTT,<br>rename entities and devices with full cascade across automations, scripts, and dashboards.</p>
</div>

## Features

**[Interactive Demo →](https://nordstad.github.io/zigporter/interactive-demo/)** — see every command in action before installing.

<table>
  <thead>
    <tr><th>Command</th><th>Description</th></tr>
  </thead>
  <tbody>
    <tr><td nowrap><code>migrate</code></td><td>Interactive wizard: remove from ZHA → factory reset → pair with Z2M → restore names, areas, and entity IDs</td></tr>
    <tr><td nowrap><code>rename&#x2011;entity</code></td><td>Rename a HA entity ID and cascade the change across automations, scripts, scenes, and all Lovelace dashboards</td></tr>
    <tr><td nowrap><code>rename&#x2011;device</code></td><td>Rename any HA device by name and cascade the change to all its entities and references</td></tr>
    <tr><td nowrap><code>check</code></td><td>Verify HA and Z2M connectivity before making changes</td></tr>
    <tr><td nowrap><code>inspect</code></td><td>Show a device's current state across ZHA, Z2M, and the HA registry</td></tr>
    <tr><td nowrap><code>export</code></td><td>Snapshot your ZHA device inventory to JSON</td></tr>
    <tr><td nowrap><code>list&#x2011;z2m</code></td><td>List all devices currently paired with Zigbee2MQTT</td></tr>
    <tr><td nowrap><code>fix&#x2011;device</code></td><td>Post-migration cleanup: remove stale ZHA device entries, delete their entities, and rename any <code>_2</code>/<code>_3</code> suffixed Z2M entities back to their original IDs</td></tr>
    <tr><td nowrap><code>stale</code></td><td>Scan all integrations for offline devices and interactively remove, annotate, ignore, or permanently suppress them</td></tr>
    <tr><td nowrap><code>network&#x2011;map</code></td><td>Render a radial SVG map of the Zigbee mesh with LQI-coloured edges, hop rings, and path-quality badges</td></tr>
  </tbody>
</table>

## Installation

```bash
uv tool install zigporter
```

Or with pip:

```bash
pip install zigporter
```

## Configuration

Run the setup wizard to get started:

```bash
zigporter setup
```

This prompts for HA credentials (required) and Zigbee2MQTT settings (optional — only needed for `migrate` and `list-z2m`).

You can also set environment variables directly or create the file manually:

| Variable | Required | Description |
|---|---|---|
| `HA_URL` | Yes | Home Assistant base URL |
| `HA_TOKEN` | Yes | [Long-Lived Access Token](https://www.home-assistant.io/docs/authentication/#your-account-profile) |
| `HA_VERIFY_SSL` | No | `true` (default) or `false` for self-signed certificates |
| `Z2M_URL` | Only for `migrate` and `list-z2m` | Zigbee2MQTT ingress URL |
| `Z2M_MQTT_TOPIC` | Only for `migrate` and `list-z2m` | Z2M base MQTT topic (default: `zigbee2mqtt`) |

See [Configuration](https://nordstad.github.io/zigporter/getting-started/configuration/) for full details.

## Migrate ZHA → Zigbee2MQTT

---
> [!WARNING]
> **Back up first** — The migration wizard removes devices from ZHA and makes changes to
> entity IDs, automations, and dashboards that are difficult to reverse. Before running,
> [back up your Home Assistant configuration](https://www.home-assistant.io/common-tasks/os/#backups).
> This tool is provided **as-is** with no warranty. Use at your own risk.
---

```bash
# Verify connectivity first
zigporter check

# Run the migration wizard
zigporter migrate
```

The wizard guides you through each device one at a time:

1. Remove from ZHA — polls the HA registry until the device is gone
2. Factory reset — prompts to clear the old pairing on the physical device
3. Pair with Z2M — opens a 300 s permit-join window and polls by IEEE address
4. Rename — restores the original ZHA name and area in Z2M and HA
5. Restore entity IDs — renames IEEE-hex entity IDs back to friendly names; detects `_2`/`_3` suffix conflicts caused by stale ZHA entries and offers to delete them and rename the Z2M entities back to their original IDs
6. Review — shows all Lovelace cards referencing the device
7. Validate — polls HA entity states until all entities come online; offers a "Reload Z2M integration" option to force-refresh sensor state without leaving the CLI

Progress is saved after every step. Press `Ctrl-C` to pause; rerun to resume.

```bash
# Check progress without entering the wizard
zigporter migrate --status
```

## Fix a Previously Migrated Device

If you migrated a device before the suffix-conflict fix was added, or if stale ZHA entries
were left behind, use `fix-device` to clean them up:

```bash
zigporter fix-device
```

The command scans HA for devices that have both a stale ZHA entry and an active Z2M entry,
lets you pick one, deletes the stale ZHA entities, removes the ZHA device from the registry,
and renames any `_2`/`_3` suffixed Z2M entities back to their original IDs so dashboard
cards work again.

## Find and Clean Up Offline Devices

Scan HA for devices whose entities are all `unavailable` or `unknown`:

```bash
zigporter stale
```

The command lists every offline device grouped as **New / Stale / Ignored**. For each
device you can:

- **Remove** — delete the entry from the HA registry
- **Mark as stale** — add a note and come back later
- **Ignore** — mark devices you know are intentionally offline
- **Suppress** — permanently hide a ghost entry or false positive from all future runs
- **Clear status** — reset to New (also un-suppresses a suppressed device)

Resolved entries (devices that came back online since your last run) are pruned from the
state file automatically. Hub and gateway devices with active children (e.g. a Plejd
GWY-01 whose lights are responsive) are automatically excluded to avoid false positives.

## Rename an Entity

Rename a Home Assistant entity ID and automatically update every reference — automations,
scripts, scenes, and Lovelace dashboards.

Both arguments are optional. Run with no arguments for a guided wizard:

```bash
# Interactive wizard — pick entity from a list, edit the new ID in-place
zigporter rename-entity

# Provide the old ID only — prompts for the new ID (pre-filled for editing)
zigporter rename-entity sensor.old_device_energy

# Preview changes (dry run)
zigporter rename-entity light.living_room_1 light.living_room_ceiling

# Apply the rename
zigporter rename-entity light.living_room_1 light.living_room_ceiling --apply
```

Without `--apply` the command shows a full diff and prompts for confirmation before writing.
New entity IDs are validated against the `domain.entity_name` format before any changes are made.

> **Note:** Jinja2 template expressions (`{{ states('old.id') }}`) are not patched automatically — review them after renaming.

## Rename a Device

Rename any Home Assistant device by name and cascade the change to all its entities and
references in HA. Supports partial name matching.

Both arguments are optional. Run with no arguments for a guided wizard:

```bash
# Interactive wizard — pick device from an area-grouped list, type the new name
zigporter rename-device

# Restrict the picker to Zigbee devices only (ZHA + Zigbee2MQTT)
zigporter rename-device --filter=zigbee

# Provide the old name only — prompts for the new name
zigporter rename-device "Living Room 1"

# Preview changes (dry run)
zigporter rename-device "Living Room 1" "Living Room Ceiling"

# Apply the rename
zigporter rename-device "Living Room 1" "Living Room Ceiling" --apply
```

If `Z2M_URL` is configured and the device is managed by Zigbee2MQTT, the command also
offers to rename the Z2M friendly name in a separate prompt — so you stay in control of
whether HA and Z2M names are kept in sync.

## Visualise the Zigbee Mesh

Generate a radial SVG map of your network with LQI-coloured edges and per-device
path-quality badges:

```bash
# Default: tree view printed to terminal + SVG file
zigporter network-map --output z2m_network.svg

# Table view (sortable columns) instead of tree
zigporter network-map --format table

# Adjust LQI thresholds (edges and glows change colour accordingly)
zigporter network-map --output network.svg --warn-lqi 100 --critical-lqi 50
```

Open the `.svg` in any browser — hover over truncated device names to see the full name.

## Confirmed Working

| Software | Version |
|---|---|
| Home Assistant OS | 2026.2.3 |
| HA Supervisor | 2026.02.2 |
| Zigbee2MQTT | 2.8.0-1 |

Running a different version? Open an [issue](https://github.com/nordstad/zigporter/issues) to let us know.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions, code style, and how to submit a pull request.

```bash
uv sync --dev
uv run pytest
uv run ruff check .
uv run ruff format .
```

See [Development](https://nordstad.github.io/zigporter/development/) for architecture details and contribution guidelines.

## License

MIT — see [LICENSE](LICENSE).
