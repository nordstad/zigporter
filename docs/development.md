# Development

## Setup

```bash
git clone https://github.com/nordstad/zigporter.git
cd zigporter
uv sync --dev
cp .env.example .env
```

## Running tests

```bash
uv run pytest                         # all tests
uv run pytest tests/test_ha_client.py # single file
uv run pytest -k test_name            # single test
```

Tests use `pytest-asyncio` (auto mode), `respx` for HTTP mocking, and `pytest-mock` for patches.

## Lint and format

```bash
uv run ruff check .    # lint
uv run ruff format .   # format
```

Run these before opening a PR to keep CI green.

## Key conventions

- Python 3.12+; use built-in generics (`list[str]`, `dict[str, int]`)
- All I/O is async (`asyncio` / `httpx` / `websockets`)
- Pydantic v2 models for all structured data
- Line length: 100 chars

## Adding async methods to HAClient

When adding async methods to `HAClient`, update the `mock_ha_client` fixture in `tests/commands/test_migrate.py` with `AsyncMock` for each new method.

## Z2M gotchas

- After renaming a device in Z2M, HA entity IDs update asynchronously. Re-fetch entity IDs from the registry on each polling attempt — never cache them before the loop.
- After pairing with Z2M, the device has a **new** HA `device_id` (MQTT-based). Never reuse the old ZHA `device_id` — use `HAClient.get_z2m_device_id(ieee)` instead.
