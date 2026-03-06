# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`zigporter` is a CLI toolkit for Zigbee device management in Home Assistant: migrate devices from ZHA to Zigbee2MQTT, rename entities/devices with cascading HA config updates, and fix stale ZHA registry entries post-migration. Uses an interactive wizard workflow with persistent state tracking so migrations can be paused and resumed.

## Commands

```bash
# Install dependencies
uv sync

# Run CLI
uv run zigporter --help
uv run zigporter export
uv run zigporter list-z2m
uv run zigporter migrate <export-file>
uv run zigporter check                       # Pre-flight connectivity check
uv run zigporter inspect <device>            # Inspect a single device's state
uv run zigporter rename-entity <old> <new>   # Rename a HA entity ID
uv run zigporter rename-device <id> <name>   # Rename a Z2M device friendly name
uv run zigporter fix-device                  # Post-migration cleanup for stale ZHA entries

# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/test_ha_client.py

# Run a single test
uv run pytest tests/test_ha_client.py::test_name

# Lint and format
uv run ruff check .
uv run ruff format .
```

## Architecture

The codebase follows a layered architecture:

```text
CLI Layer       main.py (Typer app, registers commands)
    ↓
Command Layer   commands/{check,export,fix_device,inspect,list_z2m,migrate,rename,setup}.py
    ↓
Client Layer    ha_client.py (HA WebSocket + REST), z2m_client.py (Z2M HTTP ingress)
    ↓
Data Layer      models.py (Pydantic), migration_state.py (persistent JSON)
```

**API communication:**

- `HAClient` uses WebSocket for ZHA device registry queries (HA 2025+ dropped the REST ZHA endpoint) and REST for entity states.
- `Z2MClient` uses a three-tier auth fallback: (1) Bearer token directly on `Z2M_URL`, (2) ingress session cookie via `/api/hassio/ingress/session`, (3) HA-native fallback using `HAClient.call_service()` for `mqtt.publish` when Supervisor is unavailable.

**State persistence:** `MigrationState` serializes to JSON on disk, keyed by IEEE address. Device progress is tracked as `PENDING → IN_PROGRESS → MIGRATED / FAILED`.

**`compare` and `rename` commands have been removed** — they were unimplemented stubs. Use `migrate --status` and `list-z2m` instead.

## Configuration

Run `zigporter setup` or create `~/.config/zigporter/.env`. CWD `.env` still works as
a project-level override (useful for `uv run` development).

`config.py` loads these via `python-dotenv` and exposes a `Config` dataclass. SSL context is built from `HA_VERIFY_SSL` and passed through all HTTP/WebSocket calls.

## Environment Variables

Required in `~/.config/zigporter/.env` or `.env` (CWD):

```env
HA_URL=http://homeassistant.local:8123
HA_TOKEN=<long-lived access token>
HA_VERIFY_SSL=true          # Set false for self-signed certs
Z2M_URL=http://homeassistant.local:8123/api/hassio_ingress/<slug>
Z2M_MQTT_TOPIC=zigbee2mqtt  # Default; change if customised
```

## Key Conventions

- Python 3.13; use built-in generics (`list[str]`, `dict[str, int]`) — never `from typing import List, Dict`.
- All I/O is async (`asyncio`/`httpx`/`websockets`).
- Pydantic v2 models for all structured data.
- Line length: 100 chars (ruff config in `pyproject.toml`).
- Tests use `pytest-asyncio` (auto mode), `respx` for HTTP mocking, and `pytest-mock` for patches.

## Z2M Migration Gotchas

- After renaming a device in Z2M (via `rename-device`), HA entities go Unknown because the MQTT topic changes. Fix: reload the Z2M config entry (`HAClient.reload_config_entry`). The Z2M config entry is identified by `domain=mqtt` + title containing `"zigbee2mqtt"` — see `HAClient.get_z2m_config_entry_id()`.
- When adding async methods to `HAClient` called from `execute_device_rename`, also add them as `AsyncMock` to the `mock_device_exec_client` fixture in `tests/commands/test_rename.py` (in addition to the existing note about `mock_ha_client` in `test_migrate.py`).
- After renaming a device in Z2M, HA entity IDs update async (IEEE-hex names → friendly-name-based). Re-fetch entity IDs from the registry on each polling attempt, not just once before the loop.
- After pairing with Z2M, the device has a **new** HA `device_id` (MQTT-based). Never reuse the old ZHA `device_id` for area assignment or entity lookup — use `HAClient.get_z2m_device_id(ieee)` instead.
- When adding async methods to `HAClient`, update the `mock_ha_client` fixture in `tests/commands/test_migrate.py` with `AsyncMock` for each new method.
- Scope `ruff format` to changed files only (`uv run ruff format <file>`) to avoid noisy diffs from pre-existing formatting drift in untouched files.
- **`_2`/`_3` entity suffix conflicts:** HA appends numeric suffixes to new Z2M entity IDs when stale ZHA registry entries still occupy the original IDs. Step 5 of the migrate wizard detects and resolves this automatically. For devices that were already migrated before this fix, use `zigporter fix-device` to clean up stale entries and rename suffixed entities back to their originals.
- **Helper / Group config entries:** HA Helper config entries (groups, template helpers, etc.) store entity ID references in their `options` dict, not in automations/scenes/dashboards. `rename-entity` scans these via `HAClient.get_config_entries()` and patches them via `HAClient.update_config_entry_options()`. They appear as "helper" rows in the rename plan. This mirrors the `build_rename_plan_from_snapshot` logic used by `rename-device`.

## Publishing to PyPI

Full details in `guides/publishing.md`. Quick reference:

```bash
# 1. Bump version + CHANGELOG (or use /bump-version skill)
#    Edit pyproject.toml: version = "x.y.z"
#    Edit CHANGELOG.md: move [Unreleased] → [x.y.z] with today's date

# 2. Commit and push to main
git add pyproject.toml CHANGELOG.md
git commit -m "chore: bump version to x.y.z"
git push origin main

# 3. Tag and push — the workflow handles EVERYTHING else
git tag vx.y.z
git push origin vx.y.z
# ✅ Builds, validates, publishes to PyPI, creates GitHub Release, updates CHANGELOG
# ❌ DO NOT manually run: gh release create vx.y.z
```

Use the `/bump-version` skill to automate step 1 (analyses unreleased commits, moves
`[Unreleased]` entries, fixes comparison links, commits — does NOT tag or push).

## network-map LQI semantics

The `network-map` command displays two distinct LQI values per device.  Understanding
the difference is important when modifying the rendering or comparing output to Z2M.

**Routing path LQI** (`lqi_map`, shown as `LQI: N` in the tree)
: `min(parent→device, device→parent)` from the Z2M network-map scan.  Represents the
quality of the actual link the device uses to forward traffic.  A device at depth 2
correctly shows a high value here if it routes through a strong intermediate router,
even though its direct coordinator link may be weak.

**Direct coordinator LQI** (`coord_lqi_map`, shown as `(coord: N)` annotation)
: LQI measured by the coordinator when receiving a direct frame from this device during
the network scan.  This is the value shown in the Z2M device card badge
(`last_linkquality`).  Only annotated for depth > 1 devices where it is below
`warn_lqi`, to flag poor fallback connectivity if the routing parent fails.

**Z2M link direction convention**: in the raw network-map data, `source` = neighbor
being measured, `target` = scanning device, `lqi` = measured **by the scanner receiving
from the neighbor**.  So `{source: A, target: Coordinator, lqi: 29}` means the
coordinator measured 29 when receiving from A — this is the direct coordinator LQI for
A that ends up in `coord_lqi_map`.

**Why Z2M 2.x live overlay does not work**: Z2M 2.x does not publish retained messages
on device state topics, and HA disables the `Linkquality` diagnostic sensor entity by
default (`"disabled_by": "integration"`).  The `get_linkquality_map()` MQTT subscriber
returns an empty dict in practice; all LQI values come from the network-map scan.

## Demo

`docs/demo/index.html` is a self-contained browser terminal emulator with hardcoded playback scripts.

**Keep it in sync:** after adding or significantly changing a CLI command, run `/update-demo` to audit
and update the demo scenarios at `docs/demo/index.html`. Specifically:

- New command added → add a `DEMO_<NAME>` script and `DEMOS` registry entry
- Command output or steps changed → update the matching `DEMO_*` constant
- Command removed → remove its `DEMO_*` constant and `DEMOS` entry
