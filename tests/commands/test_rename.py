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
    assert titles[None] == "Default"


def test_discover_dashboards_default_panel():
    panels = {"lovelace": {"component_name": "lovelace", "url_path": "", "title": None}}
    url_paths, titles = _discover_dashboards(panels)
    assert None in url_paths
    assert titles[None] == "Default"


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
    panels = {
        "config": {"component_name": "config"},
        "lovelace": {"component_name": "lovelace", "url_path": ""},
    }
    url_paths, _ = _discover_dashboards(panels)
    assert len(url_paths) == 1


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
    mocker.patch("zigporter.commands.rename.asyncio.run", return_value=True)

    from zigporter.commands.rename import rename_command  # noqa: PLC0415

    rename_command("https://ha.test", "token", True, "switch.old", "switch.new", False)


def test_rename_command_failure(mocker):
    import typer  # noqa: PLC0415

    mocker.patch("zigporter.commands.rename.asyncio.run", return_value=False)

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
    result = runner.invoke(app, ["rename", "switch.old", "switch.new", "--apply"])
    assert result.exit_code == 0
    mock_cmd.assert_called_once_with(
        ha_url="https://ha.test",
        token="token",
        verify_ssl=True,
        old_entity_id="switch.old",
        new_entity_id="switch.new",
        apply=True,
    )
