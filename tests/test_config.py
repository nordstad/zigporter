import pytest

import zigporter.config
from zigporter.config import load_config, load_z2m_config


@pytest.fixture(autouse=True)
def _reset_env_loaded(monkeypatch, tmp_path):
    """Reset the module-level _env_loaded guard and point config_dir to an empty
    temp directory so the real ~/.config/zigporter/.env never leaks into tests."""
    monkeypatch.setattr(zigporter.config, "_env_loaded", False)
    monkeypatch.setattr(zigporter.config, "config_dir", lambda: tmp_path)


def test_load_config_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HA_URL", "https://ha.test")
    monkeypatch.setenv("HA_TOKEN", "mytoken")
    monkeypatch.delenv("HA_VERIFY_SSL", raising=False)
    monkeypatch.chdir(tmp_path)

    url, token, verify_ssl = load_config()

    assert url == "https://ha.test"
    assert token == "mytoken"
    assert verify_ssl is True


def test_load_config_strips_trailing_slash(monkeypatch, tmp_path):
    monkeypatch.setenv("HA_URL", "https://ha.test/")
    monkeypatch.setenv("HA_TOKEN", "mytoken")
    monkeypatch.chdir(tmp_path)

    url, _, _ = load_config()

    assert url == "https://ha.test"


def test_load_config_verify_ssl_false(monkeypatch, tmp_path):
    monkeypatch.setenv("HA_URL", "https://ha.test")
    monkeypatch.setenv("HA_TOKEN", "mytoken")
    monkeypatch.setenv("HA_VERIFY_SSL", "false")
    monkeypatch.chdir(tmp_path)

    _, _, verify_ssl = load_config()

    assert verify_ssl is False


def test_load_config_missing_url_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("HA_URL", raising=False)
    monkeypatch.setenv("HA_TOKEN", "mytoken")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("zigporter.config.config_dir", lambda: tmp_path)

    with pytest.raises(ValueError, match="HA_URL"):
        load_config()


def test_load_config_missing_token_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("HA_URL", "https://ha.test")
    monkeypatch.delenv("HA_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("zigporter.config.config_dir", lambda: tmp_path)

    with pytest.raises(ValueError, match="HA_TOKEN"):
        load_config()


def test_load_config_from_dotenv_file(tmp_path, monkeypatch):
    monkeypatch.delenv("HA_URL", raising=False)
    monkeypatch.delenv("HA_TOKEN", raising=False)
    monkeypatch.delenv("HA_VERIFY_SSL", raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text("HA_URL=https://ha.dotenv\nHA_TOKEN=dotenvtoken\n")
    monkeypatch.chdir(tmp_path)

    url, token, verify_ssl = load_config()

    assert url == "https://ha.dotenv"
    assert token == "dotenvtoken"
    assert verify_ssl is True


def test_env_var_overrides_dotenv(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("HA_URL=https://ha.dotenv\nHA_TOKEN=dotenvtoken\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HA_URL", "https://ha.override")
    monkeypatch.setenv("HA_TOKEN", "overridetoken")

    url, token, _ = load_config()

    assert url == "https://ha.override"
    assert token == "overridetoken"


def test_load_z2m_config(monkeypatch, tmp_path):
    monkeypatch.setenv("Z2M_URL", "https://ha.test/45df7312_zigbee2mqtt")
    monkeypatch.chdir(tmp_path)

    url, mqtt_topic = load_z2m_config()

    assert url == "https://ha.test/45df7312_zigbee2mqtt"
    assert mqtt_topic == "zigbee2mqtt"


def test_load_z2m_config_strips_trailing_slash(monkeypatch, tmp_path):
    monkeypatch.setenv("Z2M_URL", "https://ha.test/45df7312_zigbee2mqtt/")
    monkeypatch.chdir(tmp_path)

    url, _ = load_z2m_config()

    assert url == "https://ha.test/45df7312_zigbee2mqtt"


def test_load_z2m_config_missing_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("Z2M_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("zigporter.config.config_dir", lambda: tmp_path)

    with pytest.raises(ValueError, match="Z2M_URL"):
        load_z2m_config()


def test_default_convention_path(tmp_path, monkeypatch):
    monkeypatch.setattr("zigporter.config.config_dir", lambda: tmp_path)
    from zigporter.config import default_convention_path

    result = default_convention_path()

    assert result == tmp_path / "naming-convention.json"
