[![CI](https://github.com/nordstad/zigporter/actions/workflows/ci.yml/badge.svg)](https://github.com/nordstad/zigporter/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/nordstad/zigporter/graph/badge.svg)](https://codecov.io/gh/nordstad/zigporter)
[![Documentation](https://img.shields.io/badge/docs-mkdocs-blue)](https://nordstad.github.io/zigporter)
[![PyPI - Version](https://img.shields.io/pypi/v/zigporter)](https://pypi.org/project/zigporter/)
[![PyPI - Downloads](https://img.shields.io/pepy/dt/zigporter)](https://pepy.tech/project/zigporter)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

# zigporter

CLI tool to migrate Zigbee devices from ZHA to Zigbee2MQTT in Home Assistant.
Runs an interactive per-device wizard with persistent state so migrations can be paused and resumed across sessions.

> **Early Development Notice**
> This tool is in early development and has only been tested with one specific setup:
> - Home Assistant OS 2026.2.3
> - Supervisor 2026.02.2
> - Zigbee2MQTT 2.8.0-1
>
> I have not had the possibility to test with different HA or Z2M versions and setups.
> Feedback is very welcome — please open an [issue](https://github.com/nordstad/zigporter/issues) or submit a [PR](https://github.com/nordstad/zigporter/pulls) if you test with a different configuration.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Home Assistant with ZHA and Zigbee2MQTT add-on

## Installation

```bash
uv tool install zigporter
```

## Configuration

```bash
cp .env.example .env   # fill in your values
```

| Variable | Description |
|---|---|
| `HA_URL` | Home Assistant URL |
| `HA_TOKEN` | [Long-Lived Access Token](https://www.home-assistant.io/docs/authentication/#your-account-profile) |
| `HA_VERIFY_SSL` | `true` / `false` (false for self-signed certs) |
| `Z2M_URL` | Zigbee2MQTT ingress URL |
| `Z2M_MQTT_TOPIC` | Z2M base topic (default: `zigbee2mqtt`) |

## Usage

```bash
# Export your ZHA device inventory
zigporter export

# (Optional) inspect what's already in Z2M
zigporter list-z2m

# Run the migration wizard
zigporter migrate [ZHA_EXPORT]

# Check progress without entering the wizard
zigporter migrate --status
```

## How it works

The wizard migrates one device at a time through five steps:

1. **Remove from ZHA** — confirms deletion in the HA registry
2. **Reset device** — prompts you to factory-reset the physical device
3. **Pair with Z2M** — opens a 120 s permit-join window and polls by IEEE address
4. **Rename** — applies the original ZHA name and area in Z2M and HA
5. **Validate** — polls HA entity states until all are online

State is written to `zha-migration-state.json` after every step. `Ctrl-C` marks the device `FAILED` — rerun to retry.

See the [wiki](https://github.com/nordstad/zigporter/wiki) for detailed diagrams and architecture docs.

## Development

```bash
uv sync --dev
uv run pytest
uv run ruff check .
uv run ruff format .
```

## License

MIT — see [LICENSE](LICENSE).
