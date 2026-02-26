# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`zigporter` is a CLI tool for migrating Zigbee devices from ZHA (Zigbee Home Automation) to Zigbee2MQTT in Home Assistant. It uses an interactive wizard workflow with persistent state tracking so migrations can be paused and resumed.

## Commands

```bash
# Install dependencies
uv sync

# Run CLI
uv run zigporter --help
uv run zigporter export
uv run zigporter list-z2m
uv run zigporter migrate <export-file>

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

```
CLI Layer       main.py (Typer app, registers commands)
    ↓
Command Layer   commands/{export,migrate,compare,rename}.py
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

## Key Conventions

- Python 3.13; use built-in generics (`list[str]`, `dict[str, int]`) — never `from typing import List, Dict`.
- All I/O is async (`asyncio`/`httpx`/`websockets`).
- Pydantic v2 models for all structured data.
- Line length: 100 chars (ruff config in `pyproject.toml`).
- Tests use `pytest-asyncio` (auto mode), `respx` for HTTP mocking, and `pytest-mock` for patches.

## Z2M Migration Gotchas

- After renaming a device in Z2M, HA entity IDs update async (IEEE-hex names → friendly-name-based). Re-fetch entity IDs from the registry on each polling attempt, not just once before the loop.
- After pairing with Z2M, the device has a **new** HA `device_id` (MQTT-based). Never reuse the old ZHA `device_id` for area assignment or entity lookup — use `HAClient.get_z2m_device_id(ieee)` instead.
- When adding async methods to `HAClient`, update the `mock_ha_client` fixture in `tests/commands/test_migrate.py` with `AsyncMock` for each new method.
- Scope `ruff format` to changed files only (`uv run ruff format <file>`) to avoid noisy diffs from pre-existing formatting drift in untouched files.
