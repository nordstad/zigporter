# zigporter

[![CI](https://github.com/nordstad/zigporter/actions/workflows/ci.yml/badge.svg)](https://github.com/nordstad/zigporter/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/nordstad/zigporter/branch/main/graph/badge.svg)](https://codecov.io/gh/nordstad/zigporter)
[![Documentation](https://img.shields.io/badge/docs-zensical-blue)](https://nordstad.github.io/zigporter)
[![PyPI - Version](https://img.shields.io/pypi/v/zigporter?label=PyPI)](https://pypi.org/project/zigporter/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![PyPI Downloads](https://static.pepy.tech/personalized-badge/zigporter?period=total&units=INTERNATIONAL_SYSTEM&left_color=BLACK&right_color=GREEN&left_text=downloads)](https://pepy.tech/projects/zigporter)

Home Assistant device management from the command line — migrate from ZHA to Zigbee2MQTT,
rename entities and devices with full cascade across automations, scripts, and dashboards.

> **Early Development** — Tested with HA OS 2026.2.3 · Supervisor 2026.02.2 · Z2M 2.8.0-1.
> Open an [issue](https://github.com/nordstad/zigporter/issues) if you run a different configuration.

## Features

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
  </tbody>
</table>

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Home Assistant with the Zigbee2MQTT add-on

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

This prompts for all required values and saves them to `~/.config/zigporter/.env`.

You can also set environment variables directly or create the file manually:

| Variable | Required | Description |
|---|---|---|
| `HA_URL` | Yes | Home Assistant base URL |
| `HA_TOKEN` | Yes | [Long-Lived Access Token](https://www.home-assistant.io/docs/authentication/#your-account-profile) |
| `HA_VERIFY_SSL` | No | `true` (default) or `false` for self-signed certificates |
| `Z2M_URL` | For `migrate` / `list-z2m` / `rename-device` | Zigbee2MQTT ingress URL |
| `Z2M_MQTT_TOPIC` | No | Z2M base MQTT topic (default: `zigbee2mqtt`) |

See [Configuration](https://nordstad.github.io/zigporter/getting-started/configuration/) for full details.

## Migrate ZHA → Zigbee2MQTT

```bash
# Verify connectivity first
zigporter check

# Run the migration wizard
zigporter migrate
```

> **Back up first** — The migration wizard removes devices from ZHA and makes changes to
> entity IDs, automations, and dashboards that are difficult to reverse. Before running,
> [back up your Home Assistant configuration](https://www.home-assistant.io/common-tasks/os/#backups).
> This tool is provided **as-is** with no warranty. Use at your own risk.

On the first non-status run, `zigporter` requires a one-time backup confirmation and stores
that acknowledgement in `~/.config/zigporter/.backup-confirmed`.

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

## Rename an Entity

Rename a Home Assistant entity ID and automatically update every reference — automations,
scripts, scenes, and Lovelace dashboards:

```bash
# Preview changes (dry run)
zigporter rename-entity light.living_room_1 light.living_room_ceiling

# Apply the rename
zigporter rename-entity light.living_room_1 light.living_room_ceiling --apply
```

Without `--apply` the command shows a full diff and prompts for confirmation before writing.

> **Note:** Jinja2 template expressions (`{{ states('old.id') }}`) are not patched automatically — review them after renaming.

## Rename a Device

Rename any Home Assistant device by name and cascade the change to all its entities and
references in HA. Supports partial name matching:

```bash
# Preview changes (dry run)
zigporter rename-device "Living Room 1" "Living Room Ceiling"

# Apply the rename
zigporter rename-device "Living Room 1" "Living Room Ceiling" --apply
```

If `Z2M_URL` is configured and the device is managed by Zigbee2MQTT, the command also
offers to rename the Z2M friendly name in a separate prompt — so you stay in control of
whether HA and Z2M names are kept in sync.

## Development

```bash
uv sync --dev
uv run pytest
uv run ruff check .
uv run ruff format .
```

See [Development](https://nordstad.github.io/zigporter/development/) for architecture details and contribution guidelines.

## License

MIT — see [LICENSE](LICENSE).
