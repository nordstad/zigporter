# Installation

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Home Assistant with ZHA and Zigbee2MQTT add-on running

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
