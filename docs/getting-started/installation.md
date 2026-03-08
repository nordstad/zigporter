# Installation

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Home Assistant with ZHA and Zigbee2MQTT add-on running

## Confirmed Working

**Platform**

| | |
|---|---|
| OS | Linux, macOS, Windows 11 |

**Software**

| | Version |
|---|---|
| Home Assistant OS | 2026.2.3 |
| HA Supervisor | 2026.02.2 |
| HA install type | HA OS |
| Zigbee2MQTT | 2.8.0-1 |
| Python | 3.12, 3.13, 3.14 |

Running a different version? [Submit a compatibility report](https://github.com/nordstad/zigporter/issues/new?template=compatibility_report.md) to let us know.

## Install via uv

```bash
uv tool install zigporter
```

## Install via pip

```bash
pip install zigporter
```

## Install from source

```bash
git clone https://github.com/nordstad/zigporter.git
cd zigporter
uv sync
uv run zigporter --help
```

## Verify installation

```bash
zigporter --version
zigporter --help
```
