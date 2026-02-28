"""Tests for the rename command."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from zigporter.commands.rename import (
    RenameLocation,
    RenamePlan,
    _count_occurrences,
    _deep_replace,
    _discover_dashboards,
    build_rename_plan,
    display_plan,
    execute_rename,
)


# ---------------------------------------------------------------------------
# _count_occurrences
# ---------------------------------------------------------------------------


def test_count_occurrences_exact_string():
    assert _count_occurrences("switch.kitchen_plug", "switch.kitchen_plug") == 1


def test_count_occurrences_no_match():
    assert _count_occurrences("switch.other", "switch.kitchen_plug") == 0


def test_count_occurrences_in_dict_value():
    assert _count_occurrences({"entity_id": "switch.kitchen_plug"}, "switch.kitchen_plug") == 1


def test_count_occurrences_in_list():
    node = ["switch.kitchen_plug", "light.hall", "switch.kitchen_plug"]
    assert _count_occurrences(node, "switch.kitchen_plug") == 2


def test_count_occurrences_in_dict_key():
    # Scene format: entity ID used as a dict key
    node = {"switch.kitchen_plug": {"state": "on"}}
    assert _count_occurrences(node, "switch.kitchen_plug") == 1


def test_count_occurrences_nested():
    node = {
        "action": [{"service": "switch.turn_on", "target": {"entity_id": "switch.kitchen_plug"}}]
    }
    assert _count_occurrences(node, "switch.kitchen_plug") == 1


def test_count_occurrences_no_partial_match():
    # "switch.kitchen_plug_power" must NOT match "switch.kitchen_plug"
    assert _count_occurrences("switch.kitchen_plug_power", "switch.kitchen_plug") == 0


def test_count_occurrences_non_string_node():
    assert _count_occurrences(42, "switch.kitchen_plug") == 0
    assert _count_occurrences(None, "switch.kitchen_plug") == 0


# ---------------------------------------------------------------------------
# _deep_replace
# ---------------------------------------------------------------------------


def test_deep_replace_string_match():
    assert _deep_replace("switch.old", "switch.old", "switch.new") == "switch.new"


def test_deep_replace_string_no_match():
    assert _deep_replace("switch.other", "switch.old", "switch.new") == "switch.other"


def test_deep_replace_dict_value():
    result = _deep_replace({"entity_id": "switch.old"}, "switch.old", "switch.new")
    assert result == {"entity_id": "switch.new"}


def test_deep_replace_dict_key():
    # Scene entities dict: entity ID is a key
    result = _deep_replace({"switch.old": {"state": "on"}}, "switch.old", "switch.new")
    assert result == {"switch.new": {"state": "on"}}


def test_deep_replace_list():
    result = _deep_replace(["switch.old", "light.hall"], "switch.old", "switch.new")
    assert result == ["switch.new", "light.hall"]


def test_deep_replace_nested():
    node = {"action": [{"service": "switch.turn_on", "target": {"entity_id": "switch.old"}}]}
    result = _deep_replace(node, "switch.old", "switch.new")
    assert result["action"][0]["target"]["entity_id"] == "switch.new"


def test_deep_replace_no_partial_replace():
    assert _deep_replace("switch.old_extra", "switch.old", "switch.new") == "switch.old_extra"


def test_deep_replace_non_string_passthrough():
    assert _deep_replace(42, "switch.old", "switch.new") == 42
    assert _deep_replace(None, "switch.old", "switch.new") is None


# ---------------------------------------------------------------------------
# _discover_dashboards
# ---------------------------------------------------------------------------


def test_discover_dashboards_empty_panels():
    url_paths, titles = _discover_dashboards({})
    assert url_paths == [None]
    assert titles[None] == "Overview"


def test_discover_dashboards_default_panel():
    panels = {"lovelace": {"component_name": "lovelace", "url_path": "", "title": None}}
    url_paths, titles = _discover_dashboards(panels)
    assert None in url_paths
    assert titles[None] == "Overview"


def test_discover_dashboards_extra_panel():
    panels = {
        "lovelace": {"component_name": "lovelace", "url_path": ""},
        "mobile": {"component_name": "lovelace", "url_path": "mobile", "title": "Mobile"},
    }
    url_paths, titles = _discover_dashboards(panels)
    assert None in url_paths
    assert "mobile" in url_paths
    assert titles["mobile"] == "Mobile"


def test_discover_dashboards_ignores_non_lovelace():
    # "config" is in _NON_LOVELACE_PANELS so it's excluded via URL blocklist.
    panels = {
        "config": {"component_name": "config"},
        "lovelace": {"component_name": "lovelace", "url_path": ""},
    }
    url_paths, _ = _discover_dashboards(panels)
    assert len(url_paths) == 1


def test_discover_dashboards_includes_custom_panel():
    """HACS/custom frontend panels with non-lovelace component_name must be included."""
    panels = {
        "lovelace": {"component_name": "lovelace", "url_path": ""},
        "dashboard-mushroom": {
            "component_name": "custom:mushroom",
            "url_path": "dashboard-mushroom",
            "title": "Mushroom",
        },
    }
    url_paths, titles = _discover_dashboards(panels)
    assert "dashboard-mushroom" in url_paths
    assert titles["dashboard-mushroom"] == "Mushroom"


# ---------------------------------------------------------------------------
# build_rename_plan
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_ha_client():
    client = MagicMock()
    client.get_entity_registry = AsyncMock(
        return_value=[
            {"entity_id": "switch.kitchen_plug", "platform": "zha"},
            {"entity_id": "light.hall", "platform": "zha"},
        ]
    )
    client.get_automation_configs = AsyncMock(
        return_value=[
            {
                "id": "auto1",
                "alias": "Morning routine",
                "action": [{"entity_id": "switch.kitchen_plug"}],
            }
        ]
    )
    client.get_scripts = AsyncMock(return_value=[])
    client.get_scenes = AsyncMock(
        return_value=[
            {
                "id": "scene1",
                "name": "Kitchen evening",
                "entities": {"switch.kitchen_plug": {"state": "on"}},
            }
        ]
    )
    client.get_panels = AsyncMock(return_value={})
    client.get_lovelace_config = AsyncMock(
        return_value={
            "views": [
                {
                    "title": "Home",
                    "cards": [{"type": "entities", "entities": ["switch.kitchen_plug"]}],
                }
            ]
        }
    )
    return client


async def test_build_rename_plan_entity_not_found(mock_ha_client):
    with pytest.raises(ValueError, match="not found"):
        await build_rename_plan(mock_ha_client, "switch.nonexistent", "switch.new")


async def test_build_rename_plan_new_entity_exists(mock_ha_client):
    with pytest.raises(ValueError, match="already exists"):
        await build_rename_plan(mock_ha_client, "switch.kitchen_plug", "light.hall")


async def test_build_rename_plan_always_includes_registry(mock_ha_client):
    plan = await build_rename_plan(mock_ha_client, "switch.kitchen_plug", "switch.new_plug")
    registry_locs = [loc for loc in plan.locations if loc.context == "registry"]
    assert len(registry_locs) == 1


async def test_build_rename_plan_finds_automation(mock_ha_client):
    plan = await build_rename_plan(mock_ha_client, "switch.kitchen_plug", "switch.new_plug")
    auto_locs = [loc for loc in plan.locations if loc.context == "automation"]
    assert len(auto_locs) == 1
    assert auto_locs[0].name == "Morning routine"
    assert auto_locs[0].item_id == "auto1"


async def test_build_rename_plan_finds_scene(mock_ha_client):
    plan = await build_rename_plan(mock_ha_client, "switch.kitchen_plug", "switch.new_plug")
    scene_locs = [loc for loc in plan.locations if loc.context == "scene"]
    assert len(scene_locs) == 1
    assert scene_locs[0].name == "Kitchen evening"


async def test_build_rename_plan_finds_lovelace(mock_ha_client):
    plan = await build_rename_plan(mock_ha_client, "switch.kitchen_plug", "switch.new_plug")
    lv_locs = [loc for loc in plan.locations if loc.context == "lovelace"]
    assert len(lv_locs) == 1


async def test_build_rename_plan_skips_unrelated_automation(mock_ha_client):
    mock_ha_client.get_automation_configs = AsyncMock(
        return_value=[{"id": "a1", "alias": "Unrelated", "action": [{"entity_id": "light.hall"}]}]
    )
    plan = await build_rename_plan(mock_ha_client, "switch.kitchen_plug", "switch.new_plug")
    auto_locs = [loc for loc in plan.locations if loc.context == "automation"]
    assert len(auto_locs) == 0


async def test_build_rename_plan_finds_script(mock_ha_client):
    mock_ha_client.get_scripts = AsyncMock(
        return_value=[
            {
                "id": "s1",
                "alias": "Turn on plug",
                "sequence": [{"service": "switch.turn_on", "entity_id": "switch.kitchen_plug"}],
            }
        ]
    )
    plan = await build_rename_plan(mock_ha_client, "switch.kitchen_plug", "switch.new_plug")
    script_locs = [loc for loc in plan.locations if loc.context == "script"]
    assert len(script_locs) == 1
    assert script_locs[0].name == "Turn on plug"


async def test_build_rename_plan_skips_null_lovelace(mock_ha_client):
    mock_ha_client.get_lovelace_config = AsyncMock(return_value=None)
    plan = await build_rename_plan(mock_ha_client, "switch.kitchen_plug", "switch.new_plug")
    lv_locs = [loc for loc in plan.locations if loc.context == "lovelace"]
    assert len(lv_locs) == 0


async def test_build_rename_plan_tracks_scanned_dashboards(mock_ha_client):
    """Non-YAML dashboards that were fetched should appear in scanned_dashboard_names."""
    plan = await build_rename_plan(mock_ha_client, "switch.kitchen_plug", "switch.new_plug")
    # The default fixture has empty panels → url_paths=[None] titled "Overview"
    # and lovelace_config returns a real dict, so "Overview" should be scanned
    assert "Overview" in plan.scanned_dashboard_names
    assert plan.yaml_mode_dashboard_names == []
    assert plan.yaml_mode_dashboard_paths == []


async def test_build_rename_plan_tracks_yaml_mode_dashboards(mock_ha_client):
    """YAML-mode dashboards should populate yaml_mode_dashboard_names and not be scanned."""
    from zigporter.ha_client import YAML_MODE  # noqa: PLC0415

    mock_ha_client.get_lovelace_config = AsyncMock(return_value=YAML_MODE)
    plan = await build_rename_plan(mock_ha_client, "switch.kitchen_plug", "switch.new_plug")
    assert "Overview" in plan.yaml_mode_dashboard_names
    assert None in plan.yaml_mode_dashboard_paths
    assert plan.scanned_dashboard_names == []
    lv_locs = [loc for loc in plan.locations if loc.context == "lovelace"]
    assert len(lv_locs) == 0


# ---------------------------------------------------------------------------
# execute_rename
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_exec_client():
    client = MagicMock()
    client.rename_entity_id = AsyncMock(return_value=None)
    client.update_automation = AsyncMock(return_value=None)
    client.update_script = AsyncMock(return_value=None)
    client.update_scene = AsyncMock(return_value=None)
    client.save_lovelace_config = AsyncMock(return_value=None)
    return client


async def test_execute_rename_calls_registry(mock_exec_client):
    plan = RenamePlan(
        old_entity_id="switch.old",
        new_entity_id="switch.new",
        locations=[
            RenameLocation(
                context="registry", name="HA entity registry", item_id="switch.old", occurrences=1
            )
        ],
    )
    await execute_rename(mock_exec_client, plan)
    mock_exec_client.rename_entity_id.assert_called_once_with("switch.old", "switch.new")


async def test_execute_rename_patches_automation(mock_exec_client):
    config = {"id": "a1", "alias": "Test", "action": [{"entity_id": "switch.old"}]}
    plan = RenamePlan(
        old_entity_id="switch.old",
        new_entity_id="switch.new",
        locations=[
            RenameLocation(
                context="automation",
                name="Test",
                item_id="a1",
                occurrences=1,
                raw_config=config,
            )
        ],
    )
    await execute_rename(mock_exec_client, plan)
    patched = mock_exec_client.update_automation.call_args[0][1]
    assert patched["action"][0]["entity_id"] == "switch.new"


async def test_execute_rename_patches_script(mock_exec_client):
    config = {"id": "s1", "alias": "Test", "sequence": [{"entity_id": "switch.old"}]}
    plan = RenamePlan(
        old_entity_id="switch.old",
        new_entity_id="switch.new",
        locations=[
            RenameLocation(
                context="script", name="Test", item_id="s1", occurrences=1, raw_config=config
            )
        ],
    )
    await execute_rename(mock_exec_client, plan)
    patched = mock_exec_client.update_script.call_args[0][1]
    assert patched["sequence"][0]["entity_id"] == "switch.new"


async def test_execute_rename_patches_scene_key(mock_exec_client):
    config = {"id": "s1", "name": "Evening", "entities": {"switch.old": {"state": "on"}}}
    plan = RenamePlan(
        old_entity_id="switch.old",
        new_entity_id="switch.new",
        locations=[
            RenameLocation(
                context="scene", name="Evening", item_id="s1", occurrences=1, raw_config=config
            )
        ],
    )
    await execute_rename(mock_exec_client, plan)
    patched = mock_exec_client.update_scene.call_args[0][1]
    assert "switch.new" in patched["entities"]
    assert "switch.old" not in patched["entities"]


async def test_execute_rename_patches_lovelace_default(mock_exec_client):
    config = {"views": [{"cards": [{"type": "entities", "entities": ["switch.old"]}]}]}
    plan = RenamePlan(
        old_entity_id="switch.old",
        new_entity_id="switch.new",
        locations=[
            RenameLocation(
                context="lovelace", name="Default", item_id="", occurrences=1, raw_config=config
            )
        ],
    )
    await execute_rename(mock_exec_client, plan)
    patched_config, patched_url = mock_exec_client.save_lovelace_config.call_args[0]
    assert patched_config["views"][0]["cards"][0]["entities"] == ["switch.new"]
    assert patched_url is None  # "" item_id → None url_path for default dashboard


async def test_execute_rename_patches_lovelace_named(mock_exec_client):
    config = {"views": [{"cards": [{"type": "button", "entity": "switch.old"}]}]}
    plan = RenamePlan(
        old_entity_id="switch.old",
        new_entity_id="switch.new",
        locations=[
            RenameLocation(
                context="lovelace",
                name="Mobile",
                item_id="mobile",
                occurrences=1,
                raw_config=config,
            )
        ],
    )
    await execute_rename(mock_exec_client, plan)
    _, patched_url = mock_exec_client.save_lovelace_config.call_args[0]
    assert patched_url == "mobile"


# ---------------------------------------------------------------------------
# display_plan (smoke test — must not raise)
# ---------------------------------------------------------------------------


def test_display_plan_no_raise():
    plan = RenamePlan(
        old_entity_id="switch.old",
        new_entity_id="switch.new",
        locations=[
            RenameLocation(
                context="registry", name="HA entity registry", item_id="switch.old", occurrences=1
            ),
            RenameLocation(context="automation", name="My auto", item_id="a1", occurrences=2),
            RenameLocation(context="scene", name="Evening", item_id="s1", occurrences=1),
            RenameLocation(context="lovelace", name="Default", item_id="", occurrences=3),
        ],
    )
    display_plan(plan)  # must not raise


def test_rename_plan_total_occurrences():
    plan = RenamePlan(
        old_entity_id="switch.old",
        new_entity_id="switch.new",
        locations=[
            RenameLocation(
                context="registry", name="HA entity registry", item_id="x", occurrences=1
            ),
            RenameLocation(context="automation", name="Auto", item_id="a1", occurrences=3),
            RenameLocation(context="lovelace", name="Default", item_id="", occurrences=2),
        ],
    )
    assert plan.total_occurrences == 6


def test_display_plan_always_shows_energy_note(mocker):
    """Energy config auto-update note should always appear in the footer."""
    printed: list[str] = []
    mocker.patch(
        "zigporter.commands.rename.console.print",
        side_effect=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
    )

    plan = RenamePlan(
        old_entity_id="switch.old",
        new_entity_id="switch.new",
        locations=[
            RenameLocation(
                context="registry", name="HA entity registry", item_id="switch.old", occurrences=1
            ),
        ],
    )
    display_plan(plan)
    all_output = "\n".join(printed)
    assert "energy" in all_output
    assert "auto-updated" in all_output


def test_display_plan_shows_zero_ref_scanned_dashboards(mocker):
    """Scanned dashboards with 0 references should appear in the footer."""
    printed: list[str] = []
    mocker.patch(
        "zigporter.commands.rename.console.print",
        side_effect=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
    )

    plan = RenamePlan(
        old_entity_id="switch.old",
        new_entity_id="switch.new",
        locations=[
            RenameLocation(
                context="registry", name="HA entity registry", item_id="switch.old", occurrences=1
            ),
        ],
        scanned_dashboard_names=["Overview", "Mushroom"],
    )
    display_plan(plan)
    all_output = "\n".join(printed)
    assert "Overview" in all_output
    assert "Mushroom" in all_output
    assert "0 references" in all_output


def test_display_plan_omits_auto_updated_from_zero_ref_footer(mocker):
    """Dashboards that have lovelace location entries should NOT appear in the 0-ref footer."""
    printed: list[str] = []
    mocker.patch(
        "zigporter.commands.rename.console.print",
        side_effect=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
    )

    plan = RenamePlan(
        old_entity_id="switch.old",
        new_entity_id="switch.new",
        locations=[
            RenameLocation(context="lovelace", name="Home", item_id="", occurrences=1),
        ],
        scanned_dashboard_names=["Home", "Mushroom"],
    )
    display_plan(plan)
    # "Home" has a match so it's in the table, not the 0-ref footer
    # "Mushroom" has no match so it should appear in the footer
    footer_lines = [p for p in printed if "0 references" in p]
    assert any("Mushroom" in line for line in footer_lines)
    assert not any("Home" in line for line in footer_lines)


def test_display_plan_shows_yaml_mode_dashboards_inline(mocker):
    """YAML-mode dashboards should appear in the footer with a YAML warning."""
    printed: list[str] = []
    mocker.patch(
        "zigporter.commands.rename.console.print",
        side_effect=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
    )

    plan = RenamePlan(
        old_entity_id="switch.old",
        new_entity_id="switch.new",
        locations=[
            RenameLocation(
                context="registry", name="HA entity registry", item_id="switch.old", occurrences=1
            ),
        ],
        yaml_mode_dashboard_names=["Overview"],
        yaml_mode_dashboard_paths=[None],
    )
    display_plan(plan)
    all_output = "\n".join(printed)
    assert "Overview" in all_output
    assert "YAML mode" in all_output


def test_display_plan_shows_yaml_mode_manual_steps(mocker):
    """YAML-mode dashboards should trigger the manual-steps block with grep hint and find/replace table."""
    printed: list[str] = []
    mocker.patch(
        "zigporter.commands.rename.console.print",
        side_effect=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
    )

    plan = RenamePlan(
        old_entity_id="sensor.kontor_temp",
        new_entity_id="sensor.office_climate",
        locations=[
            RenameLocation(
                context="registry",
                name="HA entity registry",
                item_id="sensor.kontor_temp",
                occurrences=1,
            ),
        ],
        yaml_mode_dashboard_names=["Overview"],
        yaml_mode_dashboard_paths=[None],
    )
    display_plan(plan)
    all_output = "\n".join(printed)
    # grep hint uses the full entity ID as the search term
    assert "sensor.kontor_temp" in all_output
    assert "grep" in all_output
    # find/replace table content
    assert "sensor.office_climate" in all_output


# ---------------------------------------------------------------------------
# run_rename
# ---------------------------------------------------------------------------

_SIMPLE_PLAN = RenamePlan(
    old_entity_id="switch.old",
    new_entity_id="switch.new",
    locations=[
        RenameLocation(
            context="registry", name="HA entity registry", item_id="switch.old", occurrences=1
        ),
        RenameLocation(context="automation", name="My auto", item_id="a1", occurrences=1),
    ],
)


async def test_run_rename_apply_success(mocker):
    mocker.patch("zigporter.commands.rename.HAClient")
    mocker.patch("zigporter.commands.rename.build_rename_plan", return_value=_SIMPLE_PLAN)
    mocker.patch("zigporter.commands.rename.execute_rename", new=AsyncMock())

    from zigporter.commands.rename import run_rename  # noqa: PLC0415

    result = await run_rename("https://ha.test", "token", True, "switch.old", "switch.new", True)
    assert result is True


async def test_run_rename_validation_error(mocker):
    mocker.patch("zigporter.commands.rename.HAClient")
    mocker.patch("zigporter.commands.rename.build_rename_plan", side_effect=ValueError("not found"))

    from zigporter.commands.rename import run_rename  # noqa: PLC0415

    result = await run_rename("https://ha.test", "token", True, "switch.old", "switch.new", True)
    assert result is False


async def test_run_rename_no_tty_no_apply(mocker):
    mocker.patch("zigporter.commands.rename.HAClient")
    mocker.patch("zigporter.commands.rename.build_rename_plan", return_value=_SIMPLE_PLAN)
    mocker.patch("zigporter.commands.rename.sys").stdin.isatty.return_value = False

    from zigporter.commands.rename import run_rename  # noqa: PLC0415

    result = await run_rename("https://ha.test", "token", True, "switch.old", "switch.new", False)
    assert result is False


async def test_run_rename_confirmed(mocker):
    mocker.patch("zigporter.commands.rename.HAClient")
    mocker.patch("zigporter.commands.rename.build_rename_plan", return_value=_SIMPLE_PLAN)
    mock_execute = mocker.patch("zigporter.commands.rename.execute_rename", new=AsyncMock())
    mocker.patch("zigporter.commands.rename.sys").stdin.isatty.return_value = True
    mock_q = mocker.MagicMock()
    mock_q.unsafe_ask_async = AsyncMock(return_value=True)
    mocker.patch("zigporter.commands.rename.questionary.confirm", return_value=mock_q)

    from zigporter.commands.rename import run_rename  # noqa: PLC0415

    result = await run_rename("https://ha.test", "token", True, "switch.old", "switch.new", False)
    assert result is True
    mock_execute.assert_called_once()


async def test_run_rename_aborted(mocker):
    mocker.patch("zigporter.commands.rename.HAClient")
    mocker.patch("zigporter.commands.rename.build_rename_plan", return_value=_SIMPLE_PLAN)
    mocker.patch("zigporter.commands.rename.execute_rename", new=AsyncMock())
    mocker.patch("zigporter.commands.rename.sys").stdin.isatty.return_value = True
    mock_q = mocker.MagicMock()
    mock_q.unsafe_ask_async = AsyncMock(return_value=False)
    mocker.patch("zigporter.commands.rename.questionary.confirm", return_value=mock_q)

    from zigporter.commands.rename import run_rename  # noqa: PLC0415

    result = await run_rename("https://ha.test", "token", True, "switch.old", "switch.new", False)
    assert result is True  # aborted is not an error


# ---------------------------------------------------------------------------
# rename_command
# ---------------------------------------------------------------------------


def test_rename_command_success(mocker):
    mocker.patch("zigporter.commands.rename.run_rename", new=AsyncMock(return_value=True))

    from zigporter.commands.rename import rename_command  # noqa: PLC0415

    rename_command("https://ha.test", "token", True, "switch.old", "switch.new", False)


def test_rename_command_failure(mocker):
    import typer  # noqa: PLC0415

    mocker.patch("zigporter.commands.rename.run_rename", new=AsyncMock(return_value=False))

    from zigporter.commands.rename import rename_command  # noqa: PLC0415

    with pytest.raises(typer.Exit):
        rename_command("https://ha.test", "token", True, "switch.old", "switch.new", False)


# ---------------------------------------------------------------------------
# rename CLI (main.py)
# ---------------------------------------------------------------------------


def test_rename_cli_invokes_rename_command(mocker):
    mocker.patch("zigporter.main._get_config", return_value=("https://ha.test", "token", True))
    mock_cmd = mocker.patch("zigporter.commands.rename.rename_command")

    from typer.testing import CliRunner  # noqa: PLC0415

    from zigporter.main import app  # noqa: PLC0415

    runner = CliRunner()
    result = runner.invoke(app, ["rename-entity", "switch.old", "switch.new", "--apply"])
    assert result.exit_code == 0
    mock_cmd.assert_called_once_with(
        ha_url="https://ha.test",
        token="token",
        verify_ssl=True,
        old_entity_id="switch.old",
        new_entity_id="switch.new",
        apply=True,
    )


# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------


def test_slugify_basic():
    from zigporter.commands.rename import slugify  # noqa: PLC0415

    assert slugify("Living Room Lamp") == "living_room_lamp"


def test_slugify_unicode_transliteration():
    from zigporter.commands.rename import slugify  # noqa: PLC0415

    assert slugify("Büro (Office)") == "buro_office"


def test_slugify_already_slug():
    from zigporter.commands.rename import slugify  # noqa: PLC0415

    assert slugify("kitchen_plug") == "kitchen_plug"


def test_slugify_leading_trailing_separators():
    from zigporter.commands.rename import slugify  # noqa: PLC0415

    assert slugify("  my device  ") == "my_device"


# ---------------------------------------------------------------------------
# compute_entity_pairs
# ---------------------------------------------------------------------------


def test_compute_entity_pairs_all_match():
    from zigporter.commands.rename import compute_entity_pairs  # noqa: PLC0415

    entities = [
        {"entity_id": "light.kitchen_plug", "original_name": "Light"},
        {"entity_id": "sensor.kitchen_plug_power", "original_name": "Power"},
    ]
    matched, odd = compute_entity_pairs(entities, "kitchen_plug", "living_room_lamp")
    assert len(matched) == 2
    assert len(odd) == 0
    assert ("light.kitchen_plug", "light.living_room_lamp") in matched
    assert ("sensor.kitchen_plug_power", "sensor.living_room_lamp_power") in matched


def test_compute_entity_pairs_odd_entity():
    from zigporter.commands.rename import compute_entity_pairs  # noqa: PLC0415

    entities = [
        {"entity_id": "light.kitchen_plug", "original_name": "Light"},
        {"entity_id": "sensor.power_usage_custom", "original_name": "Power"},
    ]
    matched, odd = compute_entity_pairs(entities, "kitchen_plug", "living_room_lamp")
    assert len(matched) == 1
    assert len(odd) == 1
    assert odd[0]["entity_id"] == "sensor.power_usage_custom"


def test_compute_entity_pairs_empty_slug():
    from zigporter.commands.rename import compute_entity_pairs  # noqa: PLC0415

    entities = [{"entity_id": "light.some_entity", "original_name": "Light"}]
    # empty old_slug → everything goes to odd
    matched, odd = compute_entity_pairs(entities, "", "bedroom_lamp")
    assert matched == []
    assert len(odd) == 1


# ---------------------------------------------------------------------------
# fetch_ha_snapshot + build_rename_plan_from_snapshot
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_ha_snapshot_client():
    client = MagicMock()
    client.get_entity_registry = AsyncMock(
        return_value=[
            {"entity_id": "light.kitchen_plug", "device_id": "dev1"},
            {"entity_id": "sensor.kitchen_plug_power", "device_id": "dev1"},
        ]
    )
    client.get_automation_configs = AsyncMock(
        return_value=[
            {"id": "a1", "alias": "Auto", "action": [{"entity_id": "light.kitchen_plug"}]}
        ]
    )
    client.get_scripts = AsyncMock(return_value=[])
    client.get_scenes = AsyncMock(return_value=[])
    client.get_panels = AsyncMock(return_value={})
    client.get_lovelace_config = AsyncMock(return_value=None)
    client.get_config_entries = AsyncMock(return_value=[])
    return client


async def test_fetch_ha_snapshot_returns_snapshot(mock_ha_snapshot_client):
    from zigporter.commands.rename import fetch_ha_snapshot  # noqa: PLC0415

    snapshot = await fetch_ha_snapshot(mock_ha_snapshot_client)
    assert len(snapshot.entity_registry) == 2
    assert len(snapshot.automations) == 1
    assert snapshot.url_paths == [None]


async def test_build_rename_plan_from_snapshot_finds_automation(mock_ha_snapshot_client):
    from zigporter.commands.rename import (  # noqa: PLC0415
        build_rename_plan_from_snapshot,
        fetch_ha_snapshot,
    )

    snapshot = await fetch_ha_snapshot(mock_ha_snapshot_client)
    plan = build_rename_plan_from_snapshot(snapshot, "light.kitchen_plug", "light.bedroom_lamp")
    auto_locs = [loc for loc in plan.locations if loc.context == "automation"]
    assert len(auto_locs) == 1


async def test_build_rename_plan_from_snapshot_entity_not_found(mock_ha_snapshot_client):
    from zigporter.commands.rename import (  # noqa: PLC0415
        build_rename_plan_from_snapshot,
        fetch_ha_snapshot,
    )

    snapshot = await fetch_ha_snapshot(mock_ha_snapshot_client)
    with pytest.raises(ValueError, match="not found"):
        build_rename_plan_from_snapshot(snapshot, "light.nonexistent", "light.new")


async def test_build_rename_plan_from_snapshot_new_entity_exists(mock_ha_snapshot_client):
    from zigporter.commands.rename import (  # noqa: PLC0415
        build_rename_plan_from_snapshot,
        fetch_ha_snapshot,
    )

    snapshot = await fetch_ha_snapshot(mock_ha_snapshot_client)
    with pytest.raises(ValueError, match="already exists"):
        build_rename_plan_from_snapshot(snapshot, "light.kitchen_plug", "sensor.kitchen_plug_power")


# ---------------------------------------------------------------------------
# YAML_MODE sentinel
# ---------------------------------------------------------------------------


def test_is_yaml_mode_sentinel():
    from zigporter.ha_client import YAML_MODE, is_yaml_mode  # noqa: PLC0415

    assert is_yaml_mode(YAML_MODE) is True
    assert is_yaml_mode(None) is False
    assert is_yaml_mode({}) is False  # different empty dict — not the sentinel
    assert is_yaml_mode({"views": []}) is False


async def test_build_rename_plan_from_snapshot_skips_yaml_mode(mock_ha_snapshot_client):
    """Dashboards returning YAML_MODE must be skipped (not crash, not counted as refs)."""
    from zigporter.commands.rename import (  # noqa: PLC0415
        build_rename_plan_from_snapshot,
        fetch_ha_snapshot,
    )
    from zigporter.ha_client import YAML_MODE  # noqa: PLC0415

    mock_ha_snapshot_client.get_lovelace_config = AsyncMock(return_value=YAML_MODE)
    snapshot = await fetch_ha_snapshot(mock_ha_snapshot_client)
    plan = build_rename_plan_from_snapshot(snapshot, "light.kitchen_plug", "light.bedroom_lamp")
    lv_locs = [loc for loc in plan.locations if loc.context == "lovelace"]
    assert len(lv_locs) == 0


# ---------------------------------------------------------------------------
# execute_device_rename — verifies merged location updates
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_device_exec_client():
    client = MagicMock()
    client.rename_device_name = AsyncMock(return_value=None)
    client.rename_entity_id = AsyncMock(return_value=None)
    client.update_automation = AsyncMock(return_value=None)
    client.update_script = AsyncMock(return_value=None)
    client.update_scene = AsyncMock(return_value=None)
    client.save_lovelace_config = AsyncMock(return_value=None)
    return client


async def test_execute_device_rename_renames_device_and_entities(mock_device_exec_client):
    from zigporter.commands.rename import DeviceRenamePlan, RenameLocation, RenamePlan  # noqa: PLC0415
    from zigporter.commands.rename import execute_device_rename  # noqa: PLC0415

    plan = DeviceRenamePlan(
        device_id="dev1",
        old_device_name="Kitchen Plug",
        new_device_name="Living Room Plug",
        plans=[
            RenamePlan(
                old_entity_id="light.kitchen_plug",
                new_entity_id="light.living_room_plug",
                locations=[
                    RenameLocation(
                        context="registry",
                        name="HA entity registry",
                        item_id="light.kitchen_plug",
                        occurrences=1,
                    )
                ],
            )
        ],
    )
    await execute_device_rename(mock_device_exec_client, plan)
    mock_device_exec_client.rename_device_name.assert_called_once_with("dev1", "Living Room Plug")
    mock_device_exec_client.rename_entity_id.assert_called_once_with(
        "light.kitchen_plug", "light.living_room_plug"
    )


async def test_execute_device_rename_merges_shared_automation(mock_device_exec_client):
    """An automation referenced by two entities should be updated exactly once
    with both substitutions applied."""
    from zigporter.commands.rename import DeviceRenamePlan, RenameLocation, RenamePlan  # noqa: PLC0415
    from zigporter.commands.rename import execute_device_rename  # noqa: PLC0415

    shared_config = {
        "id": "a1",
        "alias": "Morning",
        "action": [
            {"entity_id": "light.kitchen_plug"},
            {"entity_id": "sensor.kitchen_plug_power"},
        ],
    }
    plan = DeviceRenamePlan(
        device_id="dev1",
        old_device_name="Kitchen Plug",
        new_device_name="Bedroom Lamp",
        plans=[
            RenamePlan(
                old_entity_id="light.kitchen_plug",
                new_entity_id="light.bedroom_lamp",
                locations=[
                    RenameLocation(
                        context="registry",
                        name="HA entity registry",
                        item_id="light.kitchen_plug",
                        occurrences=1,
                    ),
                    RenameLocation(
                        context="automation",
                        name="Morning",
                        item_id="a1",
                        occurrences=1,
                        raw_config=shared_config,
                    ),
                ],
            ),
            RenamePlan(
                old_entity_id="sensor.kitchen_plug_power",
                new_entity_id="sensor.bedroom_lamp_power",
                locations=[
                    RenameLocation(
                        context="registry",
                        name="HA entity registry",
                        item_id="sensor.kitchen_plug_power",
                        occurrences=1,
                    ),
                    RenameLocation(
                        context="automation",
                        name="Morning",
                        item_id="a1",
                        occurrences=1,
                        raw_config=shared_config,
                    ),
                ],
            ),
        ],
    )
    await execute_device_rename(mock_device_exec_client, plan)

    # Automation updated exactly once
    assert mock_device_exec_client.update_automation.call_count == 1
    patched = mock_device_exec_client.update_automation.call_args[0][1]
    # Both entity IDs replaced
    assert patched["action"][0]["entity_id"] == "light.bedroom_lamp"
    assert patched["action"][1]["entity_id"] == "sensor.bedroom_lamp_power"


# ---------------------------------------------------------------------------
# display_device_plan (smoke test)
# ---------------------------------------------------------------------------


def test_display_device_plan_no_raise():
    from zigporter.commands.rename import DeviceRenamePlan, RenameLocation, RenamePlan  # noqa: PLC0415
    from zigporter.commands.rename import display_device_plan  # noqa: PLC0415

    plan = DeviceRenamePlan(
        device_id="dev1",
        old_device_name="Kitchen Plug",
        new_device_name="Bedroom Lamp",
        plans=[
            RenamePlan(
                old_entity_id="light.kitchen_plug",
                new_entity_id="light.bedroom_lamp",
                locations=[
                    RenameLocation(
                        context="registry",
                        name="HA entity registry",
                        item_id="light.kitchen_plug",
                        occurrences=1,
                    ),
                    RenameLocation(
                        context="automation", name="Morning", item_id="a1", occurrences=1
                    ),
                ],
            )
        ],
    )
    display_device_plan(plan)  # must not raise


def test_display_device_plan_shows_zero_ref_dashboards_when_auto_updated(mocker):
    """Dashboards that were scanned but had 0 refs should appear even when other
    locations were auto-updated."""
    from zigporter.commands.rename import DeviceRenamePlan, RenameLocation, RenamePlan  # noqa: PLC0415
    from zigporter.commands.rename import display_device_plan  # noqa: PLC0415

    printed: list[str] = []
    mocker.patch(
        "zigporter.commands.rename.console.print",
        side_effect=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
    )

    plan = DeviceRenamePlan(
        device_id="dev1",
        old_device_name="Kitchen Plug",
        new_device_name="Bedroom Lamp",
        plans=[
            RenamePlan(
                old_entity_id="light.kitchen_plug",
                new_entity_id="light.bedroom_lamp",
                locations=[
                    RenameLocation(context="lovelace", name="Home", item_id="", occurrences=2),
                ],
            )
        ],
        scanned_names={"dashboards": ["Home", "Mushroom"]},
        failed_dashboards=[],
    )
    display_device_plan(plan)
    all_output = "\n".join(printed)
    assert "Mushroom" in all_output
    assert "0 references" in all_output


def test_display_device_plan_shows_yaml_mode_dashboards_inline(mocker):
    """YAML-mode dashboards should appear inline in the auto-updated block
    with a YAML warning, even though they also appear in the manual steps below."""
    from zigporter.commands.rename import DeviceRenamePlan, RenameLocation, RenamePlan  # noqa: PLC0415
    from zigporter.commands.rename import display_device_plan  # noqa: PLC0415

    printed: list[str] = []
    mocker.patch(
        "zigporter.commands.rename.console.print",
        side_effect=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
    )

    plan = DeviceRenamePlan(
        device_id="dev1",
        old_device_name="Kitchen Plug",
        new_device_name="Bedroom Lamp",
        plans=[
            RenamePlan(
                old_entity_id="light.kitchen_plug",
                new_entity_id="light.bedroom_lamp",
                locations=[
                    RenameLocation(context="lovelace", name="Home", item_id="", occurrences=2),
                ],
            )
        ],
        scanned_names={"dashboards": ["Home"]},
        failed_dashboards=["Overview"],
        failed_dashboard_paths=[None],
    )
    display_device_plan(plan)
    all_output = "\n".join(printed)
    assert "Overview" in all_output
    assert "YAML mode" in all_output


# ---------------------------------------------------------------------------
# run_rename_device
# ---------------------------------------------------------------------------


_DEVICE = {"id": "dev1", "name": "Kitchen Plug", "name_by_user": None}

_DEVICE_PLAN = None  # built lazily in tests


async def test_run_rename_device_apply_success(mocker):
    mock_instance = MagicMock()
    mock_instance.get_entities_for_device = AsyncMock(
        return_value=[
            {"entity_id": "light.kitchen_plug", "original_name": "Light", "device_id": "dev1"}
        ]
    )
    mocker.patch("zigporter.commands.rename.HAClient", return_value=mock_instance)
    mocker.patch("zigporter.commands.rename.find_device", new=AsyncMock(return_value=_DEVICE))
    mocker.patch(
        "zigporter.commands.rename.fetch_ha_snapshot",
        new=AsyncMock(
            return_value=MagicMock(
                entity_registry=[
                    {"entity_id": "light.kitchen_plug", "device_id": "dev1"},
                ],
                automations=[],
                scripts=[],
                scenes=[],
                url_paths=[None],
                titles={None: "Default"},
                lovelace_configs=[None],
            )
        ),
    )
    mocker.patch(
        "zigporter.commands.rename.build_rename_plan_from_snapshot",
        return_value=RenamePlan(
            old_entity_id="light.kitchen_plug",
            new_entity_id="light.bedroom_lamp",
            locations=[
                RenameLocation(
                    context="registry",
                    name="HA entity registry",
                    item_id="light.kitchen_plug",
                    occurrences=1,
                )
            ],
        ),
    )
    mocker.patch("zigporter.commands.rename.execute_device_rename", new=AsyncMock())

    from zigporter.commands.rename import run_rename_device  # noqa: PLC0415

    result = await run_rename_device(
        "https://ha.test", "token", True, "Kitchen Plug", "Bedroom Lamp", True
    )
    assert result is True


async def test_run_rename_device_device_not_found(mocker):
    mocker.patch("zigporter.commands.rename.HAClient")
    mocker.patch("zigporter.commands.rename.find_device", new=AsyncMock(return_value=None))

    from zigporter.commands.rename import run_rename_device  # noqa: PLC0415

    result = await run_rename_device(
        "https://ha.test", "token", True, "Nonexistent Device", "New Name", True
    )
    assert result is False


async def test_run_rename_device_no_tty_no_apply(mocker):
    mock_instance = MagicMock()
    mock_instance.get_entities_for_device = AsyncMock(
        return_value=[
            {"entity_id": "light.kitchen_plug", "original_name": "Light", "device_id": "dev1"}
        ]
    )
    mocker.patch("zigporter.commands.rename.HAClient", return_value=mock_instance)
    mocker.patch("zigporter.commands.rename.find_device", new=AsyncMock(return_value=_DEVICE))
    mocker.patch(
        "zigporter.commands.rename.fetch_ha_snapshot",
        new=AsyncMock(
            return_value=MagicMock(
                entity_registry=[{"entity_id": "light.kitchen_plug", "device_id": "dev1"}],
                automations=[],
                scripts=[],
                scenes=[],
                url_paths=[None],
                titles={None: "Default"},
                lovelace_configs=[None],
            )
        ),
    )
    mocker.patch(
        "zigporter.commands.rename.build_rename_plan_from_snapshot",
        return_value=RenamePlan(
            old_entity_id="light.kitchen_plug",
            new_entity_id="light.bedroom_lamp",
            locations=[
                RenameLocation(
                    context="registry",
                    name="HA entity registry",
                    item_id="light.kitchen_plug",
                    occurrences=1,
                )
            ],
        ),
    )
    mocker.patch("zigporter.commands.rename.sys").stdin.isatty.return_value = False

    from zigporter.commands.rename import run_rename_device  # noqa: PLC0415

    result = await run_rename_device(
        "https://ha.test", "token", True, "Kitchen Plug", "Bedroom Lamp", False
    )
    assert result is False


# ---------------------------------------------------------------------------
# rename_device_command
# ---------------------------------------------------------------------------


def test_rename_device_command_success(mocker):
    mocker.patch("zigporter.commands.rename.run_rename_device", new=AsyncMock(return_value=True))

    from zigporter.commands.rename import rename_device_command  # noqa: PLC0415

    rename_device_command("https://ha.test", "token", True, "Kitchen Plug", "Bedroom Lamp", False)


def test_rename_device_command_failure(mocker):
    import typer  # noqa: PLC0415

    mocker.patch("zigporter.commands.rename.run_rename_device", new=AsyncMock(return_value=False))

    from zigporter.commands.rename import rename_device_command  # noqa: PLC0415

    with pytest.raises(typer.Exit):
        rename_device_command(
            "https://ha.test", "token", True, "Kitchen Plug", "Bedroom Lamp", False
        )


# ---------------------------------------------------------------------------
# rename-device CLI (main.py)
# ---------------------------------------------------------------------------


def test_rename_device_cli_invokes_rename_device_command(mocker):
    mocker.patch("zigporter.main._get_config", return_value=("https://ha.test", "token", True))
    mock_cmd = mocker.patch("zigporter.commands.rename.rename_device_command")

    from typer.testing import CliRunner  # noqa: PLC0415

    from zigporter.main import app  # noqa: PLC0415

    runner = CliRunner()
    result = runner.invoke(app, ["rename-device", "Kitchen Plug", "Bedroom Lamp", "--apply"])
    assert result.exit_code == 0
    mock_cmd.assert_called_once_with(
        ha_url="https://ha.test",
        token="token",
        verify_ssl=True,
        old_name="Kitchen Plug",
        new_name="Bedroom Lamp",
        apply=True,
    )
