from zigporter.commands.inspect import (
    DashboardRef,
    DeviceDeps,
    _cards_from_view,
    _collect_lovelace_entities,
    _scan_dashboard,
    build_deps,
    show_report,
)
from zigporter.ha_client import YAML_MODE

# ---------------------------------------------------------------------------
# Lovelace walker
# ---------------------------------------------------------------------------


def test_collect_lovelace_entities_string_entity():
    assert _collect_lovelace_entities("switch.kitchen_plug") == {"switch.kitchen_plug"}


def test_collect_lovelace_entities_entity_key():
    card = {"type": "button", "entity": "switch.kitchen_plug"}
    assert "switch.kitchen_plug" in _collect_lovelace_entities(card)


def test_collect_lovelace_entities_entity_id_key():
    card = {"type": "sensor", "entity_id": "sensor.power"}
    assert "sensor.power" in _collect_lovelace_entities(card)


def test_collect_lovelace_entities_list_of_strings():
    card = {"type": "glance", "entities": ["switch.a", "sensor.b"]}
    result = _collect_lovelace_entities(card)
    assert "switch.a" in result
    assert "sensor.b" in result


def test_collect_lovelace_entities_list_of_objects():
    card = {
        "type": "entities",
        "entities": [
            {"entity": "switch.a"},
            {"entity": "sensor.b", "name": "Override"},
        ],
    }
    result = _collect_lovelace_entities(card)
    assert "switch.a" in result
    assert "sensor.b" in result


def test_collect_lovelace_entities_nested_stack():
    card = {
        "type": "vertical-stack",
        "cards": [
            {"type": "button", "entity": "switch.a"},
            {"type": "sensor", "entity": "sensor.b"},
        ],
    }
    result = _collect_lovelace_entities(card)
    assert "switch.a" in result
    assert "sensor.b" in result


def test_collect_lovelace_entities_apexcharts_series():
    """custom:apexcharts-card uses series[*].entity — must be found recursively."""
    card = {
        "type": "custom:apexcharts-card",
        "series": [
            {"entity": "sensor.kontor_temp_sensor_temperature", "name": "Kontor"},
            {"entity": "sensor.vardagsrum_temp", "name": "Vardagsrum"},
        ],
    }
    result = _collect_lovelace_entities(card)
    assert "sensor.kontor_temp_sensor_temperature" in result
    assert "sensor.vardagsrum_temp" in result


def test_collect_lovelace_entities_ignores_urls():
    card = {"type": "picture", "image": "http://cam.local/snapshot"}
    result = _collect_lovelace_entities(card)
    assert not any("http" in e for e in result)


# ---------------------------------------------------------------------------
# Dashboard scanner
# ---------------------------------------------------------------------------


def test_cards_from_view_classic_layout():
    view = {"title": "Home", "cards": [{"type": "button", "entity": "switch.a"}]}
    cards = _cards_from_view(view)
    assert len(cards) == 1


def test_cards_from_view_sections_layout():
    view = {
        "title": "Home",
        "sections": [
            {"cards": [{"type": "button", "entity": "switch.a"}]},
            {"cards": [{"type": "sensor", "entity": "sensor.b"}]},
        ],
    }
    cards = _cards_from_view(view)
    assert len(cards) == 2


def test_cards_from_view_mixed_layouts():
    view = {
        "cards": [{"type": "button", "entity": "switch.a"}],
        "sections": [{"cards": [{"type": "sensor", "entity": "sensor.b"}]}],
    }
    cards = _cards_from_view(view)
    assert len(cards) == 2


def test_scan_dashboard_sections_layout():
    """Cards inside view.sections are found (new HA dashboard layout)."""
    config = {
        "views": [
            {
                "title": "Home",
                "sections": [
                    {
                        "cards": [
                            {
                                "type": "custom:apexcharts-card",
                                "series": [{"entity": "sensor.kontor_temp_sensor_temperature"}],
                            }
                        ]
                    }
                ],
            }
        ]
    }
    refs = _scan_dashboard(config, "Mushroom", {"sensor.kontor_temp_sensor_temperature"})
    assert len(refs) == 1
    assert refs[0].card_type == "custom:apexcharts-card"
    assert "sensor.kontor_temp_sensor_temperature" in refs[0].matched_entities


def test_scan_dashboard_finds_matching_card():
    config = {
        "views": [
            {
                "title": "Home",
                "cards": [
                    {"type": "entities", "entities": ["switch.kitchen_plug", "light.hall"]},
                ],
            }
        ]
    }
    refs = _scan_dashboard(config, "Default", {"switch.kitchen_plug"})
    assert len(refs) == 1
    assert refs[0].view_title == "Home"
    assert refs[0].card_type == "entities"
    assert "switch.kitchen_plug" in refs[0].matched_entities


def test_scan_dashboard_skips_non_matching_cards():
    config = {
        "views": [
            {
                "title": "Home",
                "cards": [
                    {"type": "button", "entity": "light.hall"},
                ],
            }
        ]
    }
    refs = _scan_dashboard(config, "Default", {"switch.kitchen_plug"})
    assert refs == []


def test_scan_dashboard_includes_card_title():
    config = {
        "views": [
            {
                "title": "Overview",
                "cards": [
                    {"type": "entities", "title": "My Plugs", "entities": ["switch.kitchen_plug"]},
                ],
            }
        ]
    }
    refs = _scan_dashboard(config, "Office", {"switch.kitchen_plug"})
    assert refs[0].card_title == "My Plugs"
    assert refs[0].dashboard_title == "Office"


# ---------------------------------------------------------------------------
# build_deps
# ---------------------------------------------------------------------------

_BASE_DATA: dict = {
    "zha_devices": [
        {
            "ieee": "00:11:22:33:44:55:66:77",
            "device_reg_id": "dev-abc",
            "user_given_name": "Kitchen Plug",
            "name": "Kitchen Plug",
            "manufacturer": "IKEA",
            "model": "E1603",
        }
    ],
    "entity_registry": [
        {"entity_id": "switch.kitchen_plug", "device_id": "dev-abc", "platform": "zha"},
        {"entity_id": "sensor.kitchen_plug_power", "device_id": "dev-abc", "platform": "zha"},
    ],
    "device_registry": [{"id": "dev-abc", "area_id": "kitchen"}],
    "area_registry": [{"area_id": "kitchen", "name": "Kitchen"}],
    "automation_configs": [
        {
            "id": "auto1",
            "alias": "Morning routine",
            "action": [{"service": "switch.turn_on", "entity_id": "switch.kitchen_plug"}],
        },
        {
            "id": "auto2",
            "alias": "Unrelated",
            "action": [{"service": "light.turn_on", "entity_id": "light.hall"}],
        },
    ],
    "scripts": [
        {
            "id": "script1",
            "alias": "Turn on kitchen",
            "sequence": [{"service": "switch.turn_on", "entity_id": "switch.kitchen_plug"}],
        }
    ],
    "scenes": [
        {
            "id": "scene1",
            "name": "Kitchen evening",
            "entities": {"switch.kitchen_plug": {"state": "on"}},
        },
        {
            "id": "scene2",
            "name": "Living room",
            "entities": {"light.hall": {"state": "on"}},
        },
    ],
    "lovelace": [
        (
            None,
            {
                "views": [
                    {
                        "title": "Home",
                        "cards": [
                            {
                                "type": "entities",
                                "title": "My Devices",
                                "entities": ["switch.kitchen_plug"],
                            }
                        ],
                    }
                ]
            },
        ),
    ],
    "dashboard_titles": {None: "Default"},
}


def test_build_deps_returns_correct_device():
    deps = build_deps("00:11:22:33:44:55:66:77", _BASE_DATA)
    assert deps is not None
    assert deps.name == "Kitchen Plug"
    assert deps.area_name == "Kitchen"
    assert deps.model == "E1603"


def test_build_deps_entities():
    deps = build_deps("00:11:22:33:44:55:66:77", _BASE_DATA)
    assert deps is not None
    assert "switch.kitchen_plug" in deps.entities
    assert "sensor.kitchen_plug_power" in deps.entities


def test_build_deps_only_matching_automations():
    deps = build_deps("00:11:22:33:44:55:66:77", _BASE_DATA)
    assert deps is not None
    assert len(deps.automations) == 1
    assert deps.automations[0]["alias"] == "Morning routine"


def test_build_deps_only_matching_scripts():
    deps = build_deps("00:11:22:33:44:55:66:77", _BASE_DATA)
    assert deps is not None
    assert len(deps.scripts) == 1
    assert deps.scripts[0]["alias"] == "Turn on kitchen"


def test_build_deps_only_matching_scenes():
    deps = build_deps("00:11:22:33:44:55:66:77", _BASE_DATA)
    assert deps is not None
    assert len(deps.scenes) == 1
    assert deps.scenes[0]["name"] == "Kitchen evening"


def test_build_deps_dashboard_refs():
    deps = build_deps("00:11:22:33:44:55:66:77", _BASE_DATA)
    assert deps is not None
    assert len(deps.dashboard_refs) == 1
    assert deps.dashboard_refs[0].dashboard_title == "Default"
    assert deps.dashboard_refs[0].view_title == "Home"
    assert deps.dashboard_refs[0].card_title == "My Devices"


def test_build_deps_unknown_ieee_returns_none():
    deps = build_deps("ff:ff:ff:ff:ff:ff:ff:ff", _BASE_DATA)
    assert deps is None


# ---------------------------------------------------------------------------
# show_report (smoke test — should not raise)
# ---------------------------------------------------------------------------


def test_show_report_full():
    deps = DeviceDeps(
        ieee="00:11:22:33:44:55:66:77",
        name="Kitchen Plug",
        manufacturer="IKEA",
        model="E1603",
        area_name="Kitchen",
        entities=["switch.kitchen_plug", "sensor.kitchen_plug_power"],
        automations=[
            {
                "alias": "Morning routine",
                "action": [{"entity_id": "switch.kitchen_plug"}],
            }
        ],
        scripts=[
            {
                "alias": "Turn on kitchen",
                "sequence": [{"entity_id": "switch.kitchen_plug"}],
            }
        ],
        scenes=[
            {
                "name": "Kitchen evening",
                "entities": {"switch.kitchen_plug": {"state": "on"}},
            }
        ],
        dashboard_refs=[
            DashboardRef(
                dashboard_title="Default",
                view_title="Home",
                card_type="entities",
                card_title="My Devices",
                matched_entities=["switch.kitchen_plug"],
            )
        ],
    )
    show_report(deps)  # must not raise


def test_show_report_no_deps():
    deps = DeviceDeps(
        ieee="00:11:22:33:44:55:66:77",
        name="Bare Device",
        manufacturer=None,
        model=None,
        area_name=None,
        entities=["switch.bare"],
        automations=[],
        scripts=[],
        scenes=[],
        dashboard_refs=[],
    )
    show_report(deps)  # must not raise


# ---------------------------------------------------------------------------
# show_migrate_inspect_summary
# ---------------------------------------------------------------------------


async def test_show_migrate_inspect_summary_empty_entity_ids():
    """Empty entity_ids list returns immediately without calling ha_client."""
    from unittest.mock import AsyncMock, MagicMock  # noqa: PLC0415

    from zigporter.commands.inspect import show_migrate_inspect_summary  # noqa: PLC0415

    ha_client = MagicMock()
    ha_client.get_panels = AsyncMock(return_value={})
    await show_migrate_inspect_summary([], ha_client)
    ha_client.get_panels.assert_not_called()


async def test_show_migrate_inspect_summary_with_matching_dashboard():
    """Shows entities list and dashboard cards that reference them."""
    from unittest.mock import AsyncMock, MagicMock  # noqa: PLC0415

    from zigporter.commands.inspect import show_migrate_inspect_summary  # noqa: PLC0415

    ha_client = MagicMock()
    ha_client.get_panels = AsyncMock(return_value={})
    ha_client.get_lovelace_config = AsyncMock(
        return_value={
            "views": [
                {
                    "title": "Main",
                    "cards": [{"type": "entities", "entities": ["switch.kitchen_plug"]}],
                }
            ]
        }
    )

    await show_migrate_inspect_summary(["switch.kitchen_plug"], ha_client)

    ha_client.get_panels.assert_called_once()
    ha_client.get_lovelace_config.assert_called_once_with(None)


async def test_show_migrate_inspect_summary_no_matching_dashboard():
    """Shows 'No dashboard cards' message when no cards reference the entities."""
    from unittest.mock import AsyncMock, MagicMock  # noqa: PLC0415

    from zigporter.commands.inspect import show_migrate_inspect_summary  # noqa: PLC0415

    ha_client = MagicMock()
    ha_client.get_panels = AsyncMock(return_value={})
    ha_client.get_lovelace_config = AsyncMock(
        return_value={
            "views": [{"title": "Home", "cards": [{"type": "button", "entity": "light.other"}]}]
        }
    )

    await show_migrate_inspect_summary(["switch.kitchen_plug"], ha_client)

    ha_client.get_lovelace_config.assert_called_once_with(None)


async def test_show_migrate_inspect_summary_discovers_extra_dashboards():
    """Extra Lovelace dashboards discovered from panels are also scanned."""
    from unittest.mock import AsyncMock, MagicMock  # noqa: PLC0415

    from zigporter.commands.inspect import show_migrate_inspect_summary  # noqa: PLC0415

    ha_client = MagicMock()
    ha_client.get_panels = AsyncMock(
        return_value={
            "lovelace": {"component_name": "lovelace", "url_path": ""},
            "mobile": {"component_name": "lovelace", "url_path": "mobile"},
        }
    )
    ha_client.get_lovelace_config = AsyncMock(return_value=None)

    await show_migrate_inspect_summary(["switch.kitchen_plug"], ha_client)

    # default (None) + "mobile"
    assert ha_client.get_lovelace_config.call_count == 2


# ---------------------------------------------------------------------------
# YAML_MODE sentinel handling
# ---------------------------------------------------------------------------


def test_build_deps_yaml_mode_dashboard_does_not_raise():
    """build_deps must not crash when a lovelace entry is the YAML_MODE sentinel."""
    data = {
        **_BASE_DATA,
        "lovelace": [(None, YAML_MODE)],
    }
    deps = build_deps("00:11:22:33:44:55:66:77", data)
    assert deps is not None
    # YAML-mode dashboard is silently skipped — no dashboard refs
    assert deps.dashboard_refs == []


def test_build_deps_mixed_yaml_mode_and_real_dashboard():
    """Only real configs are scanned; YAML_MODE entries are skipped."""
    real_config = {
        "views": [
            {
                "title": "Home",
                "cards": [{"type": "entities", "entities": ["switch.kitchen_plug"]}],
            }
        ]
    }
    data = {
        **_BASE_DATA,
        "lovelace": [
            ("yaml-dash", YAML_MODE),
            ("real-dash", real_config),
        ],
        "dashboard_titles": {"yaml-dash": "YAML Dash", "real-dash": "Real Dash"},
    }
    deps = build_deps("00:11:22:33:44:55:66:77", data)
    assert deps is not None
    assert len(deps.dashboard_refs) == 1
    assert deps.dashboard_refs[0].dashboard_title == "Real Dash"


async def test_show_migrate_inspect_summary_yaml_mode_does_not_raise():
    """show_migrate_inspect_summary must not crash when get_lovelace_config returns YAML_MODE."""
    from unittest.mock import AsyncMock, MagicMock  # noqa: PLC0415

    from zigporter.commands.inspect import show_migrate_inspect_summary  # noqa: PLC0415

    ha_client = MagicMock()
    ha_client.get_panels = AsyncMock(return_value={})
    ha_client.get_lovelace_config = AsyncMock(return_value=YAML_MODE)

    # Must not raise AttributeError: '_YamlMode' object has no attribute 'get'
    await show_migrate_inspect_summary(["switch.kitchen_plug"], ha_client)

    ha_client.get_lovelace_config.assert_called_once_with(None)


async def test_show_migrate_inspect_summary_yaml_mode_skipped_multiple_dashboards():
    """YAML_MODE dashboards are skipped; real ones are still scanned."""
    from unittest.mock import AsyncMock, MagicMock  # noqa: PLC0415

    from zigporter.commands.inspect import show_migrate_inspect_summary  # noqa: PLC0415

    real_config = {
        "views": [
            {
                "title": "Main",
                "cards": [{"type": "entities", "entities": ["switch.kitchen_plug"]}],
            }
        ]
    }

    ha_client = MagicMock()
    ha_client.get_panels = AsyncMock(
        return_value={
            "lovelace": {"component_name": "lovelace", "url_path": ""},
            "mobile": {"component_name": "lovelace", "url_path": "mobile"},
        }
    )
    # First call (default) returns YAML_MODE; second call (mobile) returns a real config
    ha_client.get_lovelace_config = AsyncMock(side_effect=[YAML_MODE, real_config])

    # Must not raise
    await show_migrate_inspect_summary(["switch.kitchen_plug"], ha_client)

    assert ha_client.get_lovelace_config.call_count == 2
