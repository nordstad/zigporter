# Configuration

## Option 1 — Setup wizard (recommended)

```bash
zigporter setup
```

Prompts for all values and saves to `~/.config/zigporter/.env`.

## Option 2 — Environment variables

Export directly in your shell, add to `~/.zshenv` / `~/.bashrc`, or inject via CI/CD secrets:

```bash
export HA_URL=https://your-ha-instance.local
export HA_TOKEN=your_token
export Z2M_URL=https://your-ha-instance.local/abc123_zigbee2mqtt
```

Shell/OS environment variables always override any `.env` file value.

## Option 3 — CWD `.env` (dev/project override)

A `.env` in your current working directory is loaded first and takes precedence over the
global `~/.config/zigporter/.env`. Useful when running `uv run zigporter` from a project
directory during development.

```bash
# .env (in your project root)
HA_URL=http://homeassistant.local:8123
HA_TOKEN=your_dev_token
Z2M_URL=http://homeassistant.local:8123/api/hassio_ingress/abc123_zigbee2mqtt
```

> **Load order (high → low precedence):**
> 1. Shell/OS environment variables — always win
> 2. CWD `.env` — loaded if present (project/dev override)
> 3. `~/.config/zigporter/.env` — fallback (where `zigporter setup` saves)

## Variables

| Variable | Required | Description |
|---|---|---|
| `HA_URL` | Yes | Home Assistant base URL, e.g. `https://homeassistant.local` |
| `HA_TOKEN` | Yes | Long-Lived Access Token from your HA profile |
| `HA_VERIFY_SSL` | No | `true` (default) or `false` for self-signed certificates |
| `Z2M_URL` | For `migrate` / `list-z2m` | Zigbee2MQTT ingress URL, e.g. `https://homeassistant.local/abc123_zigbee2mqtt` |
| `Z2M_MQTT_TOPIC` | No | Z2M base MQTT topic (default: `zigbee2mqtt`) |

## Getting a Long-Lived Access Token

1. In Home Assistant, go to your profile page (click your username in the sidebar)
2. Scroll to **Long-Lived Access Tokens**
3. Click **Create token**, give it a name, and copy the value

## Finding your Z2M ingress URL

1. In Home Assistant, go to **Settings → Add-ons → Zigbee2MQTT**
2. Click **Open Web UI** — the URL in your browser is your `Z2M_URL`

## Example `~/.config/zigporter/.env`

```dotenv
HA_URL=https://homeassistant.local
HA_TOKEN=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...
HA_VERIFY_SSL=true
Z2M_URL=https://homeassistant.local/45df7312_zigbee2mqtt
Z2M_MQTT_TOPIC=zigbee2mqtt
```
