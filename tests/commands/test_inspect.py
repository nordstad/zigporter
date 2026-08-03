from unittest.mock import AsyncMock

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
    deps = build_deps("dev-abc", _BASE_DATA)
    assert deps is not None
    assert deps.name == "Kitchen Plug"
    assert deps.area_name == "Kitchen"
    assert deps.model == "E1603"


def test_build_deps_entities():
    deps = build_deps("dev-abc", _BASE_DATA)
    assert deps is not None
    assert "switch.kitchen_plug" in deps.entities
    assert "sensor.kitchen_plug_power" in deps.entities


def test_build_deps_only_matching_automations():
    deps = build_deps("dev-abc", _BASE_DATA)
    assert deps is not None
    assert len(deps.automations) == 1
    assert deps.automations[0]["alias"] == "Morning routine"


def test_build_deps_only_matching_scripts():
    deps = build_deps("dev-abc", _BASE_DATA)
    assert deps is not None
    assert len(deps.scripts) == 1
    assert deps.scripts[0]["alias"] == "Turn on kitchen"


def test_build_deps_only_matching_scenes():
    deps = build_deps("dev-abc", _BASE_DATA)
    assert deps is not None
    assert len(deps.scenes) == 1
    assert deps.scenes[0]["name"] == "Kitchen evening"


def test_build_deps_dashboard_refs():
    deps = build_deps("dev-abc", _BASE_DATA)
    assert deps is not None
    assert len(deps.dashboard_refs) == 1
    assert deps.dashboard_refs[0].dashboard_title == "Default"
    assert deps.dashboard_refs[0].view_title == "Home"
    assert deps.dashboard_refs[0].card_title == "My Devices"


def test_build_deps_unknown_device_id_returns_none():
    deps = build_deps("nonexistent-device-id", _BASE_DATA)
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
    from unittest.mock import MagicMock

    from zigporter.commands.inspect import show_migrate_inspect_summary

    ha_client = MagicMock()
    ha_client.get_panels = AsyncMock(return_value={})
    await show_migrate_inspect_summary([], ha_client)
    ha_client.get_panels.assert_not_called()


async def test_show_migrate_inspect_summary_with_matching_dashboard():
    """Shows entities list and dashboard cards that reference them."""
    from unittest.mock import MagicMock

    from zigporter.commands.inspect import show_migrate_inspect_summary

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
    from unittest.mock import MagicMock

    from zigporter.commands.inspect import show_migrate_inspect_summary

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
    from unittest.mock import MagicMock

    from zigporter.commands.inspect import show_migrate_inspect_summary

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
    deps = build_deps("dev-abc", data)
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
    deps = build_deps("dev-abc", data)
    assert deps is not None
    assert len(deps.dashboard_refs) == 1
    assert deps.dashboard_refs[0].dashboard_title == "Real Dash"


async def test_show_migrate_inspect_summary_yaml_mode_does_not_raise():
    """show_migrate_inspect_summary must not crash when get_lovelace_config returns YAML_MODE."""
    from unittest.mock import MagicMock

    from zigporter.commands.inspect import show_migrate_inspect_summary

    ha_client = MagicMock()
    ha_client.get_panels = AsyncMock(return_value={})
    ha_client.get_lovelace_config = AsyncMock(return_value=YAML_MODE)

    # Must not raise AttributeError: '_YamlMode' object has no attribute 'get'
    await show_migrate_inspect_summary(["switch.kitchen_plug"], ha_client)

    ha_client.get_lovelace_config.assert_called_once_with(None)


async def test_show_migrate_inspect_summary_yaml_mode_skipped_multiple_dashboards():
    """YAML_MODE dashboards are skipped; real ones are still scanned."""
    from unittest.mock import MagicMock

    from zigporter.commands.inspect import show_migrate_inspect_summary

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


# ---------------------------------------------------------------------------
# _resolve_device_arg
# ---------------------------------------------------------------------------

# _BASE_DATA ZHA device: ieee="00:11:22:33:44:55:66:77", device_reg_id="dev-abc",
# entity switch.kitchen_plug has device_id="dev-abc".
# _resolve_device_arg now returns HA device IDs (not IEEE addresses).


def test_resolve_device_arg_by_entity_id():
    from zigporter.commands.inspect import _resolve_device_arg

    matches = _resolve_device_arg("switch.kitchen_plug", _BASE_DATA, "zha")
    assert matches == ["dev-abc"]


def test_resolve_device_arg_by_entity_id_no_match():
    from zigporter.commands.inspect import _resolve_device_arg

    matches = _resolve_device_arg("sensor.nonexistent", _BASE_DATA, "zha")
    assert matches == []


def test_resolve_device_arg_by_ieee_0x_prefix():
    from zigporter.commands.inspect import _resolve_device_arg

    matches = _resolve_device_arg("0x0011223344556677", _BASE_DATA, "zha")
    assert matches == ["dev-abc"]


def test_resolve_device_arg_by_ieee_colon():
    from zigporter.commands.inspect import _resolve_device_arg

    matches = _resolve_device_arg("00:11:22:33:44:55:66:77", _BASE_DATA, "zha")
    assert matches == ["dev-abc"]


def test_resolve_device_arg_by_ieee_no_match():
    from zigporter.commands.inspect import _resolve_device_arg

    matches = _resolve_device_arg("0xffffffffffffffff", _BASE_DATA, "zha")
    assert matches == []


def test_resolve_device_arg_by_name_partial():
    from zigporter.commands.inspect import _resolve_device_arg

    matches = _resolve_device_arg("Kitchen", _BASE_DATA, "zha")
    assert matches == ["dev-abc"]


def test_resolve_device_arg_by_name_exact():
    from zigporter.commands.inspect import _resolve_device_arg

    matches = _resolve_device_arg("Kitchen Plug", _BASE_DATA, "zha")
    assert matches == ["dev-abc"]


def test_resolve_device_arg_by_name_case_insensitive():
    from zigporter.commands.inspect import _resolve_device_arg

    matches = _resolve_device_arg("kitchen plug", _BASE_DATA, "zha")
    assert matches == ["dev-abc"]


def test_resolve_device_arg_by_name_no_match():
    from zigporter.commands.inspect import _resolve_device_arg

    matches = _resolve_device_arg("Bedroom Sensor", _BASE_DATA, "zha")
    assert matches == []


def test_resolve_device_arg_ambiguous_name():
    from zigporter.commands.inspect import _resolve_device_arg

    data = {
        **_BASE_DATA,
        "zha_devices": [
            {"ieee": "aaa", "device_reg_id": "d1", "name": "Kitchen Plug"},
            {"ieee": "bbb", "device_reg_id": "d2", "name": "Kitchen Sensor"},
        ],
        "device_registry": [
            {"id": "d1", "area_id": "kitchen"},
            {"id": "d2", "area_id": "kitchen"},
        ],
    }
    matches = _resolve_device_arg("Kitchen", data, "zha")
    assert len(matches) == 2


_ZHA_DEVICE_HA_ID = "b0e705604fe68bb74dadec3ae697af63"
_OTHER_DEVICE_HA_ID = "aaaa0000bbbb1111cccc2222dddd3333"


def test_resolve_device_arg_by_ha_device_id():
    """32-char hex HA device ID is resolved directly without name/IEEE matching."""
    from zigporter.commands.inspect import _resolve_device_arg

    matches = _resolve_device_arg(
        _ZHA_DEVICE_HA_ID,
        {
            **_BASE_DATA,
            "device_registry": [{"id": _ZHA_DEVICE_HA_ID, "area_id": None}],
            "zha_devices": [
                {
                    "ieee": "00:11:22:33:44:55:66:77",
                    "device_reg_id": _ZHA_DEVICE_HA_ID,
                    "name": "Kitchen Plug",
                }
            ],
        },
        "zha",
    )
    assert matches == [_ZHA_DEVICE_HA_ID]


def test_resolve_device_arg_by_ha_device_id_not_backend():
    """HA device ID that exists but belongs to a different backend returns __not_backend__."""
    from zigporter.commands.inspect import _resolve_device_arg

    data = {
        **_BASE_DATA,
        "device_registry": [
            {"id": "dev-abc", "area_id": "kitchen"},
            {"id": _OTHER_DEVICE_HA_ID, "name": "Apple TV", "area_id": None},
        ],
    }
    matches = _resolve_device_arg(_OTHER_DEVICE_HA_ID, data, "zha")
    assert matches == ["__not_backend__"]


def test_resolve_device_arg_non_zha_entity_returns_not_backend():
    """Entity that exists but belongs to a non-ZHA device returns __not_backend__."""
    from zigporter.commands.inspect import _resolve_device_arg

    data = {
        **_BASE_DATA,
        "entity_registry": [
            *_BASE_DATA["entity_registry"],
            {
                "entity_id": "device_tracker.apple_tv",
                "device_id": "dev-appletv",
                "platform": "apple_tv",
            },
        ],
        "device_registry": [
            {"id": "dev-abc", "area_id": "kitchen"},
            {"id": "dev-appletv", "name": "Apple TV", "area_id": None},
        ],
    }
    matches = _resolve_device_arg("device_tracker.apple_tv", data, "zha")
    assert matches == ["__not_backend__"]


def test_resolve_device_arg_non_zha_name_returns_not_backend():
    """Name that matches a non-ZHA device returns __not_backend__."""
    from zigporter.commands.inspect import _resolve_device_arg

    data = {
        **_BASE_DATA,
        "device_registry": [
            {"id": "dev-abc", "area_id": "kitchen"},
            {"id": "dev-appletv", "name": "Apple TV 4K", "area_id": None},
        ],
    }
    matches = _resolve_device_arg("Apple TV", data, "zha")
    assert matches == ["__not_backend__"]


def test_resolve_device_arg_backend_all_finds_any_device():
    """backend='all' matches non-ZHA devices too."""
    from zigporter.commands.inspect import _resolve_device_arg

    data = {
        **_BASE_DATA,
        "device_registry": [
            {"id": "dev-abc", "area_id": "kitchen"},
            {"id": "dev-appletv", "name": "Apple TV 4K", "area_id": None},
        ],
    }
    matches = _resolve_device_arg("Apple TV", data, "all")
    assert matches == ["dev-appletv"]


# ---------------------------------------------------------------------------
# run_inspect headless mode
# ---------------------------------------------------------------------------


async def test_run_inspect_headless_not_found(mocker, capsys):
    from zigporter.commands.inspect import run_inspect

    mock_ha = mocker.AsyncMock()
    mocker.patch("zigporter.commands.inspect.HAClient", return_value=mock_ha)
    mocker.patch(
        "zigporter.commands.inspect.fetch_all_data",
        new=AsyncMock(return_value={**_BASE_DATA}),
    )
    mock_pick = mocker.patch("zigporter.commands.inspect._pick_device")

    await run_inspect("url", "token", False, device="Nonexistent Device")

    mock_pick.assert_not_called()


async def test_run_inspect_headless_ambiguous(mocker):
    from zigporter.commands.inspect import run_inspect

    ambiguous_data = {
        **_BASE_DATA,
        "zha_devices": [
            {"ieee": "aaa", "device_reg_id": "d1", "name": "Kitchen Plug", "user_given_name": None},
            {
                "ieee": "bbb",
                "device_reg_id": "d2",
                "name": "Kitchen Sensor",
                "user_given_name": None,
            },
        ],
        "device_registry": [
            {"id": "d1", "area_id": "kitchen"},
            {"id": "d2", "area_id": "kitchen"},
        ],
    }
    mock_ha = mocker.AsyncMock()
    mocker.patch("zigporter.commands.inspect.HAClient", return_value=mock_ha)
    mocker.patch(
        "zigporter.commands.inspect.fetch_all_data",
        new=AsyncMock(return_value=ambiguous_data),
    )
    mock_show = mocker.patch("zigporter.commands.inspect.show_report")
    mock_pick = mocker.patch("zigporter.commands.inspect._pick_device")

    await run_inspect("url", "token", False, device="Kitchen")

    mock_show.assert_not_called()
    mock_pick.assert_not_called()


async def test_run_inspect_json_output(mocker, capsys):
    from zigporter.commands.inspect import run_inspect

    mock_ha = mocker.AsyncMock()
    mocker.patch("zigporter.commands.inspect.HAClient", return_value=mock_ha)
    mocker.patch(
        "zigporter.commands.inspect.fetch_all_data",
        new=AsyncMock(return_value={**_BASE_DATA}),
    )
    mocker.patch("zigporter.commands.inspect._pick_device")

    await run_inspect("url", "token", False, device="switch.kitchen_plug", json_output=True)

    import json

    out = json.loads(capsys.readouterr().out)
    assert out["name"] == "Kitchen Plug"
    assert "switch.kitchen_plug" in out["entities"]
    assert "automations" in out
    assert "dashboard_refs" in out


async def test_run_inspect_headless_skips_picker_and_shows_report(mocker):
    from zigporter.commands.inspect import run_inspect

    mock_ha = mocker.AsyncMock()
    mocker.patch("zigporter.commands.inspect.HAClient", return_value=mock_ha)
    mocker.patch(
        "zigporter.commands.inspect.fetch_all_data",
        new=AsyncMock(return_value={**_BASE_DATA}),
    )
    mock_show = mocker.patch("zigporter.commands.inspect.show_report")
    mock_pick = mocker.patch("zigporter.commands.inspect._pick_device")

    await run_inspect("url", "token", False, device="switch.kitchen_plug")

    mock_pick.assert_not_called()
    mock_show.assert_called_once()


# ---------------------------------------------------------------------------
# fix 1: _filter_by_backend z2m uses parse_z2m_ieee_identifier
# ---------------------------------------------------------------------------


def test_filter_by_backend_z2m_accepts_standard_zigbee2mqtt_prefix():
    """Identifiers with 'zigbee2mqtt_0x...' prefix are accepted."""
    from zigporter.commands.inspect import _filter_by_backend

    dr = [
        {"id": "dev1", "identifiers": [["mqtt", "zigbee2mqtt_0x0011223344556677"]]},
        {"id": "dev2", "identifiers": [["zha", "00:11:22:33:44:55:66:88"]]},
    ]
    result = _filter_by_backend(dr, [], "z2m")
    assert [d["id"] for d in result] == ["dev1"]


def test_filter_by_backend_z2m_accepts_bare_ieee_mqtt_identifier():
    """Bare IEEE hex MQTT identifiers (no 'zigbee2mqtt' prefix) are accepted."""
    from zigporter.commands.inspect import _filter_by_backend

    dr = [{"id": "dev1", "identifiers": [["mqtt", "0011223344556677"]]}]
    result = _filter_by_backend(dr, [], "z2m")
    assert [d["id"] for d in result] == ["dev1"]


def test_filter_by_backend_z2m_accepts_0x_prefixed_mqtt_identifier():
    """MQTT identifiers with 0x-prefixed IEEE (no zigbee2mqtt_ prefix) are accepted."""
    from zigporter.commands.inspect import _filter_by_backend

    dr = [{"id": "dev1", "identifiers": [["mqtt", "0x0011223344556677"]]}]
    result = _filter_by_backend(dr, [], "z2m")
    assert [d["id"] for d in result] == ["dev1"]


def test_filter_by_backend_z2m_rejects_non_ieee_mqtt_identifier():
    """MQTT identifiers that are not parseable as IEEE addresses are excluded."""
    from zigporter.commands.inspect import _filter_by_backend

    dr = [{"id": "dev1", "identifiers": [["mqtt", "some_other_integration_device"]]}]
    result = _filter_by_backend(dr, [], "z2m")
    assert result == []


# ---------------------------------------------------------------------------
# fix 2: --json suppresses --debug output
# ---------------------------------------------------------------------------


async def test_run_inspect_json_debug_produces_clean_json(mocker, capsys):
    """--json --debug must not write debug text to stdout; output must be valid JSON."""
    from zigporter.commands.inspect import run_inspect

    mock_ha = mocker.AsyncMock()
    mocker.patch("zigporter.commands.inspect.HAClient", return_value=mock_ha)
    mocker.patch(
        "zigporter.commands.inspect.fetch_all_data",
        new=AsyncMock(return_value={**_BASE_DATA, "_panels_data": {}}),
    )
    mocker.patch("zigporter.commands.inspect._pick_device")

    await run_inspect(
        "url", "token", False, device="switch.kitchen_plug", json_output=True, debug=True
    )

    import json

    captured = capsys.readouterr()
    # Must parse cleanly — no debug text mixed in
    out = json.loads(captured.out)
    assert out["name"] == "Kitchen Plug"


# ---------------------------------------------------------------------------
# fix 3: invalid --backend fails fast
# ---------------------------------------------------------------------------


async def test_run_inspect_invalid_backend_errors(mocker, capsys):
    """Unknown --backend value must print an error and return without calling HA."""
    from zigporter.commands.inspect import run_inspect

    mock_ha = mocker.AsyncMock()
    mocker.patch("zigporter.commands.inspect.HAClient", return_value=mock_ha)
    fetch_mock = mocker.patch(
        "zigporter.commands.inspect.fetch_all_data",
        new=AsyncMock(return_value={**_BASE_DATA}),
    )

    await run_inspect("url", "token", False, device="switch.kitchen_plug", backend="zhm")

    fetch_mock.assert_not_called()


# ---------------------------------------------------------------------------
# run_inspect — __not_backend__ paths
# ---------------------------------------------------------------------------


async def test_run_inspect_not_backend_zha_prints_integration_hint(mocker):
    """__not_backend__ with backend='zha' prints a hint about --backend all."""
    from zigporter.commands.inspect import run_inspect

    mock_ha = mocker.AsyncMock()
    mocker.patch("zigporter.commands.inspect.HAClient", return_value=mock_ha)
    mocker.patch(
        "zigporter.commands.inspect.fetch_all_data",
        new=AsyncMock(return_value={**_BASE_DATA}),
    )
    mocker.patch(
        "zigporter.commands.inspect._resolve_device_arg",
        return_value=["__not_backend__"],
    )
    mock_show = mocker.patch("zigporter.commands.inspect.show_report")

    await run_inspect("url", "token", False, device="some.entity", backend="zha")

    mock_show.assert_not_called()


async def test_run_inspect_not_backend_all_prints_not_found(mocker):
    """__not_backend__ with backend='all' prints device not found (no integration hint)."""
    from zigporter.commands.inspect import run_inspect

    mock_ha = mocker.AsyncMock()
    mocker.patch("zigporter.commands.inspect.HAClient", return_value=mock_ha)
    mocker.patch(
        "zigporter.commands.inspect.fetch_all_data",
        new=AsyncMock(return_value={**_BASE_DATA}),
    )
    mocker.patch(
        "zigporter.commands.inspect._resolve_device_arg",
        return_value=["__not_backend__"],
    )
    mock_show = mocker.patch("zigporter.commands.inspect.show_report")

    await run_inspect("url", "token", False, device="some.entity", backend="all")

    mock_show.assert_not_called()


# ---------------------------------------------------------------------------
# run_inspect — interactive picker paths
# ---------------------------------------------------------------------------


async def test_run_inspect_interactive_picker_returns_none(mocker):
    """When device is None and the picker returns None, show_report is not called."""
    from zigporter.commands.inspect import run_inspect

    mock_ha = mocker.AsyncMock()
    mocker.patch("zigporter.commands.inspect.HAClient", return_value=mock_ha)
    mocker.patch(
        "zigporter.commands.inspect.fetch_all_data",
        new=AsyncMock(return_value={**_BASE_DATA}),
    )
    mocker.patch("zigporter.commands.inspect._pick_device", new=AsyncMock(return_value=None))
    mock_show = mocker.patch("zigporter.commands.inspect.show_report")

    await run_inspect("url", "token", False)  # device=None → interactive

    mock_show.assert_not_called()


async def test_run_inspect_interactive_picker_shows_report(mocker):
    """When device is None and picker returns a device_id, show_report is called."""
    from zigporter.commands.inspect import run_inspect

    mock_ha = mocker.AsyncMock()
    mocker.patch("zigporter.commands.inspect.HAClient", return_value=mock_ha)
    mocker.patch(
        "zigporter.commands.inspect.fetch_all_data",
        new=AsyncMock(return_value={**_BASE_DATA}),
    )
    mocker.patch("zigporter.commands.inspect._pick_device", new=AsyncMock(return_value="dev-abc"))
    mock_show = mocker.patch("zigporter.commands.inspect.show_report")

    await run_inspect("url", "token", False)

    mock_show.assert_called_once()


# ---------------------------------------------------------------------------
# run_inspect — deps is None
# ---------------------------------------------------------------------------


async def test_run_inspect_deps_none_prints_error(mocker):
    """build_deps returning None must print an error and not call show_report."""
    from zigporter.commands.inspect import run_inspect

    mock_ha = mocker.AsyncMock()
    mocker.patch("zigporter.commands.inspect.HAClient", return_value=mock_ha)
    mocker.patch(
        "zigporter.commands.inspect.fetch_all_data",
        new=AsyncMock(return_value={**_BASE_DATA}),
    )
    mocker.patch("zigporter.commands.inspect.build_deps", return_value=None)
    mock_show = mocker.patch("zigporter.commands.inspect.show_report")

    await run_inspect("url", "token", False, device="switch.kitchen_plug")

    mock_show.assert_not_called()


# ---------------------------------------------------------------------------
# run_inspect — debug mode calls _debug_lovelace
# ---------------------------------------------------------------------------


async def test_run_inspect_debug_mode_calls_debug_lovelace(mocker):
    """debug=True without --json must call _debug_lovelace."""
    from zigporter.commands.inspect import run_inspect

    mock_ha = mocker.AsyncMock()
    mocker.patch("zigporter.commands.inspect.HAClient", return_value=mock_ha)
    mocker.patch(
        "zigporter.commands.inspect.fetch_all_data",
        new=AsyncMock(return_value={**_BASE_DATA, "_panels_data": {}}),
    )
    mock_debug = mocker.patch("zigporter.commands.inspect._debug_lovelace")
    mocker.patch("zigporter.commands.inspect.show_report")

    await run_inspect("url", "token", False, debug=True, device="switch.kitchen_plug")

    mock_debug.assert_called_once()


# ---------------------------------------------------------------------------
# _debug_lovelace — covers all config branches (None, YAML_MODE, real)
# ---------------------------------------------------------------------------


def test_debug_lovelace_all_branches():
    """_debug_lovelace must not raise for None, YAML_MODE, and real lovelace configs."""
    from zigporter.commands.inspect import _debug_lovelace

    all_data = {
        "_panels_data": {
            "lovelace": {"component_name": "lovelace", "url_path": ""},
            "other": {"component_name": "custom_panel"},
        },
        "lovelace": [
            (None, None),  # fetch failed
            ("yaml-dash", YAML_MODE),  # YAML mode — skipped
            (
                "home",
                {
                    "views": [
                        {
                            "title": "Home",
                            "cards": [{"type": "button", "entity": "switch.plug"}],
                        }
                    ]
                },
            ),
        ],
    }
    _debug_lovelace(all_data)  # must not raise


# ---------------------------------------------------------------------------
# _filter_by_backend — unknown backend falls through to full registry
# ---------------------------------------------------------------------------


def test_filter_by_backend_unknown_returns_full_registry():
    """An unrecognised backend string falls through to the full device_registry."""
    from zigporter.commands.inspect import _filter_by_backend

    dr = [{"id": "dev1"}, {"id": "dev2"}]
    result = _filter_by_backend(dr, [], "unknown_backend")
    assert result == dr


# ---------------------------------------------------------------------------
# build_deps — non-ZHA device uses device_registry fields
# ---------------------------------------------------------------------------


def test_build_deps_non_zha_device_uses_registry_fields():
    """Non-ZHA device must fall back to device_registry name/manufacturer/model."""
    data = {
        **_BASE_DATA,
        "zha_devices": [],
        "device_registry": [
            {
                "id": "dev-mqtt",
                "area_id": "kitchen",
                "name": "Z2M Plug",
                "name_by_user": "My Smart Plug",
                "manufacturer": "Sonoff",
                "model": "S31",
            }
        ],
        "entity_registry": [
            {"entity_id": "switch.z2m_plug", "device_id": "dev-mqtt", "platform": "mqtt"}
        ],
    }
    deps = build_deps("dev-mqtt", data)
    assert deps is not None
    assert deps.name == "My Smart Plug"
    assert deps.manufacturer == "Sonoff"
    assert deps.model == "S31"
    assert deps.ieee is None
