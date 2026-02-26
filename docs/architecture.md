# Architecture

## Layer overview

```
CLI Layer       main.py  (Typer, --help / --version)
    ↓
Command Layer   commands/{export, migrate, list_z2m, compare*, rename*}.py
    ↓
Client Layer    ha_client.py   (WebSocket + REST)
                z2m_client.py  (HTTP ingress, three-tier auth)
    ↓
Data Layer      models.py           (Pydantic v2)
                migration_state.py  (JSON on disk, keyed by IEEE)
```

`compare` and `rename` commands are stubs — not yet implemented.

## API communication

### HAClient

Uses WebSocket for ZHA device registry queries (HA 2025+ dropped the REST ZHA endpoint) and REST for entity states. All calls are async via `httpx` and `websockets`.

### Z2MClient

Uses a three-tier auth fallback — see [Z2M authentication](guide/authentication.md).

## State persistence

`MigrationState` serializes device progress to a JSON file on disk, keyed by IEEE address.

Transitions: `PENDING → IN_PROGRESS → MIGRATED / FAILED`

After every state transition the file is written synchronously so a crash or `Ctrl-C` never loses progress.

## Configuration

`config.py` loads variables from `.env` via `python-dotenv` and exposes a `Config` dataclass. An SSL context is built from `HA_VERIFY_SSL` and passed through all HTTP and WebSocket calls.
