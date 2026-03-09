"""Tests for zigporter.naming_convention."""

from datetime import datetime, timezone

from zigporter.naming_convention import NamingConvention, load_convention, save_convention


def test_load_convention_returns_none_when_file_missing(tmp_path):
    path = tmp_path / "naming-convention.json"
    assert load_convention(path) is None


def test_load_convention_returns_model_when_file_exists(tmp_path):
    path = tmp_path / "naming-convention.json"
    convention = NamingConvention(
        pattern="{area}_{type}_{desc}",
        examples=["kitchen_light_main", "bedroom_sensor_door"],
    )
    path.write_text(convention.model_dump_json())

    result = load_convention(path)

    assert result is not None
    assert result.pattern == "{area}_{type}_{desc}"
    assert result.examples == ["kitchen_light_main", "bedroom_sensor_door"]


def test_save_convention_writes_file(tmp_path):
    path = tmp_path / "naming-convention.json"
    convention = NamingConvention(pattern="{area}_{desc}")

    save_convention(convention, path)

    assert path.exists()
    loaded = load_convention(path)
    assert loaded is not None
    assert loaded.pattern == "{area}_{desc}"


def test_save_convention_updates_timestamp(tmp_path):
    path = tmp_path / "naming-convention.json"
    old_time = datetime(2020, 1, 1, tzinfo=timezone.utc)
    convention = NamingConvention(pattern="{desc}", updated_at=old_time)

    save_convention(convention, path)

    loaded = load_convention(path)
    assert loaded is not None
    assert loaded.updated_at > old_time


def test_naming_convention_defaults(tmp_path):
    convention = NamingConvention(pattern="{area}_{type}")
    assert convention.examples == []
    assert isinstance(convention.updated_at, datetime)
