[![CI](https://github.com/nordstad/zigporter/actions/workflows/ci.yml/badge.svg)](https://github.com/nordstad/zigporter/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/nordstad/zigporter/graph/badge.svg)](https://codecov.io/gh/nordstad/zigporter)
[![Documentation](https://img.shields.io/badge/docs-mkdocs-blue)](https://nordstad.github.io/zigporter)
[![PyPI - Version](https://img.shields.io/pypi/v/zigporter)](https://pypi.org/project/zigporter/)
[![PyPI - Downloads](https://img.shields.io/pepy/dt/zigporter)](https://pepy.tech/project/zigporter)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

# zigporter

*Because re-pairing 30 Zigbee devices by hand is a special kind of misery.*

CLI tool that automates the ZHA → Zigbee2MQTT migration in Home Assistant — one device at a
time, with checkpoints so you can stop and pick up where you left off.

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

**Option 1 — Setup wizard (recommended)**

```bash
zigporter setup
```

Prompts for all values and saves to `~/.config/zigporter/.env`.

**Option 2 — Manual config file**

Create `~/.config/zigporter/.env` (see `.env.example` for the template):

```bash
mkdir -p ~/.config/zigporter
cp .env.example ~/.config/zigporter/.env
# edit the file with your values
```

**Option 3 — Environment variables**

Export directly in your shell or add to `~/.zshenv` / `~/.bashrc`:

```bash
export HA_URL=https://your-ha-instance.local
export HA_TOKEN=your_token
export Z2M_URL=https://your-ha-instance.local/abc123_zigbee2mqtt
```

| Variable | Required | Description |
|---|---|---|
| `HA_URL` | Yes | Home Assistant URL |
| `HA_TOKEN` | Yes | [Long-Lived Access Token](https://www.home-assistant.io/docs/authentication/#your-account-profile) |
| `HA_VERIFY_SSL` | No | `true` / `false` (default: `true`; use `false` for self-signed certs) |
| `Z2M_URL` | Yes | Zigbee2MQTT ingress URL |
| `Z2M_MQTT_TOPIC` | No | Z2M base topic (default: `zigbee2mqtt`) |

## Usage

```bash
# Verify your setup before migrating (recommended first step)
zigporter check

# Run the migration wizard (runs checks automatically on first run)
zigporter migrate

# Check migration progress without entering the wizard
zigporter migrate --status

# (Optional) manually export your ZHA device inventory
zigporter export

# (Optional) inspect what's already in Z2M
zigporter list-z2m
```

`zigporter migrate` handles everything automatically on first run:
1. Runs pre-flight checks (HA reachable, ZHA active, Z2M running)
2. Prompts you to back up Home Assistant and your ZHA network
3. Fetches a ZHA export if one is not found, or offers to refresh an existing one
4. Opens the interactive migration wizard

All files are stored in `~/.config/zigporter/` so the tool works from any directory.
Use `--skip-checks` on subsequent runs to skip the pre-flight checks.

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
