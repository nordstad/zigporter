import os
from pathlib import Path

from dotenv import load_dotenv


def config_dir() -> Path:
    """Return (and create) the user-level config directory ~/.config/zigporter/."""
    d = Path.home() / ".config" / "zigporter"
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_export_path() -> Path:
    return config_dir() / "zha-export.json"


def default_state_path() -> Path:
    return config_dir() / "migration-state.json"


def backup_confirmed_path() -> Path:
    return config_dir() / ".backup-confirmed"


def _load_env() -> None:
    # CWD .env takes highest precedence; fall back to ~/.config/zigporter/.env
    if not load_dotenv(Path.cwd() / ".env"):
        load_dotenv(config_dir() / ".env")


def load_config() -> tuple[str, str, bool]:
    """Load HA_URL, HA_TOKEN, and HA_VERIFY_SSL from .env or environment variables.

    Environment variables always take precedence over .env file values.

    Returns:
        Tuple of (ha_url, ha_token, verify_ssl)

    Raises:
        ValueError: If HA_URL or HA_TOKEN are missing.
    """
    _load_env()

    ha_url = os.environ.get("HA_URL", "").rstrip("/")
    ha_token = os.environ.get("HA_TOKEN", "")
    verify_ssl = os.environ.get("HA_VERIFY_SSL", "true").lower() != "false"

    if not ha_url:
        raise ValueError("HA_URL is not set. Add it to .env or set it as an environment variable.")
    if not ha_token:
        raise ValueError(
            "HA_TOKEN is not set. Add it to .env or set it as an environment variable."
        )

    return ha_url, ha_token, verify_ssl


def load_z2m_config() -> tuple[str, str]:
    """Load Z2M_URL and Z2M_MQTT_TOPIC from .env or environment variables.

    Returns:
        Tuple of (z2m_url, mqtt_topic). mqtt_topic defaults to "zigbee2mqtt".

    Raises:
        ValueError: If Z2M_URL is missing.
    """
    _load_env()

    z2m_url = os.environ.get("Z2M_URL", "").rstrip("/")
    if not z2m_url:
        raise ValueError("Z2M_URL is not set. Add it to .env or set it as an environment variable.")

    mqtt_topic = os.environ.get("Z2M_MQTT_TOPIC", "zigbee2mqtt")
    return z2m_url, mqtt_topic
