"""Tests for the rename command."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from zigporter.commands.rename import (
    RenameLocation,
    RenamePlan,
    _count_occurrences,
    _deep_replace,
    _discover_dashboards,
    _suggest_entity_ids,
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
    client.get_config_entries = AsyncMock(return_value=[])
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
    client.update_config_entry_options = AsyncMock(return_value=None)
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


async def test_execute_rename_lovelace_save_failure_warns_and_continues(mock_exec_client, mocker):
    """save_lovelace_config raises RuntimeError → warns and does not crash."""
    mock_exec_client.save_lovelace_config = AsyncMock(
        side_effect=RuntimeError("WebSocket command failed: Not supported")
    )
    printed = []
    mocker.patch(
        "zigporter.commands.rename.console.print",
        side_effect=lambda *a, **kw: printed.append(a[0] if a else ""),
    )
    config = {"views": [{"entity": "switch.old"}]}
    plan = RenamePlan(
        old_entity_id="switch.old",
        new_entity_id="switch.new",
        locations=[
            RenameLocation(
                context="lovelace",
                name="Lights",
                item_id="lights",
                occurrences=1,
                raw_config=config,
            )
        ],
    )
    # Must not raise
    await execute_rename(mock_exec_client, plan)
    assert any("skipped" in str(p) for p in printed)


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
    mock_ha_cls = mocker.patch("zigporter.commands.rename.HAClient")
    mock_ha_cls.return_value.get_entity_registry = AsyncMock(return_value=[])
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
    client.get_z2m_config_entry_id = AsyncMock(return_value="z2m-entry-1")
    client.reload_config_entry = AsyncMock(return_value=None)
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


# ---------------------------------------------------------------------------
# display_plan — line 350: spacing between 2+ YAML-mode dashboards
# ---------------------------------------------------------------------------


def test_display_plan_yaml_mode_spacing_between_multiple(mocker):
    """Line 350: console.print() spacer between yaml-mode dashboard entries."""
    from zigporter.commands.rename import display_plan  # noqa: PLC0415

    plan = RenamePlan(
        old_entity_id="switch.kitchen_plug",
        new_entity_id="switch.bedroom_lamp",
        locations=[
            RenameLocation(
                context="registry",
                name="HA entity registry",
                item_id="switch.kitchen_plug",
                occurrences=1,
            )
        ],
        yaml_mode_dashboard_names=["Dashboard A", "Dashboard B"],
        yaml_mode_dashboard_paths=[None, "mobile"],
    )
    printed = []
    mocker.patch(
        "zigporter.commands.rename.console.print",
        side_effect=lambda *a, **kw: printed.append(a[0] if a else ""),
    )
    display_plan(plan)
    # The spacer console.print() is called with no args — produces an empty-string entry
    assert any(a == "" for a in printed)


# ---------------------------------------------------------------------------
# build_rename_plan_from_snapshot — script/scene/lovelace/config_entry matches
# ---------------------------------------------------------------------------


def _make_snapshot(
    *,
    scripts=None,
    scenes=None,
    lovelace_configs=None,
    url_paths=None,
    titles=None,
    config_entries=None,
):
    from zigporter.commands.rename import HASnapshot  # noqa: PLC0415

    return HASnapshot(
        entity_registry=[
            {"entity_id": "switch.kitchen_plug", "device_id": "dev1"},
            {"entity_id": "switch.other", "device_id": "dev2"},
        ],
        automations=[],
        scripts=scripts or [],
        scenes=scenes or [],
        url_paths=url_paths if url_paths is not None else [None],
        titles=titles if titles is not None else {None: "Overview"},
        lovelace_configs=lovelace_configs if lovelace_configs is not None else [None],
        config_entries=config_entries or [],
    )


def test_build_rename_plan_from_snapshot_script_match():
    """Lines 557-559: script with a matching entity ID."""
    from zigporter.commands.rename import build_rename_plan_from_snapshot  # noqa: PLC0415

    snap = _make_snapshot(
        scripts=[{"id": "s1", "alias": "My Script", "entity_id": "switch.kitchen_plug"}]
    )
    plan = build_rename_plan_from_snapshot(snap, "switch.kitchen_plug", "switch.bedroom_lamp")
    script_locs = [loc for loc in plan.locations if loc.context == "script"]
    assert len(script_locs) == 1
    assert script_locs[0].name == "My Script"


def test_build_rename_plan_from_snapshot_scene_match():
    """Lines 570-572: scene with a matching entity ID."""
    from zigporter.commands.rename import build_rename_plan_from_snapshot  # noqa: PLC0415

    snap = _make_snapshot(
        scenes=[
            {"id": "sc1", "name": "Evening", "entities": {"switch.kitchen_plug": {"state": "on"}}}
        ]
    )
    plan = build_rename_plan_from_snapshot(snap, "switch.kitchen_plug", "switch.bedroom_lamp")
    scene_locs = [loc for loc in plan.locations if loc.context == "scene"]
    assert len(scene_locs) == 1
    assert scene_locs[0].name == "Evening"


def test_build_rename_plan_from_snapshot_lovelace_match():
    """Lines 585-588: lovelace dashboard with a matching entity ID."""
    from zigporter.commands.rename import build_rename_plan_from_snapshot  # noqa: PLC0415

    lv_config = {"views": [{"cards": [{"entity": "switch.kitchen_plug"}]}]}
    snap = _make_snapshot(
        url_paths=[None],
        titles={None: "Home"},
        lovelace_configs=[lv_config],
    )
    plan = build_rename_plan_from_snapshot(snap, "switch.kitchen_plug", "switch.bedroom_lamp")
    lv_locs = [loc for loc in plan.locations if loc.context == "lovelace"]
    assert len(lv_locs) == 1
    assert lv_locs[0].name == "Home"


def test_build_rename_plan_from_snapshot_config_entry_match():
    """Lines 599-602: config_entry whose options contain the entity ID."""
    from zigporter.commands.rename import build_rename_plan_from_snapshot  # noqa: PLC0415

    snap = _make_snapshot(
        config_entries=[
            {
                "entry_id": "ce1",
                "title": "My Helper",
                "options": {"entity_id": "switch.kitchen_plug"},
            }
        ]
    )
    plan = build_rename_plan_from_snapshot(snap, "switch.kitchen_plug", "switch.bedroom_lamp")
    ce_locs = [loc for loc in plan.locations if loc.context == "config_entry"]
    assert len(ce_locs) == 1
    assert ce_locs[0].name == "My Helper"
    assert ce_locs[0].item_id == "ce1"


# ---------------------------------------------------------------------------
# find_device — lines 649-663: multiple partial matches → prompts user
# ---------------------------------------------------------------------------


async def test_find_device_multiple_partial_matches(mocker):
    """Lines 649-663: questionary.select is called when multiple devices partially match."""
    from zigporter.commands.rename import find_device  # noqa: PLC0415

    devices = [
        {"id": "d1", "name": "Kitchen Plug A", "name_by_user": None},
        {"id": "d2", "name": "Kitchen Plug B", "name_by_user": None},
    ]
    mock_ha = MagicMock()
    mock_ha.get_device_registry = AsyncMock(return_value=devices)

    selected_device = devices[1]
    mock_select = MagicMock()
    mock_select.unsafe_ask_async = AsyncMock(return_value=selected_device)
    mocker.patch("zigporter.commands.rename.questionary.select", return_value=mock_select)

    result = await find_device(mock_ha, "Kitchen Plug")
    assert result == selected_device
    assert mock_select.unsafe_ask_async.called


# ---------------------------------------------------------------------------
# _suggest_entity_id — lines 702-705: empty orig_slug
# ---------------------------------------------------------------------------


def test_suggest_entity_id_empty_orig_slug():
    """Lines 702-705: entity has no name/original_name → just domain.new_slug."""
    from zigporter.commands.rename import _suggest_entity_id  # noqa: PLC0415

    entity = {"entity_id": "switch.0x001234", "name": None, "original_name": None}
    result = _suggest_entity_id(entity, "bedroom_lamp")
    assert result == "switch.bedroom_lamp"


def test_suggest_entity_id_with_orig_slug():
    """When orig_name present → domain.new_slug_orig_slug."""
    from zigporter.commands.rename import _suggest_entity_id  # noqa: PLC0415

    entity = {"entity_id": "switch.kitchen_plug", "name": None, "original_name": "Power"}
    result = _suggest_entity_id(entity, "bedroom_lamp")
    assert result == "switch.bedroom_lamp_power"


# ---------------------------------------------------------------------------
# display_device_plan — lines 786-787, 791-795, 831: else-branch (no location_details)
# ---------------------------------------------------------------------------


def test_display_device_plan_no_location_details_with_scanned_and_energy(mocker):
    """Lines 786-787, 791-795: else-branch shows scanned counts and energy note."""
    from zigporter.commands.rename import DeviceRenamePlan, display_device_plan  # noqa: PLC0415

    device_plan = DeviceRenamePlan(
        device_id="dev1",
        old_device_name="Kitchen Plug",
        new_device_name="Bedroom Lamp",
        plans=[
            RenamePlan(
                old_entity_id="switch.kitchen_plug",
                new_entity_id="switch.bedroom_lamp",
                locations=[
                    RenameLocation(
                        context="registry",
                        name="HA entity registry",
                        item_id="switch.kitchen_plug",
                        occurrences=1,
                    )
                ],
            )
        ],
        scanned_names={
            "automations": ["Morning Routine"],
            "dashboards": ["Home Dashboard"],
        },
    )
    printed = []
    mocker.patch(
        "zigporter.commands.rename.console.print",
        side_effect=lambda *a, **kw: printed.append(str(a[0]) if a else ""),
    )
    display_device_plan(device_plan)
    all_output = "\n".join(printed)
    assert "Morning Routine" in all_output or "automations" in all_output
    assert "Home Dashboard" in all_output
    assert "energy" in all_output


def test_display_device_plan_multiple_failed_dashboards_spacer(mocker):
    """Line 831: spacer console.print() between 2+ failed dashboards."""
    from zigporter.commands.rename import DeviceRenamePlan, display_device_plan  # noqa: PLC0415

    device_plan = DeviceRenamePlan(
        device_id="dev1",
        old_device_name="Kitchen Plug",
        new_device_name="Bedroom Lamp",
        plans=[
            RenamePlan(
                old_entity_id="switch.kitchen_plug",
                new_entity_id="switch.bedroom_lamp",
                locations=[
                    RenameLocation(
                        context="registry",
                        name="HA entity registry",
                        item_id="switch.kitchen_plug",
                        occurrences=1,
                    )
                ],
            )
        ],
        failed_dashboards=["Dashboard A", "Dashboard B"],
        failed_dashboard_paths=[None, "mobile"],
    )
    printed = []
    mocker.patch(
        "zigporter.commands.rename.console.print",
        side_effect=lambda *a, **kw: printed.append(a[0] if a else ""),
    )
    display_device_plan(device_plan)
    assert any(a == "" for a in printed)


# ---------------------------------------------------------------------------
# execute_device_rename — lines 887-894: script/scene/lovelace/config_entry contexts
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_full_exec_client():
    client = MagicMock()
    client.rename_device_name = AsyncMock(return_value=None)
    client.rename_entity_id = AsyncMock(return_value=None)
    client.update_automation = AsyncMock(return_value=None)
    client.update_script = AsyncMock(return_value=None)
    client.update_scene = AsyncMock(return_value=None)
    client.save_lovelace_config = AsyncMock(return_value=None)
    client.update_config_entry_options = AsyncMock(return_value=None)
    return client


async def test_execute_device_rename_script_scene_lovelace_config_entry(mock_full_exec_client):
    """Lines 887-894: script, scene, lovelace and config_entry branches are called."""
    from zigporter.commands.rename import DeviceRenamePlan, execute_device_rename  # noqa: PLC0415

    plan = DeviceRenamePlan(
        device_id="dev1",
        old_device_name="Kitchen Plug",
        new_device_name="Bedroom Lamp",
        plans=[
            RenamePlan(
                old_entity_id="switch.kitchen_plug",
                new_entity_id="switch.bedroom_lamp",
                locations=[
                    RenameLocation(
                        context="registry",
                        name="HA entity registry",
                        item_id="switch.kitchen_plug",
                        occurrences=1,
                    ),
                    RenameLocation(
                        context="script",
                        name="My Script",
                        item_id="s1",
                        occurrences=1,
                        raw_config={"id": "s1", "entity_id": "switch.kitchen_plug"},
                    ),
                    RenameLocation(
                        context="scene",
                        name="Evening",
                        item_id="sc1",
                        occurrences=1,
                        raw_config={"id": "sc1", "entities": {"switch.kitchen_plug": {}}},
                    ),
                    RenameLocation(
                        context="lovelace",
                        name="Home",
                        item_id="",
                        occurrences=1,
                        raw_config={"views": [{"entity": "switch.kitchen_plug"}]},
                    ),
                    RenameLocation(
                        context="config_entry",
                        name="Helper",
                        item_id="ce1",
                        occurrences=1,
                        raw_config={"entity_id": "switch.kitchen_plug"},
                    ),
                ],
            )
        ],
    )
    await execute_device_rename(mock_full_exec_client, plan)
    mock_full_exec_client.update_script.assert_called_once()
    mock_full_exec_client.update_scene.assert_called_once()
    mock_full_exec_client.save_lovelace_config.assert_called_once()
    mock_full_exec_client.update_config_entry_options.assert_called_once()


async def test_execute_device_rename_lovelace_save_failure_warns_and_continues(
    mock_full_exec_client, mocker
):
    """save_lovelace_config raises RuntimeError → warns, does not crash, other updates continue."""
    from zigporter.commands.rename import DeviceRenamePlan, execute_device_rename  # noqa: PLC0415

    mock_full_exec_client.save_lovelace_config = AsyncMock(
        side_effect=RuntimeError("WebSocket command failed: Not supported")
    )
    printed = []
    mocker.patch(
        "zigporter.commands.rename.console.print",
        side_effect=lambda *a, **kw: printed.append(a[0] if a else ""),
    )
    plan = DeviceRenamePlan(
        device_id="dev1",
        old_device_name="Kitchen Plug",
        new_device_name="Bedroom Lamp",
        plans=[
            RenamePlan(
                old_entity_id="switch.kitchen_plug",
                new_entity_id="switch.bedroom_lamp",
                locations=[
                    RenameLocation(
                        context="lovelace",
                        name="Lights",
                        item_id="lights",
                        occurrences=1,
                        raw_config={"views": [{"entity": "switch.kitchen_plug"}]},
                    ),
                    RenameLocation(
                        context="config_entry",
                        name="Helper",
                        item_id="ce1",
                        occurrences=1,
                        raw_config={"entity_id": "switch.kitchen_plug"},
                    ),
                ],
            )
        ],
    )
    # Must not raise
    await execute_device_rename(mock_full_exec_client, plan)
    assert any("skipped" in str(p) for p in printed)
    # Other contexts still processed
    mock_full_exec_client.update_config_entry_options.assert_called_once()


# ---------------------------------------------------------------------------
# run_rename_device — various branches
# ---------------------------------------------------------------------------


def _make_snapshot_mock(entity_ids=None, automations=None, scripts=None, scenes=None):
    """Build a MagicMock snapshot for run_rename_device tests."""
    eids = entity_ids or ["switch.kitchen_plug"]
    return MagicMock(
        entity_registry=[{"entity_id": eid, "device_id": "dev1"} for eid in eids],
        automations=automations or [],
        scripts=scripts or [],
        scenes=scenes or [],
        url_paths=[None],
        titles={None: "Overview"},
        lovelace_configs=[None],
        config_entries=[],
    )


async def test_run_rename_device_fuzzy_match_prints_info(mocker):
    """Line 923: actual_name differs from query → prints fuzzy match info."""
    from zigporter.commands.rename import run_rename_device  # noqa: PLC0415

    device = {"id": "dev1", "name": "Kitchen Plug", "name_by_user": None}
    mock_instance = MagicMock()
    mock_instance.get_entities_for_device = AsyncMock(return_value=[])
    mocker.patch("zigporter.commands.rename.HAClient", return_value=mock_instance)
    mocker.patch("zigporter.commands.rename.find_device", new=AsyncMock(return_value=device))

    printed = []
    mocker.patch(
        "zigporter.commands.rename.console.print",
        side_effect=lambda *a, **kw: printed.append(str(a[0]) if a else ""),
    )
    # "kitchen" is a partial query that differs from "Kitchen Plug"
    result = await run_rename_device("https://ha.test", "token", True, "kitchen", "New Name", True)
    # Device found but no entities → returns True
    assert result is True
    all_output = "\n".join(printed)
    assert "Kitchen Plug" in all_output


async def test_run_rename_device_no_entities(mocker):
    """Lines 931-932: no entities found → prints yellow warning, returns True."""
    from zigporter.commands.rename import run_rename_device  # noqa: PLC0415

    device = {"id": "dev1", "name": "Kitchen Plug", "name_by_user": None}
    mock_instance = MagicMock()
    mock_instance.get_entities_for_device = AsyncMock(return_value=[])
    mocker.patch("zigporter.commands.rename.HAClient", return_value=mock_instance)
    mocker.patch("zigporter.commands.rename.find_device", new=AsyncMock(return_value=device))

    result = await run_rename_device(
        "https://ha.test", "token", True, "Kitchen Plug", "New Name", True
    )
    assert result is True


async def test_run_rename_device_odd_entities_no_tty(mocker):
    """Lines 941-945: odd entities exist but no TTY → skips them."""
    from zigporter.commands.rename import run_rename_device  # noqa: PLC0415

    device = {"id": "dev1", "name": "Kitchen Plug", "name_by_user": None}
    # Entity whose suffix doesn't contain "kitchen_plug" slug → goes to odd list
    entities = [
        {"entity_id": "switch.0xabcd1234", "name": None, "original_name": None, "device_id": "dev1"}
    ]
    mock_instance = MagicMock()
    mock_instance.get_entities_for_device = AsyncMock(return_value=entities)
    mocker.patch("zigporter.commands.rename.HAClient", return_value=mock_instance)
    mocker.patch("zigporter.commands.rename.find_device", new=AsyncMock(return_value=device))
    mocker.patch("zigporter.commands.rename.sys").stdin.isatty.return_value = False

    result = await run_rename_device(
        "https://ha.test", "token", True, "Kitchen Plug", "Bedroom Lamp", True
    )
    # No entity_pairs after skipping odds → returns True (no valid pairs)
    assert result is True


async def test_run_rename_device_odd_entities_interactive_suggested(mocker):
    """Lines 963-970: interactive: user picks suggested."""
    from zigporter.commands.rename import run_rename_device  # noqa: PLC0415

    device = {"id": "dev1", "name": "Kitchen Plug", "name_by_user": None}
    entities = [
        {"entity_id": "switch.0xabcd", "name": None, "original_name": None, "device_id": "dev1"}
    ]
    mock_instance = MagicMock()
    mock_instance.get_entities_for_device = AsyncMock(return_value=entities)
    mocker.patch("zigporter.commands.rename.HAClient", return_value=mock_instance)
    mocker.patch("zigporter.commands.rename.find_device", new=AsyncMock(return_value=device))

    mock_sys = mocker.patch("zigporter.commands.rename.sys")
    mock_sys.stdin.isatty.return_value = True

    suggested_val = "switch.bedroom_lamp"
    mock_select = MagicMock()
    mock_select.unsafe_ask_async = AsyncMock(return_value=("suggested", suggested_val))
    mocker.patch("zigporter.commands.rename.questionary.select", return_value=mock_select)

    snap = _make_snapshot_mock(entity_ids=["switch.0xabcd"])
    mocker.patch("zigporter.commands.rename.fetch_ha_snapshot", new=AsyncMock(return_value=snap))
    mock_plan = RenamePlan(
        old_entity_id="switch.0xabcd",
        new_entity_id="switch.bedroom_lamp",
        locations=[
            RenameLocation(
                context="registry",
                name="HA entity registry",
                item_id="switch.0xabcd",
                occurrences=1,
            )
        ],
    )
    mocker.patch(
        "zigporter.commands.rename.build_rename_plan_from_snapshot", return_value=mock_plan
    )
    mocker.patch("zigporter.commands.rename.execute_device_rename", new=AsyncMock())

    result = await run_rename_device(
        "https://ha.test", "token", True, "Kitchen Plug", "Bedroom Lamp", True
    )
    assert result is True


async def test_run_rename_device_odd_entities_interactive_custom(mocker):
    """Lines 965-970: interactive: user picks custom entity ID."""
    from zigporter.commands.rename import run_rename_device  # noqa: PLC0415

    device = {"id": "dev1", "name": "Kitchen Plug", "name_by_user": None}
    entities = [
        {"entity_id": "switch.0xabcd", "name": None, "original_name": None, "device_id": "dev1"}
    ]
    mock_instance = MagicMock()
    mock_instance.get_entities_for_device = AsyncMock(return_value=entities)
    mocker.patch("zigporter.commands.rename.HAClient", return_value=mock_instance)
    mocker.patch("zigporter.commands.rename.find_device", new=AsyncMock(return_value=device))

    mock_sys = mocker.patch("zigporter.commands.rename.sys")
    mock_sys.stdin.isatty.return_value = True

    mock_select = MagicMock()
    mock_select.unsafe_ask_async = AsyncMock(return_value=("custom", None))
    mocker.patch("zigporter.commands.rename.questionary.select", return_value=mock_select)

    mock_text = MagicMock()
    mock_text.unsafe_ask_async = AsyncMock(return_value="switch.my_custom_id")
    mocker.patch("zigporter.commands.rename.questionary.text", return_value=mock_text)

    snap = _make_snapshot_mock(entity_ids=["switch.0xabcd"])
    mocker.patch("zigporter.commands.rename.fetch_ha_snapshot", new=AsyncMock(return_value=snap))
    mock_plan = RenamePlan(
        old_entity_id="switch.0xabcd",
        new_entity_id="switch.my_custom_id",
        locations=[
            RenameLocation(
                context="registry",
                name="HA entity registry",
                item_id="switch.0xabcd",
                occurrences=1,
            )
        ],
    )
    mocker.patch(
        "zigporter.commands.rename.build_rename_plan_from_snapshot", return_value=mock_plan
    )
    mocker.patch("zigporter.commands.rename.execute_device_rename", new=AsyncMock())

    result = await run_rename_device(
        "https://ha.test", "token", True, "Kitchen Plug", "Bedroom Lamp", True
    )
    assert result is True


async def test_run_rename_device_entity_pairs_empty_after_odds(mocker):
    """Lines 973-974: entity_pairs empty after resolving odds → returns True."""
    from zigporter.commands.rename import run_rename_device  # noqa: PLC0415

    device = {"id": "dev1", "name": "Kitchen Plug", "name_by_user": None}
    entities = [
        {"entity_id": "switch.0xabcd", "name": None, "original_name": None, "device_id": "dev1"}
    ]
    mock_instance = MagicMock()
    mock_instance.get_entities_for_device = AsyncMock(return_value=entities)
    mocker.patch("zigporter.commands.rename.HAClient", return_value=mock_instance)
    mocker.patch("zigporter.commands.rename.find_device", new=AsyncMock(return_value=device))

    mock_sys = mocker.patch("zigporter.commands.rename.sys")
    mock_sys.stdin.isatty.return_value = True

    # User picks "skip" → entity_pairs stays empty
    mock_select = MagicMock()
    mock_select.unsafe_ask_async = AsyncMock(return_value=("skip", None))
    mocker.patch("zigporter.commands.rename.questionary.select", return_value=mock_select)

    result = await run_rename_device(
        "https://ha.test", "token", True, "Kitchen Plug", "Bedroom Lamp", True
    )
    assert result is True


async def test_run_rename_device_old_eid_not_in_registry_skipped(mocker):
    """Lines 985-988: old_eid not in registry → skip warning."""
    from zigporter.commands.rename import run_rename_device  # noqa: PLC0415

    device = {"id": "dev1", "name": "Kitchen Plug", "name_by_user": None}
    entities = [
        {
            "entity_id": "switch.kitchen_plug",
            "name": None,
            "original_name": None,
            "device_id": "dev1",
        }
    ]
    mock_instance = MagicMock()
    mock_instance.get_entities_for_device = AsyncMock(return_value=entities)
    mocker.patch("zigporter.commands.rename.HAClient", return_value=mock_instance)
    mocker.patch("zigporter.commands.rename.find_device", new=AsyncMock(return_value=device))

    # Snapshot without "switch.kitchen_plug" in entity_registry → triggers 985-988
    snap = MagicMock(
        entity_registry=[{"entity_id": "switch.other", "device_id": "dev2"}],
        automations=[],
        scripts=[],
        scenes=[],
        url_paths=[None],
        titles={None: "Overview"},
        lovelace_configs=[None],
        config_entries=[],
    )
    mocker.patch("zigporter.commands.rename.fetch_ha_snapshot", new=AsyncMock(return_value=snap))

    printed = []
    mocker.patch(
        "zigporter.commands.rename.console.print",
        side_effect=lambda *a, **kw: printed.append(str(a[0]) if a else ""),
    )

    result = await run_rename_device(
        "https://ha.test", "token", True, "Kitchen Plug", "Bedroom Lamp", True
    )
    assert result is False
    assert any("not found" in p for p in printed)


async def test_run_rename_device_new_eid_already_exists_skipped(mocker):
    """Lines 990-991: new_eid already exists → skip warning, no valid plans → False."""
    from zigporter.commands.rename import run_rename_device  # noqa: PLC0415

    device = {"id": "dev1", "name": "Kitchen Plug", "name_by_user": None}
    entities = [
        {
            "entity_id": "switch.kitchen_plug",
            "name": None,
            "original_name": None,
            "device_id": "dev1",
        }
    ]
    mock_instance = MagicMock()
    mock_instance.get_entities_for_device = AsyncMock(return_value=entities)
    mocker.patch("zigporter.commands.rename.HAClient", return_value=mock_instance)
    mocker.patch("zigporter.commands.rename.find_device", new=AsyncMock(return_value=device))

    # Both old and new already in registry → new_eid exists → skipped
    snap = MagicMock(
        entity_registry=[
            {"entity_id": "switch.kitchen_plug", "device_id": "dev1"},
            {"entity_id": "switch.bedroom_lamp", "device_id": "dev2"},
        ],
        automations=[],
        scripts=[],
        scenes=[],
        url_paths=[None],
        titles={None: "Overview"},
        lovelace_configs=[None],
        config_entries=[],
    )
    mocker.patch("zigporter.commands.rename.fetch_ha_snapshot", new=AsyncMock(return_value=snap))

    printed = []
    mocker.patch(
        "zigporter.commands.rename.console.print",
        side_effect=lambda *a, **kw: printed.append(str(a[0]) if a else ""),
    )

    result = await run_rename_device(
        "https://ha.test", "token", True, "Kitchen Plug", "Bedroom Lamp", True
    )
    assert result is False
    assert any("already exists" in p for p in printed)


async def test_run_rename_device_no_valid_plans(mocker):
    """Lines 995-996: no valid plans after filtering → returns False."""
    from zigporter.commands.rename import run_rename_device  # noqa: PLC0415

    device = {"id": "dev1", "name": "Kitchen Plug", "name_by_user": None}
    entities = [
        {
            "entity_id": "switch.kitchen_plug",
            "name": None,
            "original_name": None,
            "device_id": "dev1",
        }
    ]
    mock_instance = MagicMock()
    mock_instance.get_entities_for_device = AsyncMock(return_value=entities)
    mocker.patch("zigporter.commands.rename.HAClient", return_value=mock_instance)
    mocker.patch("zigporter.commands.rename.find_device", new=AsyncMock(return_value=device))

    # Snapshot has old_eid but also new_eid → all pairs skipped → plans empty
    snap = MagicMock(
        entity_registry=[
            {"entity_id": "switch.kitchen_plug", "device_id": "dev1"},
            {"entity_id": "switch.bedroom_lamp", "device_id": "dev2"},
        ],
        automations=[],
        scripts=[],
        scenes=[],
        url_paths=[None],
        titles={None: "Overview"},
        lovelace_configs=[None],
        config_entries=[],
    )
    mocker.patch("zigporter.commands.rename.fetch_ha_snapshot", new=AsyncMock(return_value=snap))
    # build_rename_plan_from_snapshot raises because new_eid already exists → skip
    mocker.patch(
        "zigporter.commands.rename.build_rename_plan_from_snapshot",
        side_effect=ValueError("already exists"),
    )

    result = await run_rename_device(
        "https://ha.test", "token", True, "Kitchen Plug", "Bedroom Lamp", True
    )
    assert result is False


# ---------------------------------------------------------------------------
# find_device — exact match, no match, single partial match
# ---------------------------------------------------------------------------


async def test_find_device_exact_match():
    """Line 654: exactly 1 exact match returns it directly."""
    from zigporter.commands.rename import find_device  # noqa: PLC0415

    devices = [
        {"id": "d1", "name": "Kitchen Plug", "name_by_user": None},
        {"id": "d2", "name": "Bedroom Lamp", "name_by_user": None},
    ]
    mock_ha = MagicMock()
    mock_ha.get_device_registry = AsyncMock(return_value=devices)
    result = await find_device(mock_ha, "Kitchen Plug")
    assert result == devices[0]


async def test_find_device_no_match():
    """Line 658: no partial matches returns None."""
    from zigporter.commands.rename import find_device  # noqa: PLC0415

    mock_ha = MagicMock()
    mock_ha.get_device_registry = AsyncMock(
        return_value=[
            {"id": "d1", "name": "Bedroom Lamp", "name_by_user": None},
        ]
    )
    result = await find_device(mock_ha, "nonexistent")
    assert result is None


async def test_find_device_single_partial_match():
    """Line 660: exactly 1 partial match returns it directly."""
    from zigporter.commands.rename import find_device  # noqa: PLC0415

    mock_ha = MagicMock()
    mock_ha.get_device_registry = AsyncMock(
        return_value=[
            {"id": "d1", "name": "Kitchen Plug 2000", "name_by_user": None},
        ]
    )
    result = await find_device(mock_ha, "kitchen")
    assert result["id"] == "d1"


# ---------------------------------------------------------------------------
# run_rename_device — scanned_names population and dry-run confirm paths
# ---------------------------------------------------------------------------


async def test_run_rename_device_scanned_names_populated(mocker):
    """Lines 1000-1023: automations/scripts/scenes/dashboards populate scanned_names."""
    from zigporter.commands.rename import run_rename_device  # noqa: PLC0415

    device = {"id": "dev1", "name": "Kitchen Plug", "name_by_user": None}
    entities = [
        {
            "entity_id": "switch.kitchen_plug",
            "name": None,
            "original_name": None,
            "device_id": "dev1",
        }
    ]
    mock_instance = MagicMock()
    mock_instance.get_entities_for_device = AsyncMock(return_value=entities)
    mocker.patch("zigporter.commands.rename.HAClient", return_value=mock_instance)
    mocker.patch("zigporter.commands.rename.find_device", new=AsyncMock(return_value=device))

    snap = MagicMock(
        entity_registry=[{"entity_id": "switch.kitchen_plug", "device_id": "dev1"}],
        automations=[{"id": "a1", "alias": "Auto 1"}],
        scripts=[{"id": "s1", "alias": "Script 1"}],
        scenes=[{"id": "sc1", "name": "Scene 1"}],
        url_paths=[None],
        titles={None: "Overview"},
        lovelace_configs=[{"views": []}],
        config_entries=[],
    )
    mocker.patch("zigporter.commands.rename.fetch_ha_snapshot", new=AsyncMock(return_value=snap))
    mock_plan = RenamePlan(
        old_entity_id="switch.kitchen_plug",
        new_entity_id="switch.bedroom_lamp",
        locations=[
            RenameLocation(
                context="registry",
                name="HA entity registry",
                item_id="switch.kitchen_plug",
                occurrences=1,
            )
        ],
    )
    mocker.patch(
        "zigporter.commands.rename.build_rename_plan_from_snapshot", return_value=mock_plan
    )
    mocker.patch("zigporter.commands.rename.execute_device_rename", new=AsyncMock())

    result = await run_rename_device(
        "https://ha.test", "token", True, "Kitchen Plug", "Bedroom Lamp", True
    )
    assert result is True


async def test_run_rename_device_dry_run_no_tty(mocker):
    """Lines 1040-1045: dry-run without TTY returns False."""
    from zigporter.commands.rename import run_rename_device  # noqa: PLC0415

    device = {"id": "dev1", "name": "Kitchen Plug", "name_by_user": None}
    entities = [
        {
            "entity_id": "switch.kitchen_plug",
            "name": None,
            "original_name": None,
            "device_id": "dev1",
        }
    ]
    mock_instance = MagicMock()
    mock_instance.get_entities_for_device = AsyncMock(return_value=entities)
    mocker.patch("zigporter.commands.rename.HAClient", return_value=mock_instance)
    mocker.patch("zigporter.commands.rename.find_device", new=AsyncMock(return_value=device))

    snap = MagicMock(
        entity_registry=[{"entity_id": "switch.kitchen_plug", "device_id": "dev1"}],
        automations=[],
        scripts=[],
        scenes=[],
        url_paths=[None],
        titles={None: "Overview"},
        lovelace_configs=[None],
        config_entries=[],
    )
    mocker.patch("zigporter.commands.rename.fetch_ha_snapshot", new=AsyncMock(return_value=snap))
    mock_plan = RenamePlan(
        old_entity_id="switch.kitchen_plug",
        new_entity_id="switch.bedroom_lamp",
        locations=[
            RenameLocation(
                context="registry",
                name="HA entity registry",
                item_id="switch.kitchen_plug",
                occurrences=1,
            )
        ],
    )
    mocker.patch(
        "zigporter.commands.rename.build_rename_plan_from_snapshot", return_value=mock_plan
    )
    mock_sys = mocker.patch("zigporter.commands.rename.sys")
    mock_sys.stdin.isatty.return_value = False

    result = await run_rename_device(
        "https://ha.test", "token", True, "Kitchen Plug", "Bedroom Lamp", False
    )
    assert result is False


async def test_run_rename_device_dry_run_aborted(mocker):
    """Lines 1046-1051: dry-run with TTY, user aborts → returns True."""
    from zigporter.commands.rename import run_rename_device  # noqa: PLC0415

    device = {"id": "dev1", "name": "Kitchen Plug", "name_by_user": None}
    entities = [
        {
            "entity_id": "switch.kitchen_plug",
            "name": None,
            "original_name": None,
            "device_id": "dev1",
        }
    ]
    mock_instance = MagicMock()
    mock_instance.get_entities_for_device = AsyncMock(return_value=entities)
    mocker.patch("zigporter.commands.rename.HAClient", return_value=mock_instance)
    mocker.patch("zigporter.commands.rename.find_device", new=AsyncMock(return_value=device))

    snap = MagicMock(
        entity_registry=[{"entity_id": "switch.kitchen_plug", "device_id": "dev1"}],
        automations=[],
        scripts=[],
        scenes=[],
        url_paths=[None],
        titles={None: "Overview"},
        lovelace_configs=[None],
        config_entries=[],
    )
    mocker.patch("zigporter.commands.rename.fetch_ha_snapshot", new=AsyncMock(return_value=snap))
    mock_plan = RenamePlan(
        old_entity_id="switch.kitchen_plug",
        new_entity_id="switch.bedroom_lamp",
        locations=[
            RenameLocation(
                context="registry",
                name="HA entity registry",
                item_id="switch.kitchen_plug",
                occurrences=1,
            )
        ],
    )
    mocker.patch(
        "zigporter.commands.rename.build_rename_plan_from_snapshot", return_value=mock_plan
    )
    mock_sys = mocker.patch("zigporter.commands.rename.sys")
    mock_sys.stdin.isatty.return_value = True
    mock_confirm = MagicMock()
    mock_confirm.unsafe_ask_async = AsyncMock(return_value=False)
    mocker.patch("zigporter.commands.rename.questionary.confirm", return_value=mock_confirm)

    result = await run_rename_device(
        "https://ha.test", "token", True, "Kitchen Plug", "Bedroom Lamp", False
    )
    assert result is True


# ---------------------------------------------------------------------------
# _ieee_from_ha_device
# ---------------------------------------------------------------------------


def test_ieee_from_ha_device_returns_ieee():
    from zigporter.commands.rename import _ieee_from_ha_device  # noqa: PLC0415

    device = {"identifiers": [["mqtt", "zigbee2mqtt_0x001234567890abcd"]]}
    assert _ieee_from_ha_device(device) == "0x001234567890abcd"


def test_ieee_from_ha_device_case_insensitive():
    from zigporter.commands.rename import _ieee_from_ha_device  # noqa: PLC0415

    device = {"identifiers": [["mqtt", "Zigbee2MQTT_0xABCDEF"]]}
    assert _ieee_from_ha_device(device) == "0xabcdef"


def test_ieee_from_ha_device_skips_non_z2m_identifiers():
    from zigporter.commands.rename import _ieee_from_ha_device  # noqa: PLC0415

    device = {"identifiers": [["zha", "00:11:22:33:44:55:66:77"], ["mqtt", "other_device_123"]]}
    assert _ieee_from_ha_device(device) is None


def test_ieee_from_ha_device_empty_identifiers():
    from zigporter.commands.rename import _ieee_from_ha_device  # noqa: PLC0415

    assert _ieee_from_ha_device({"identifiers": []}) is None
    assert _ieee_from_ha_device({}) is None


def test_ieee_from_ha_device_uses_first_z2m_match():
    from zigporter.commands.rename import _ieee_from_ha_device  # noqa: PLC0415

    device = {
        "identifiers": [
            ["other", "something"],
            ["mqtt", "zigbee2mqtt_0xaabbccdd"],
        ]
    }
    assert _ieee_from_ha_device(device) == "0xaabbccdd"


# ---------------------------------------------------------------------------
# execute_device_rename — Z2M sync
# ---------------------------------------------------------------------------


async def test_execute_device_rename_calls_z2m_rename(mock_device_exec_client):
    """When z2m_client and z2m_friendly_name are provided, rename_device is called."""
    from zigporter.commands.rename import DeviceRenamePlan, RenameLocation, RenamePlan  # noqa: PLC0415
    from zigporter.commands.rename import execute_device_rename  # noqa: PLC0415

    mock_z2m = MagicMock()
    mock_z2m.rename_device = AsyncMock(return_value=None)

    plan = DeviceRenamePlan(
        device_id="dev1",
        old_device_name="Kitchen Plug",
        new_device_name="Window Left Plug",
        plans=[
            RenamePlan(
                old_entity_id="switch.kitchen_plug",
                new_entity_id="switch.window_left_plug",
                locations=[
                    RenameLocation(
                        context="registry",
                        name="HA entity registry",
                        item_id="switch.kitchen_plug",
                        occurrences=1,
                    )
                ],
            )
        ],
    )
    await execute_device_rename(
        mock_device_exec_client, plan, z2m_client=mock_z2m, z2m_friendly_name="KitchenPlug"
    )
    mock_z2m.rename_device.assert_called_once_with("KitchenPlug", "Window Left Plug")


async def test_execute_device_rename_z2m_failure_does_not_abort(mock_device_exec_client):
    """A Z2M rename failure prints a warning but does not raise."""
    from zigporter.commands.rename import DeviceRenamePlan, RenameLocation, RenamePlan  # noqa: PLC0415
    from zigporter.commands.rename import execute_device_rename  # noqa: PLC0415

    mock_z2m = MagicMock()
    mock_z2m.rename_device = AsyncMock(side_effect=RuntimeError("Z2M unreachable"))

    plan = DeviceRenamePlan(
        device_id="dev1",
        old_device_name="Kitchen Plug",
        new_device_name="Window Left Plug",
        plans=[
            RenamePlan(
                old_entity_id="switch.kitchen_plug",
                new_entity_id="switch.window_left_plug",
                locations=[
                    RenameLocation(
                        context="registry",
                        name="HA entity registry",
                        item_id="switch.kitchen_plug",
                        occurrences=1,
                    )
                ],
            )
        ],
    )
    # Should not raise
    await execute_device_rename(
        mock_device_exec_client, plan, z2m_client=mock_z2m, z2m_friendly_name="KitchenPlug"
    )
    mock_device_exec_client.rename_device_name.assert_called_once()


async def test_execute_device_rename_no_z2m_params_skips(mock_device_exec_client):
    """When z2m_client/z2m_friendly_name are None, Z2M rename is never called."""
    from zigporter.commands.rename import DeviceRenamePlan, RenameLocation, RenamePlan  # noqa: PLC0415
    from zigporter.commands.rename import execute_device_rename  # noqa: PLC0415

    mock_z2m = MagicMock()
    mock_z2m.rename_device = AsyncMock(return_value=None)

    plan = DeviceRenamePlan(
        device_id="dev1",
        old_device_name="Kitchen Plug",
        new_device_name="Window Left Plug",
        plans=[
            RenamePlan(
                old_entity_id="switch.kitchen_plug",
                new_entity_id="switch.window_left_plug",
                locations=[
                    RenameLocation(
                        context="registry",
                        name="HA entity registry",
                        item_id="switch.kitchen_plug",
                        occurrences=1,
                    )
                ],
            )
        ],
    )
    # z2m_client provided but z2m_friendly_name is None → skip
    await execute_device_rename(
        mock_device_exec_client, plan, z2m_client=mock_z2m, z2m_friendly_name=None
    )
    mock_z2m.rename_device.assert_not_called()


async def test_execute_device_rename_reloads_z2m_integration_after_rename(mock_device_exec_client):
    """After a successful Z2M rename, the Z2M config entry is reloaded in HA."""
    from zigporter.commands.rename import DeviceRenamePlan, RenameLocation, RenamePlan  # noqa: PLC0415
    from zigporter.commands.rename import execute_device_rename  # noqa: PLC0415

    mock_z2m = MagicMock()
    mock_z2m.rename_device = AsyncMock(return_value=None)

    plan = DeviceRenamePlan(
        device_id="dev1",
        old_device_name="Kitchen Plug",
        new_device_name="Window Left Plug",
        plans=[
            RenamePlan(
                old_entity_id="switch.kitchen_plug",
                new_entity_id="switch.window_left_plug",
                locations=[
                    RenameLocation(
                        context="registry",
                        name="HA entity registry",
                        item_id="switch.kitchen_plug",
                        occurrences=1,
                    )
                ],
            )
        ],
    )
    await execute_device_rename(
        mock_device_exec_client, plan, z2m_client=mock_z2m, z2m_friendly_name="KitchenPlug"
    )
    mock_device_exec_client.get_z2m_config_entry_id.assert_called_once()
    mock_device_exec_client.reload_config_entry.assert_called_once_with("z2m-entry-1")


async def test_execute_device_rename_reload_skipped_when_entry_not_found(mock_device_exec_client):
    """When get_z2m_config_entry_id returns None, reload is silently skipped."""
    from zigporter.commands.rename import DeviceRenamePlan, RenameLocation, RenamePlan  # noqa: PLC0415
    from zigporter.commands.rename import execute_device_rename  # noqa: PLC0415

    mock_device_exec_client.get_z2m_config_entry_id = AsyncMock(return_value=None)
    mock_z2m = MagicMock()
    mock_z2m.rename_device = AsyncMock(return_value=None)

    plan = DeviceRenamePlan(
        device_id="dev1",
        old_device_name="Kitchen Plug",
        new_device_name="Window Left Plug",
        plans=[
            RenamePlan(
                old_entity_id="switch.kitchen_plug",
                new_entity_id="switch.window_left_plug",
                locations=[
                    RenameLocation(
                        context="registry",
                        name="HA entity registry",
                        item_id="switch.kitchen_plug",
                        occurrences=1,
                    )
                ],
            )
        ],
    )
    await execute_device_rename(
        mock_device_exec_client, plan, z2m_client=mock_z2m, z2m_friendly_name="KitchenPlug"
    )
    mock_device_exec_client.reload_config_entry.assert_not_called()


async def test_execute_device_rename_reload_skipped_on_z2m_failure(mock_device_exec_client):
    """When the Z2M rename fails, the HA integration reload is not attempted."""
    from zigporter.commands.rename import DeviceRenamePlan, RenameLocation, RenamePlan  # noqa: PLC0415
    from zigporter.commands.rename import execute_device_rename  # noqa: PLC0415

    mock_z2m = MagicMock()
    mock_z2m.rename_device = AsyncMock(side_effect=RuntimeError("Z2M unreachable"))

    plan = DeviceRenamePlan(
        device_id="dev1",
        old_device_name="Kitchen Plug",
        new_device_name="Window Left Plug",
        plans=[
            RenamePlan(
                old_entity_id="switch.kitchen_plug",
                new_entity_id="switch.window_left_plug",
                locations=[
                    RenameLocation(
                        context="registry",
                        name="HA entity registry",
                        item_id="switch.kitchen_plug",
                        occurrences=1,
                    )
                ],
            )
        ],
    )
    await execute_device_rename(
        mock_device_exec_client, plan, z2m_client=mock_z2m, z2m_friendly_name="KitchenPlug"
    )
    mock_device_exec_client.get_z2m_config_entry_id.assert_not_called()
    mock_device_exec_client.reload_config_entry.assert_not_called()


async def test_execute_device_rename_reload_config_entry_failure(mock_device_exec_client):
    """When reload_config_entry raises, the error is swallowed — rename still succeeds."""
    from zigporter.commands.rename import DeviceRenamePlan, RenameLocation, RenamePlan  # noqa: PLC0415
    from zigporter.commands.rename import execute_device_rename  # noqa: PLC0415

    mock_device_exec_client.reload_config_entry = AsyncMock(
        side_effect=RuntimeError("config entry not found")
    )
    mock_z2m = MagicMock()
    mock_z2m.rename_device = AsyncMock(return_value=None)

    plan = DeviceRenamePlan(
        device_id="dev1",
        old_device_name="Kitchen Plug",
        new_device_name="Window Left Plug",
        plans=[
            RenamePlan(
                old_entity_id="switch.kitchen_plug",
                new_entity_id="switch.window_left_plug",
                locations=[
                    RenameLocation(
                        context="registry",
                        name="HA entity registry",
                        item_id="switch.kitchen_plug",
                        occurrences=1,
                    )
                ],
            )
        ],
    )
    # Must not raise
    await execute_device_rename(
        mock_device_exec_client, plan, z2m_client=mock_z2m, z2m_friendly_name="KitchenPlug"
    )
    mock_device_exec_client.rename_device_name.assert_called_once()


# ---------------------------------------------------------------------------
# run_rename_device — Z2M sync edge cases
# ---------------------------------------------------------------------------


def _make_z2m_test_device(ieee: str | None = "0x001234567890abcd") -> dict:
    """HA device dict with a Z2M MQTT identifier (or without if ieee is None)."""
    identifiers = []
    if ieee:
        identifiers.append(["mqtt", f"zigbee2mqtt_{ieee}"])
    return {"id": "dev1", "name": "Kitchen Plug", "name_by_user": None, "identifiers": identifiers}


def _make_z2m_run_mocks(mocker, device, execute_mock=None):
    """Common mocks for run_rename_device Z2M tests."""
    mock_ha = MagicMock()
    mock_ha.get_entities_for_device = AsyncMock(
        return_value=[
            {
                "entity_id": "switch.kitchen_plug",
                "original_name": "Kitchen Plug",
                "device_id": "dev1",
            }
        ]
    )
    mocker.patch("zigporter.commands.rename.HAClient", return_value=mock_ha)
    mocker.patch("zigporter.commands.rename.find_device", new=AsyncMock(return_value=device))
    mocker.patch(
        "zigporter.commands.rename.fetch_ha_snapshot",
        new=AsyncMock(return_value=_make_snapshot_mock(["switch.kitchen_plug"])),
    )
    mocker.patch(
        "zigporter.commands.rename.build_rename_plan_from_snapshot",
        return_value=RenamePlan(
            old_entity_id="switch.kitchen_plug",
            new_entity_id="switch.window_left_plug",
            locations=[
                RenameLocation(
                    context="registry",
                    name="HA entity registry",
                    item_id="switch.kitchen_plug",
                    occurrences=1,
                )
            ],
        ),
    )
    exec_mock = execute_mock or AsyncMock()
    mocker.patch("zigporter.commands.rename.execute_device_rename", new=exec_mock)
    return exec_mock


async def test_run_rename_device_z2m_user_confirms(mocker):
    """Interactive: user says yes to Z2M prompt → execute called with z2m params."""
    from zigporter.commands.rename import run_rename_device  # noqa: PLC0415

    device = _make_z2m_test_device("0x001234567890abcd")
    exec_mock = _make_z2m_run_mocks(mocker, device)

    mock_z2m_instance = MagicMock()
    mock_z2m_instance.get_device_by_ieee = AsyncMock(
        return_value={"friendly_name": "KitchenPlug", "ieee_address": "0x001234567890abcd"}
    )
    mock_z2m_cls = mocker.patch(
        "zigporter.commands.rename.Z2MClient", return_value=mock_z2m_instance
    )
    mocker.patch(
        "zigporter.commands.rename.load_z2m_config", return_value=("http://z2m.test", "zigbee2mqtt")
    )
    mocker.patch("zigporter.commands.rename.sys").stdin.isatty.return_value = True
    # First confirm = "Apply HA changes?", second = "Also rename in Z2M?"
    mock_confirm = MagicMock()
    mock_confirm.unsafe_ask_async = AsyncMock(side_effect=[True, True])
    mocker.patch("zigporter.commands.rename.questionary.confirm", return_value=mock_confirm)

    result = await run_rename_device(
        "https://ha.test", "token", True, "Kitchen Plug", "Window Left Plug", False
    )

    assert result is True
    mock_z2m_cls.assert_called_once_with(
        "https://ha.test", "token", "http://z2m.test", True, "zigbee2mqtt"
    )
    mock_z2m_instance.get_device_by_ieee.assert_called_once_with("0x001234567890abcd")
    _, kwargs = exec_mock.call_args
    assert kwargs["z2m_friendly_name"] == "KitchenPlug"
    assert kwargs["z2m_client"] is mock_z2m_instance


async def test_run_rename_device_z2m_user_declines(mocker):
    """Interactive: user says no to Z2M prompt → execute called without z2m params."""
    from zigporter.commands.rename import run_rename_device  # noqa: PLC0415

    device = _make_z2m_test_device("0x001234567890abcd")
    exec_mock = _make_z2m_run_mocks(mocker, device)

    mock_z2m_instance = MagicMock()
    mock_z2m_instance.get_device_by_ieee = AsyncMock(
        return_value={"friendly_name": "KitchenPlug", "ieee_address": "0x001234567890abcd"}
    )
    mocker.patch("zigporter.commands.rename.Z2MClient", return_value=mock_z2m_instance)
    mocker.patch(
        "zigporter.commands.rename.load_z2m_config", return_value=("http://z2m.test", "zigbee2mqtt")
    )
    mocker.patch("zigporter.commands.rename.sys").stdin.isatty.return_value = True
    mock_confirm = MagicMock()
    mock_confirm.unsafe_ask_async = AsyncMock(side_effect=[True, False])  # yes HA, no Z2M
    mocker.patch("zigporter.commands.rename.questionary.confirm", return_value=mock_confirm)

    result = await run_rename_device(
        "https://ha.test", "token", True, "Kitchen Plug", "Window Left Plug", False
    )

    assert result is True
    _, kwargs = exec_mock.call_args
    assert kwargs["z2m_client"] is None
    assert kwargs["z2m_friendly_name"] is None


async def test_run_rename_device_z2m_apply_mode_skips_z2m(mocker):
    """--apply mode: Z2M lookup is never attempted (no network call, no prompt)."""
    from zigporter.commands.rename import run_rename_device  # noqa: PLC0415

    device = _make_z2m_test_device("0x001234567890abcd")
    exec_mock = _make_z2m_run_mocks(mocker, device)

    mock_z2m_cls = mocker.patch("zigporter.commands.rename.Z2MClient")
    mock_load_z2m = mocker.patch("zigporter.commands.rename.load_z2m_config")

    result = await run_rename_device(
        "https://ha.test",
        "token",
        True,
        "Kitchen Plug",
        "Window Left Plug",
        True,  # apply=True
    )

    assert result is True
    mock_load_z2m.assert_not_called()
    mock_z2m_cls.assert_not_called()
    _, kwargs = exec_mock.call_args
    assert kwargs["z2m_client"] is None
    assert kwargs["z2m_friendly_name"] is None


async def test_run_rename_device_z2m_not_configured(mocker):
    """load_z2m_config raises ValueError → Z2M step silently skipped."""
    from zigporter.commands.rename import run_rename_device  # noqa: PLC0415

    device = _make_z2m_test_device("0x001234567890abcd")
    exec_mock = _make_z2m_run_mocks(mocker, device)
    mocker.patch(
        "zigporter.commands.rename.load_z2m_config", side_effect=ValueError("Z2M_URL not set")
    )

    result = await run_rename_device(
        "https://ha.test", "token", True, "Kitchen Plug", "Window Left Plug", True
    )

    assert result is True
    _, kwargs = exec_mock.call_args
    assert kwargs["z2m_client"] is None
    assert kwargs["z2m_friendly_name"] is None


async def test_run_rename_device_no_z2m_identifier(mocker):
    """Device has no Z2M MQTT identifier → Z2M lookup never attempted."""
    from zigporter.commands.rename import run_rename_device  # noqa: PLC0415

    device = _make_z2m_test_device(ieee=None)  # no Z2M identifier
    exec_mock = _make_z2m_run_mocks(mocker, device)
    mock_z2m_cls = mocker.patch("zigporter.commands.rename.Z2MClient")
    mocker.patch(
        "zigporter.commands.rename.load_z2m_config", return_value=("http://z2m.test", "zigbee2mqtt")
    )

    result = await run_rename_device(
        "https://ha.test", "token", True, "Kitchen Plug", "Window Left Plug", True
    )

    assert result is True
    mock_z2m_cls.assert_not_called()
    _, kwargs = exec_mock.call_args
    assert kwargs["z2m_client"] is None
    assert kwargs["z2m_friendly_name"] is None


async def test_run_rename_device_z2m_device_not_found_in_z2m(mocker):
    """get_device_by_ieee returns None → execute called without Z2M params."""
    from zigporter.commands.rename import run_rename_device  # noqa: PLC0415

    device = _make_z2m_test_device("0x001234567890abcd")
    exec_mock = _make_z2m_run_mocks(mocker, device)

    mock_z2m_instance = MagicMock()
    mock_z2m_instance.get_device_by_ieee = AsyncMock(return_value=None)
    mocker.patch("zigporter.commands.rename.Z2MClient", return_value=mock_z2m_instance)
    mocker.patch(
        "zigporter.commands.rename.load_z2m_config", return_value=("http://z2m.test", "zigbee2mqtt")
    )

    result = await run_rename_device(
        "https://ha.test", "token", True, "Kitchen Plug", "Window Left Plug", True
    )

    assert result is True
    _, kwargs = exec_mock.call_args
    # z2m_client is instantiated but z2m_friendly_name is None → no Z2M rename
    assert kwargs["z2m_friendly_name"] is None


async def test_run_rename_device_z2m_lookup_exception_skipped(mocker):
    """Any exception during Z2M lookup is silently swallowed."""
    from zigporter.commands.rename import run_rename_device  # noqa: PLC0415

    device = _make_z2m_test_device("0x001234567890abcd")
    exec_mock = _make_z2m_run_mocks(mocker, device)
    mocker.patch(
        "zigporter.commands.rename.load_z2m_config", return_value=("http://z2m.test", "zigbee2mqtt")
    )
    mock_z2m_instance = MagicMock()
    mock_z2m_instance.get_device_by_ieee = AsyncMock(side_effect=RuntimeError("network timeout"))
    mocker.patch("zigporter.commands.rename.Z2MClient", return_value=mock_z2m_instance)

    result = await run_rename_device(
        "https://ha.test", "token", True, "Kitchen Plug", "Window Left Plug", True
    )

    assert result is True
    _, kwargs = exec_mock.call_args
    # z2m_client may be instantiated but z2m_friendly_name is None → no Z2M rename
    assert kwargs["z2m_friendly_name"] is None


async def test_run_rename_device_z2m_lookup_exception_interactive(mocker):
    """Z2M lookup exception in interactive (apply=False) mode is silently swallowed."""
    from zigporter.commands.rename import run_rename_device  # noqa: PLC0415

    device = _make_z2m_test_device("0x001234567890abcd")
    exec_mock = _make_z2m_run_mocks(mocker, device)
    mocker.patch(
        "zigporter.commands.rename.load_z2m_config", return_value=("http://z2m.test", "zigbee2mqtt")
    )
    mock_z2m_instance = MagicMock()
    mock_z2m_instance.get_device_by_ieee = AsyncMock(side_effect=OSError("connection refused"))
    mocker.patch("zigporter.commands.rename.Z2MClient", return_value=mock_z2m_instance)
    mocker.patch("zigporter.commands.rename.sys").stdin.isatty.return_value = True
    mock_confirm = MagicMock()
    mock_confirm.unsafe_ask_async = AsyncMock(return_value=True)
    mocker.patch("zigporter.commands.rename.questionary.confirm", return_value=mock_confirm)

    result = await run_rename_device(
        "https://ha.test", "token", True, "Kitchen Plug", "Window Left Plug", False
    )

    assert result is True
    _, kwargs = exec_mock.call_args
    assert kwargs["z2m_friendly_name"] is None


# ---------------------------------------------------------------------------
# config_entry scanning — build_rename_plan
# ---------------------------------------------------------------------------


async def test_build_rename_plan_finds_config_entry(mock_ha_client):
    mock_ha_client.get_config_entries = AsyncMock(
        return_value=[
            {
                "entry_id": "helper-group-1",
                "title": "Bogus Lights",
                "options": {"entities": ["switch.kitchen_plug", "light.hall"]},
            }
        ]
    )
    plan = await build_rename_plan(mock_ha_client, "switch.kitchen_plug", "switch.new_plug")
    ce_locs = [loc for loc in plan.locations if loc.context == "config_entry"]
    assert len(ce_locs) == 1
    assert ce_locs[0].item_id == "helper-group-1"
    assert ce_locs[0].name == "Bogus Lights"
    assert ce_locs[0].occurrences >= 1


async def test_execute_rename_patches_config_entry(mock_exec_client):
    options = {"entities": ["switch.old", "light.hall"]}
    plan = RenamePlan(
        old_entity_id="switch.old",
        new_entity_id="switch.new",
        locations=[
            RenameLocation(
                context="config_entry",
                name="Bogus Lights",
                item_id="helper-group-1",
                occurrences=1,
                raw_config=options,
            )
        ],
    )
    await execute_rename(mock_exec_client, plan)
    mock_exec_client.update_config_entry_options.assert_called_once()
    call_entry_id, call_options = mock_exec_client.update_config_entry_options.call_args[0]
    assert call_entry_id == "helper-group-1"
    assert "switch.new" in call_options["entities"]
    assert "switch.old" not in call_options["entities"]


# ---------------------------------------------------------------------------
# _suggest_entity_ids
# ---------------------------------------------------------------------------


def test_suggest_entity_ids_matches_by_name():
    registry = [
        {"entity_id": "light.bogus_lights", "name_by_user": "bogus lights", "name": None},
        {"entity_id": "switch.kitchen_plug", "name_by_user": None, "name": "Kitchen Plug"},
    ]
    result = _suggest_entity_ids("bogus lights", registry)
    assert result == ["light.bogus_lights"]


def test_suggest_entity_ids_matches_by_name_field():
    registry = [
        {"entity_id": "switch.kitchen_plug", "name_by_user": None, "name": "Kitchen Plug"},
    ]
    result = _suggest_entity_ids("kitchen plug", registry)
    assert result == ["switch.kitchen_plug"]


def test_suggest_entity_ids_no_match():
    registry = [
        {"entity_id": "light.hall", "name_by_user": "Hall Light", "name": None},
    ]
    result = _suggest_entity_ids("bogus lights", registry)
    assert result == []
