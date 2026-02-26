# zigporter

**zigporter** is a CLI tool for migrating Zigbee devices from ZHA to Zigbee2MQTT in Home Assistant.

It runs an interactive per-device wizard with persistent state — migrations can be paused and resumed across sessions without losing progress.

## Features

- **Per-device wizard** — step-by-step guidance through removal, reset, pairing, rename, and validation
- **Persistent state** — progress is saved to disk after every step; resume anytime
- **Smart Z2M auth** — three-tier authentication fallback (bearer token → ingress cookie → HA-native)
- **Area & entity restore** — automatically re-applies ZHA device names, areas, and entity IDs in HA

## Installation

```bash
uv tool install zigporter
```

## Quick start

```bash
# 1. Export your ZHA device inventory
zigporter export

# 2. Run the migration wizard
zigporter migrate zha-export-*.json
```

See [Installation](getting-started/installation.md) and [Configuration](getting-started/configuration.md) to get set up.
