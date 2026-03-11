"""Tests for the rename-entity command."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from zigporter.commands.rename_entity import (
    _suggest_entity_ids,
    build_rename_plan,
    display_plan,
    execute_rename,
)
from zigporter.rename_plan import (
    RenameLocation,
    RenamePlan,
    _has_template_substring,
    count_occurrences,
    deep_replace,
)

# ---------------------------------------------------------------------------
# _has_template_substring
# ---------------------------------------------------------------------------


def test_has_template_substring_detects_jinja():
    node = {"value_template": "{{ states('switch.kitchen_plug') }}"}
    assert _has_template_substring(node, "switch.kitchen_plug") is True


def test_has_template_substring_ignores_exact_match():
    # An exact match is handled by deep_replace — not a template substring
    assert _has_template_substring("switch.kitchen_plug", "switch.kitchen_plug") is False


def test_has_template_substring_no_match():
    node = {"value_template": "{{ states('light.hall') }}"}
    assert _has_template_substring(node, "switch.kitchen_plug") is False


def test_has_template_substring_in_list():
    node = [{"condition": "{{ is_state('switch.kitchen_plug', 'on') }}"}]
    assert _has_template_substring(node, "switch.kitchen_plug") is True


def test_has_template_substring_non_string_passthrough():
    assert _has_template_substring(42, "switch.kitchen_plug") is False
    assert _has_template_substring(None, "switch.kitchen_plug") is False


# ---------------------------------------------------------------------------
# count_occurrences
# ---------------------------------------------------------------------------


def testcount_occurrences_exact_string():
    assert count_occurrences("switch.kitchen_plug", "switch.kitchen_plug") == 1


def testcount_occurrences_no_match():
    assert count_occurrences("switch.other", "switch.kitchen_plug") == 0


def testcount_occurrences_in_dict_value():
    assert count_occurrences({"entity_id": "switch.kitchen_plug"}, "switch.kitchen_plug") == 1


def testcount_occurrences_in_list():
    node = ["switch.kitchen_plug", "light.hall", "switch.kitchen_plug"]
    assert count_occurrences(node, "switch.kitchen_plug") == 2


def testcount_occurrences_in_dict_key():
    # Scene format: entity ID used as a dict key
    node = {"switch.kitchen_plug": {"state": "on"}}
    assert count_occurrences(node, "switch.kitchen_plug") == 1


def testcount_occurrences_nested():
    node = {
        "action": [{"service": "switch.turn_on", "target": {"entity_id": "switch.kitchen_plug"}}]
    }
    assert count_occurrences(node, "switch.kitchen_plug") == 1


def testcount_occurrences_no_partial_match():
    # "switch.kitchen_plug_power" must NOT match "switch.kitchen_plug"
    assert count_occurrences("switch.kitchen_plug_power", "switch.kitchen_plug") == 0


def testcount_occurrences_non_string_node():
    assert count_occurrences(42, "switch.kitchen_plug") == 0
    assert count_occurrences(None, "switch.kitchen_plug") == 0


# ---------------------------------------------------------------------------
# deep_replace
# ---------------------------------------------------------------------------


def testdeep_replace_string_match():
    assert deep_replace("switch.old", "switch.old", "switch.new") == "switch.new"


def testdeep_replace_string_no_match():
    assert deep_replace("switch.other", "switch.old", "switch.new") == "switch.other"


def testdeep_replace_dict_value():
    result = deep_replace({"entity_id": "switch.old"}, "switch.old", "switch.new")
    assert result == {"entity_id": "switch.new"}


def testdeep_replace_dict_key():
    # Scene entities dict: entity ID is a key
    result = deep_replace({"switch.old": {"state": "on"}}, "switch.old", "switch.new")
    assert result == {"switch.new": {"state": "on"}}


def testdeep_replace_list():
    result = deep_replace(["switch.old", "light.hall"], "switch.old", "switch.new")
    assert result == ["switch.new", "light.hall"]


def testdeep_replace_nested():
    node = {"action": [{"service": "switch.turn_on", "target": {"entity_id": "switch.old"}}]}
    result = deep_replace(node, "switch.old", "switch.new")
    assert result["action"][0]["target"]["entity_id"] == "switch.new"


def testdeep_replace_no_partial_replace():
    assert deep_replace("switch.old_extra", "switch.old", "switch.new") == "switch.old_extra"


def testdeep_replace_non_string_passthrough():
    assert deep_replace(42, "switch.old", "switch.new") == 42
    assert deep_replace(None, "switch.old", "switch.new") is None


# ---------------------------------------------------------------------------
# _discover_dashboards
# ---------------------------------------------------------------------------


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


async def test_build_rename_plan_detects_jinja_template_in_automation(mock_ha_client):
    """Automations with template strings containing the entity ID populate jinja_template_names."""
    mock_ha_client.get_automation_configs = AsyncMock(
        return_value=[
            {
                "id": "a1",
                "alias": "Power monitor",
                "trigger": [
                    {
                        "platform": "template",
                        "value_template": "{{ states('switch.kitchen_plug') | float > 10 }}",
                    }
                ],
            }
        ]
    )
    plan = await build_rename_plan(mock_ha_client, "switch.kitchen_plug", "switch.new_plug")
    assert ("automation", "Power monitor") in plan.jinja_template_names


async def test_build_rename_plan_no_jinja_when_only_exact_matches(mock_ha_client):
    """Automations with only exact entity_id values do NOT appear in jinja_template_names."""
    plan = await build_rename_plan(mock_ha_client, "switch.kitchen_plug", "switch.new_plug")
    assert plan.jinja_template_names == []


async def test_build_rename_plan_detects_jinja_template_in_script(mock_ha_client):
    mock_ha_client.get_scripts = AsyncMock(
        return_value=[
            {
                "id": "s1",
                "alias": "Check plug",
                "sequence": [
                    {
                        "condition": "template",
                        "value_template": "{{ is_state('switch.kitchen_plug', 'on') }}",
                    }
                ],
            }
        ]
    )
    plan = await build_rename_plan(mock_ha_client, "switch.kitchen_plug", "switch.new_plug")
    assert ("script", "Check plug") in plan.jinja_template_names


async def test_display_plan_shows_jinja_warning(mock_ha_client, capsys):
    mock_ha_client.get_automation_configs = AsyncMock(
        return_value=[
            {
                "id": "a1",
                "alias": "Power monitor",
                "trigger": [
                    {
                        "platform": "template",
                        "value_template": "{{ states('switch.kitchen_plug') | float > 10 }}",
                    }
                ],
            }
        ]
    )
    plan = await build_rename_plan(mock_ha_client, "switch.kitchen_plug", "switch.new_plug")
    # Verify jinja_template_names is set before display
    assert plan.jinja_template_names
    # display_plan uses rich Console — just verify it doesn't raise and the field is populated
    display_plan(plan)  # Would raise if warning block has a bug


async def test_display_plan_no_jinja_warning_when_empty(mock_ha_client):
    """display_plan should not raise when jinja_template_names is empty."""
    plan = await build_rename_plan(mock_ha_client, "switch.kitchen_plug", "switch.new_plug")
    assert plan.jinja_template_names == []
    display_plan(plan)  # Must not raise


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
    """save_lovelace_config raises RuntimeError → warns, does not crash, other updates continue."""
    mock_exec_client.save_lovelace_config = AsyncMock(
        side_effect=RuntimeError("WebSocket command failed: Not supported")
    )
    printed = []
    mocker.patch(
        "zigporter.commands.rename_entity.console.print",
        side_effect=lambda *a, **kw: printed.append(a[0] if a else ""),
    )
    config = {"views": [{"entity": "switch.old"}]}
    auto_config = {"id": "a1", "alias": "Test", "action": [{"entity_id": "switch.old"}]}
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
            ),
            RenameLocation(
                context="automation",
                name="Test",
                item_id="a1",
                occurrences=1,
                raw_config=auto_config,
            ),
        ],
    )
    # Must not raise
    await execute_rename(mock_exec_client, plan)
    assert any("skipped" in str(p) for p in printed)
    # Other contexts still processed
    mock_exec_client.update_automation.assert_called_once()


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
        "zigporter.commands.rename_entity.console.print",
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
        "zigporter.commands.rename_entity.console.print",
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
        "zigporter.commands.rename_entity.console.print",
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
        "zigporter.commands.rename_entity.console.print",
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
        "zigporter.commands.rename_entity.console.print",
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
    mocker.patch("zigporter.commands.rename_entity.HAClient")
    mocker.patch("zigporter.commands.rename_entity.build_rename_plan", return_value=_SIMPLE_PLAN)
    mocker.patch("zigporter.commands.rename_entity.execute_rename", new=AsyncMock())

    from zigporter.commands.rename_entity import run_rename  # noqa: PLC0415

    result = await run_rename("https://ha.test", "token", True, "switch.old", "switch.new", True)
    assert result is True


async def test_run_rename_validation_error(mocker):
    mock_ha_cls = mocker.patch("zigporter.commands.rename_entity.HAClient")
    mock_ha_cls.return_value.get_entity_registry = AsyncMock(return_value=[])
    mocker.patch(
        "zigporter.commands.rename_entity.build_rename_plan", side_effect=ValueError("not found")
    )

    from zigporter.commands.rename_entity import run_rename  # noqa: PLC0415

    result = await run_rename("https://ha.test", "token", True, "switch.old", "switch.new", True)
    assert result is False


async def test_run_rename_no_tty_no_apply(mocker):
    mocker.patch("zigporter.commands.rename_entity.HAClient")
    mocker.patch("zigporter.commands.rename_entity.build_rename_plan", return_value=_SIMPLE_PLAN)
    mocker.patch("zigporter.commands.rename_entity.sys").stdin.isatty.return_value = False

    from zigporter.commands.rename_entity import run_rename  # noqa: PLC0415

    result = await run_rename("https://ha.test", "token", True, "switch.old", "switch.new", False)
    assert result is False


async def test_run_rename_confirmed(mocker):
    mocker.patch("zigporter.commands.rename_entity.HAClient")
    mocker.patch("zigporter.commands.rename_entity.build_rename_plan", return_value=_SIMPLE_PLAN)
    mock_execute = mocker.patch("zigporter.commands.rename_entity.execute_rename", new=AsyncMock())
    mocker.patch("zigporter.commands.rename_entity.sys").stdin.isatty.return_value = True
    mock_q = mocker.MagicMock()
    mock_q.unsafe_ask_async = AsyncMock(return_value=True)
    mocker.patch("zigporter.commands.rename_entity.questionary.confirm", return_value=mock_q)

    from zigporter.commands.rename_entity import run_rename  # noqa: PLC0415

    result = await run_rename("https://ha.test", "token", True, "switch.old", "switch.new", False)
    assert result is True
    mock_execute.assert_called_once()


async def test_run_rename_aborted(mocker):
    mocker.patch("zigporter.commands.rename_entity.HAClient")
    mocker.patch("zigporter.commands.rename_entity.build_rename_plan", return_value=_SIMPLE_PLAN)
    mocker.patch("zigporter.commands.rename_entity.execute_rename", new=AsyncMock())
    mocker.patch("zigporter.commands.rename_entity.sys").stdin.isatty.return_value = True
    mock_q = mocker.MagicMock()
    mock_q.unsafe_ask_async = AsyncMock(return_value=False)
    mocker.patch("zigporter.commands.rename_entity.questionary.confirm", return_value=mock_q)

    from zigporter.commands.rename_entity import run_rename  # noqa: PLC0415

    result = await run_rename("https://ha.test", "token", True, "switch.old", "switch.new", False)
    assert result is True  # aborted is not an error


# ---------------------------------------------------------------------------
# run_rename — wizard paths (old_entity_id / new_entity_id omitted)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_rename_old_id_none_no_tty(mocker):
    """old_entity_id=None + no TTY → error, no HA calls."""
    mocker.patch("zigporter.commands.rename_entity.HAClient")
    mocker.patch("zigporter.commands.rename_entity.sys").stdin.isatty.return_value = False

    from zigporter.commands.rename_entity import run_rename  # noqa: PLC0415

    result = await run_rename("https://ha.test", "token", True, None, None, False)
    assert result is False


@pytest.mark.asyncio
async def test_run_rename_old_id_none_tty_picks_interactively(mocker):
    """old_entity_id=None + TTY → pick_entity_interactively called, flow continues."""
    mocker.patch("zigporter.commands.rename_entity.HAClient")
    mocker.patch("zigporter.commands.rename_entity.sys").stdin.isatty.return_value = True
    mock_pick = mocker.patch(
        "zigporter.commands.rename_entity.pick_entity_interactively",
        new=AsyncMock(return_value="switch.old"),
    )
    mocker.patch("zigporter.commands.rename_entity.build_rename_plan", return_value=_SIMPLE_PLAN)
    mock_execute = mocker.patch("zigporter.commands.rename_entity.execute_rename", new=AsyncMock())
    mock_confirm = mocker.MagicMock()
    mock_confirm.unsafe_ask_async = AsyncMock(return_value=True)
    mocker.patch("zigporter.commands.rename_entity.questionary.confirm", return_value=mock_confirm)
    mock_text = mocker.MagicMock()
    mock_text.unsafe_ask_async = AsyncMock(return_value="switch.new")
    mocker.patch("zigporter.commands.rename_entity.questionary.text", return_value=mock_text)

    from zigporter.commands.rename_entity import run_rename  # noqa: PLC0415

    result = await run_rename("https://ha.test", "token", True, None, None, False)
    assert result is True
    mock_pick.assert_awaited_once()
    mock_execute.assert_called_once()


@pytest.mark.asyncio
async def test_run_rename_invalid_old_entity_id(mocker):
    """Invalid old_entity_id format → validation error, no HA calls."""
    mocker.patch("zigporter.commands.rename_entity.HAClient")

    from zigporter.commands.rename_entity import run_rename  # noqa: PLC0415

    result = await run_rename("https://ha.test", "token", True, "INVALID", "switch.new", False)
    assert result is False


@pytest.mark.asyncio
async def test_run_rename_invalid_new_entity_id(mocker):
    """Invalid new_entity_id format → validation error, no HA calls."""
    mocker.patch("zigporter.commands.rename_entity.HAClient")

    from zigporter.commands.rename_entity import run_rename  # noqa: PLC0415

    result = await run_rename("https://ha.test", "token", True, "switch.old", "INVALID", False)
    assert result is False


@pytest.mark.asyncio
async def test_run_rename_new_id_none_no_tty(mocker):
    """new_entity_id=None + no TTY after old ID supplied → error."""
    mocker.patch("zigporter.commands.rename_entity.HAClient")
    mocker.patch("zigporter.commands.rename_entity.sys").stdin.isatty.return_value = False

    from zigporter.commands.rename_entity import run_rename  # noqa: PLC0415

    result = await run_rename("https://ha.test", "token", True, "switch.old", None, False)
    assert result is False


@pytest.mark.asyncio
async def test_run_rename_new_id_none_tty_prefilled(mocker):
    """new_entity_id=None + TTY → questionary.text called with default=old_entity_id."""
    mocker.patch("zigporter.commands.rename_entity.HAClient")
    mocker.patch("zigporter.commands.rename_entity.sys").stdin.isatty.return_value = True
    mocker.patch("zigporter.commands.rename_entity.build_rename_plan", return_value=_SIMPLE_PLAN)
    mocker.patch("zigporter.commands.rename_entity.execute_rename", new=AsyncMock())
    mock_confirm = mocker.MagicMock()
    mock_confirm.unsafe_ask_async = AsyncMock(return_value=True)
    mocker.patch("zigporter.commands.rename_entity.questionary.confirm", return_value=mock_confirm)
    text_mock = mocker.patch("zigporter.commands.rename_entity.questionary.text")
    text_instance = mocker.MagicMock()
    text_instance.unsafe_ask_async = AsyncMock(return_value="switch.new")
    text_mock.return_value = text_instance

    from zigporter.commands.rename_entity import run_rename  # noqa: PLC0415

    result = await run_rename("https://ha.test", "token", True, "switch.old", None, False)
    assert result is True
    assert text_mock.call_args[1]["default"] == "switch.old"


# ---------------------------------------------------------------------------
# rename_command
# ---------------------------------------------------------------------------


def test_rename_command_success(mocker):
    mocker.patch("zigporter.commands.rename_entity.run_rename", new=AsyncMock(return_value=True))

    from zigporter.commands.rename_entity import rename_command  # noqa: PLC0415

    rename_command("https://ha.test", "token", True, "switch.old", "switch.new", False)


def test_rename_command_failure(mocker):
    import typer  # noqa: PLC0415

    mocker.patch("zigporter.commands.rename_entity.run_rename", new=AsyncMock(return_value=False))

    from zigporter.commands.rename_entity import rename_command  # noqa: PLC0415

    with pytest.raises(typer.Exit):
        rename_command("https://ha.test", "token", True, "switch.old", "switch.new", False)


# ---------------------------------------------------------------------------
# rename CLI (main.py)
# ---------------------------------------------------------------------------


def test_rename_cli_invokes_rename_command(mocker):
    mocker.patch("zigporter.main._get_config", return_value=("https://ha.test", "token", True))
    mock_cmd = mocker.patch("zigporter.commands.rename_entity.rename_command")

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
# pick_entity_interactively — two-step: domain select → browse list or search
# ---------------------------------------------------------------------------


def _make_select_side_effect(*return_values):
    """Return a side_effect list of mock instances for successive questionary.select calls."""
    instances = []
    for value in return_values:
        instance = MagicMock()
        instance.unsafe_ask_async = AsyncMock(return_value=value)
        instances.append(instance)
    return instances


@pytest.mark.asyncio
async def test_pick_entity_interactively_browse(mocker):
    """User picks a domain then selects an entity directly from the browse list."""
    from zigporter.commands.rename_entity import pick_entity_interactively  # noqa: PLC0415

    entities = [
        {"entity_id": "light.living_room", "name_by_user": "Living Room Light", "name": ""},
        {"entity_id": "light.kitchen", "name_by_user": "", "name": ""},
        {"entity_id": "sensor.temp", "name_by_user": "", "name": ""},
    ]
    ha_client = MagicMock()
    ha_client.get_entity_registry = AsyncMock(return_value=entities)

    select_mock = mocker.patch("zigporter.commands.rename_entity.questionary.select")
    select_mock.side_effect = _make_select_side_effect(
        "light",  # step 1: domain
        "Living Room Light  (light.living_room)",  # step 2: browse pick
    )

    result = await pick_entity_interactively(ha_client)

    assert result == "light.living_room"
    assert select_mock.call_count == 2
    # Domain picker lists both domains
    domain_values = [c.value for c in select_mock.call_args_list[0][1]["choices"]]
    assert "light" in domain_values
    assert "sensor" in domain_values
    # Browse list contains only light entities (plus sentinel)
    browse_values = [
        c.value for c in select_mock.call_args_list[1][1]["choices"] if hasattr(c, "value")
    ]
    assert "Living Room Light  (light.living_room)" in browse_values
    assert "light.kitchen" in browse_values
    assert not any("sensor" in str(v) for v in browse_values)


@pytest.mark.asyncio
async def test_pick_entity_interactively_search(mocker):
    """User picks a domain, selects Search sentinel, then uses autocomplete."""
    from zigporter.commands.rename_entity import _SEARCH_SENTINEL, pick_entity_interactively  # noqa: PLC0415

    entities = [
        {"entity_id": "light.living_room", "name_by_user": "Living Room Light", "name": ""},
        {"entity_id": "light.kitchen", "name_by_user": "", "name": ""},
    ]
    ha_client = MagicMock()
    ha_client.get_entity_registry = AsyncMock(return_value=entities)

    select_mock = mocker.patch("zigporter.commands.rename_entity.questionary.select")
    select_mock.side_effect = _make_select_side_effect(
        "light",  # step 1: domain
        _SEARCH_SENTINEL,  # step 2: user picks Search
    )

    autocomplete_mock = mocker.patch("zigporter.commands.rename_entity.questionary.autocomplete")
    ac_instance = MagicMock()
    ac_instance.unsafe_ask_async = AsyncMock(return_value="light.kitchen")
    autocomplete_mock.return_value = ac_instance

    result = await pick_entity_interactively(ha_client)

    assert result == "light.kitchen"
    assert autocomplete_mock.call_count == 1
    ac_choices = autocomplete_mock.call_args[1]["choices"]
    assert "light.kitchen" in ac_choices
    assert autocomplete_mock.call_args[1]["match_middle"] is True


@pytest.mark.asyncio
async def test_pick_entity_interactively_no_friendly_name(mocker):
    """Entities without a distinct friendly name use the entity_id as the label."""
    from zigporter.commands.rename_entity import pick_entity_interactively  # noqa: PLC0415

    entities = [{"entity_id": "switch.garage_door", "name_by_user": "", "name": ""}]
    ha_client = MagicMock()
    ha_client.get_entity_registry = AsyncMock(return_value=entities)

    select_mock = mocker.patch("zigporter.commands.rename_entity.questionary.select")
    select_mock.side_effect = _make_select_side_effect("switch", "switch.garage_door")

    result = await pick_entity_interactively(ha_client)

    assert result == "switch.garage_door"
    browse_values = [
        c.value for c in select_mock.call_args_list[1][1]["choices"] if hasattr(c, "value")
    ]
    assert "switch.garage_door" in browse_values
    # No parenthesised suffix when name equals entity_id or is empty
    assert not any("(" in str(v) for v in browse_values if "Search" not in str(v))


@pytest.mark.asyncio
async def test_pick_entity_interactively_domain_cancelled(mocker):
    """Returning None from the domain picker propagates None."""
    from zigporter.commands.rename_entity import pick_entity_interactively  # noqa: PLC0415

    ha_client = MagicMock()
    ha_client.get_entity_registry = AsyncMock(
        return_value=[{"entity_id": "light.x", "name_by_user": "", "name": ""}]
    )

    select_mock = mocker.patch("zigporter.commands.rename_entity.questionary.select")
    select_mock.side_effect = _make_select_side_effect(None)

    result = await pick_entity_interactively(ha_client)
    assert result is None


# ---------------------------------------------------------------------------
# display_plan — line 350: spacing between 2+ YAML-mode dashboards
# ---------------------------------------------------------------------------


def test_display_plan_yaml_mode_spacing_between_multiple(mocker):
    """Line 350: console.print() spacer between yaml-mode dashboard entries."""
    from zigporter.commands.rename_entity import display_plan  # noqa: PLC0415

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
        "zigporter.commands.rename_entity.console.print",
        side_effect=lambda *a, **kw: printed.append(a[0] if a else ""),
    )
    display_plan(plan)
    # The spacer console.print() is called with no args — produces an empty-string entry
    assert any(a == "" for a in printed)


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


# ---------------------------------------------------------------------------
# _validate_entity_id — empty string branch (line 304)
# ---------------------------------------------------------------------------


def test_validate_entity_id_empty_string():
    """Empty entity ID returns an error string (not True)."""
    from zigporter.commands.rename_entity import _validate_entity_id  # noqa: PLC0415

    result = _validate_entity_id("   ")
    assert result == "Entity ID cannot be empty"


# ---------------------------------------------------------------------------
# pick_entity_interactively — browse returns None (line 361)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pick_entity_interactively_browse_cancelled(mocker):
    """User cancels the entity browse step → returns None (line 361)."""
    from zigporter.commands.rename_entity import pick_entity_interactively  # noqa: PLC0415

    entities = [{"entity_id": "light.hall", "name_by_user": "", "name": ""}]
    ha_client = MagicMock()
    ha_client.get_entity_registry = AsyncMock(return_value=entities)

    select_mock = mocker.patch("zigporter.commands.rename_entity.questionary.select")
    select_mock.side_effect = _make_select_side_effect(
        "light",  # domain chosen
        None,  # browse step cancelled
    )

    result = await pick_entity_interactively(ha_client)
    assert result is None


# ---------------------------------------------------------------------------
# pick_entity_interactively — autocomplete returns None (line 380)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pick_entity_interactively_search_cancelled(mocker):
    """User cancels the autocomplete search step → returns None (line 380)."""
    from zigporter.commands.rename_entity import _SEARCH_SENTINEL, pick_entity_interactively  # noqa: PLC0415

    entities = [{"entity_id": "light.hall", "name_by_user": "", "name": ""}]
    ha_client = MagicMock()
    ha_client.get_entity_registry = AsyncMock(return_value=entities)

    select_mock = mocker.patch("zigporter.commands.rename_entity.questionary.select")
    select_mock.side_effect = _make_select_side_effect("light", _SEARCH_SENTINEL)

    autocomplete_mock = mocker.patch("zigporter.commands.rename_entity.questionary.autocomplete")
    ac_instance = MagicMock()
    ac_instance.unsafe_ask_async = AsyncMock(return_value=None)
    autocomplete_mock.return_value = ac_instance

    result = await pick_entity_interactively(ha_client)
    assert result is None


@pytest.mark.asyncio
async def test_pick_entity_interactively_search_validate_fn(mocker):
    """The _validate closure passed to questionary.autocomplete rejects unknown values
    and accepts known labels (lines 368-370)."""
    from zigporter.commands.rename_entity import _SEARCH_SENTINEL, pick_entity_interactively  # noqa: PLC0415

    entities = [{"entity_id": "light.hall", "name_by_user": "Hall Light", "name": ""}]
    ha_client = MagicMock()
    ha_client.get_entity_registry = AsyncMock(return_value=entities)

    select_mock = mocker.patch("zigporter.commands.rename_entity.questionary.select")
    select_mock.side_effect = _make_select_side_effect("light", _SEARCH_SENTINEL)

    autocomplete_mock = mocker.patch("zigporter.commands.rename_entity.questionary.autocomplete")
    ac_instance = MagicMock()
    ac_instance.unsafe_ask_async = AsyncMock(return_value="Hall Light  (light.hall)")
    autocomplete_mock.return_value = ac_instance

    await pick_entity_interactively(ha_client)

    # Extract the validate kwarg that was passed to questionary.autocomplete
    validate_fn = autocomplete_mock.call_args[1]["validate"]

    # Valid label → True
    valid_label = autocomplete_mock.call_args[1]["choices"][0]
    assert validate_fn(valid_label) is True

    # Unknown value → error string
    result = validate_fn("not a valid label")
    assert isinstance(result, str)
    assert "search" in result.lower() or "select" in result.lower()


# ---------------------------------------------------------------------------
# run_rename — pick_entity_interactively returns None (line 535)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_rename_entity_picker_cancelled(mocker):
    """old_entity_id=None + TTY + picker returns None → returns False (line 535)."""
    mocker.patch("zigporter.commands.rename_entity.HAClient")
    mocker.patch("zigporter.commands.rename_entity.sys").stdin.isatty.return_value = True
    mocker.patch(
        "zigporter.commands.rename_entity.pick_entity_interactively",
        new=AsyncMock(return_value=None),
    )

    from zigporter.commands.rename_entity import run_rename  # noqa: PLC0415

    result = await run_rename("https://ha.test", "token", True, None, None, False)
    assert result is False


# ---------------------------------------------------------------------------
# run_rename — new entity ID text input aborted (lines 557-558)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_rename_new_id_tty_aborted(mocker):
    """new_entity_id=None + TTY + user submits empty text → 'Aborted.' (lines 557-558)."""
    mocker.patch("zigporter.commands.rename_entity.HAClient")
    mocker.patch("zigporter.commands.rename_entity.sys").stdin.isatty.return_value = True

    text_mock = mocker.patch("zigporter.commands.rename_entity.questionary.text")
    text_instance = MagicMock()
    text_instance.unsafe_ask_async = AsyncMock(return_value="")
    text_mock.return_value = text_instance

    from zigporter.commands.rename_entity import run_rename  # noqa: PLC0415

    result = await run_rename("https://ha.test", "token", True, "switch.old", None, False)
    assert result is True  # aborted is not an error


# ---------------------------------------------------------------------------
# run_rename — entity not found with suggestions (lines 575-577)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_rename_not_found_with_suggestions(mocker):
    """ValueError 'not found' + suggestions → hint lines printed (lines 575-577).

    _suggest_entity_ids matches entities whose name_by_user/name equals the search term.
    The entity's name_by_user is set to the old_entity_id so the suggestion is returned.
    """
    mock_ha = MagicMock()
    # Entity whose name_by_user exactly equals the old_entity_id we're searching for
    mock_ha.get_entity_registry = AsyncMock(
        return_value=[
            {
                "entity_id": "switch.kitchen_plug",
                "name_by_user": "switch.old_plug",
                "name": None,
            },
        ]
    )
    mocker.patch("zigporter.commands.rename_entity.HAClient", return_value=mock_ha)
    mocker.patch(
        "zigporter.commands.rename_entity.build_rename_plan",
        side_effect=ValueError("switch.old_plug not found in registry"),
    )
    printed = []
    mocker.patch(
        "zigporter.commands.rename_entity.console.print",
        side_effect=lambda *a, **kw: printed.append(a[0] if a else ""),
    )

    from zigporter.commands.rename_entity import run_rename  # noqa: PLC0415

    result = await run_rename(
        "https://ha.test", "token", True, "switch.old_plug", "switch.new_plug", True
    )
    assert result is False
    assert any("Hint" in str(p) or "Re-run" in str(p) for p in printed)


# ---------------------------------------------------------------------------
# run_rename — apply=True skips confirmation prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_rename_apply_flag_skips_confirmation(mocker):
    """apply=True: execute_rename is called without consulting _prompt_apply_confirm."""
    mock_ha = MagicMock()
    mocker.patch("zigporter.commands.rename_entity.HAClient", return_value=mock_ha)

    mock_plan = MagicMock()
    mocker.patch(
        "zigporter.commands.rename_entity.build_rename_plan",
        new=AsyncMock(return_value=mock_plan),
    )
    mocker.patch("zigporter.commands.rename_entity.display_plan")
    mock_execute = mocker.patch("zigporter.commands.rename_entity.execute_rename", new=AsyncMock())
    mock_prompt = mocker.patch(
        "zigporter.commands.rename_entity._prompt_apply_confirm", new=AsyncMock(return_value=True)
    )

    from zigporter.commands.rename_entity import run_rename  # noqa: PLC0415

    result = await run_rename(
        "https://ha.test", "token", True, "switch.old", "switch.new", apply=True
    )

    assert result is True
    mock_execute.assert_awaited_once()
    mock_prompt.assert_not_awaited()


# ---------------------------------------------------------------------------
# run_rename — apply=False + user confirms → changes applied
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_rename_confirmed_applies_changes(mocker):
    """apply=False + TTY + _prompt_apply_confirm returns True → execute_rename is called."""
    mock_ha = MagicMock()
    mocker.patch("zigporter.commands.rename_entity.HAClient", return_value=mock_ha)
    mocker.patch("zigporter.commands.rename_entity.sys").stdin.isatty.return_value = True

    mock_plan = MagicMock()
    mocker.patch(
        "zigporter.commands.rename_entity.build_rename_plan",
        new=AsyncMock(return_value=mock_plan),
    )
    mocker.patch("zigporter.commands.rename_entity.display_plan")
    mock_execute = mocker.patch("zigporter.commands.rename_entity.execute_rename", new=AsyncMock())
    mocker.patch(
        "zigporter.commands.rename_entity._prompt_apply_confirm", new=AsyncMock(return_value=True)
    )

    from zigporter.commands.rename_entity import run_rename  # noqa: PLC0415

    result = await run_rename(
        "https://ha.test", "token", True, "switch.old", "switch.new", apply=False
    )

    assert result is True
    mock_execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# run_rename — apply=False + user declines → aborts cleanly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_rename_declined_aborts(mocker):
    """apply=False + TTY + _prompt_apply_confirm returns False → returns True (not an error)."""
    mock_ha = MagicMock()
    mocker.patch("zigporter.commands.rename_entity.HAClient", return_value=mock_ha)
    mocker.patch("zigporter.commands.rename_entity.sys").stdin.isatty.return_value = True

    mock_plan = MagicMock()
    mocker.patch(
        "zigporter.commands.rename_entity.build_rename_plan",
        new=AsyncMock(return_value=mock_plan),
    )
    mocker.patch("zigporter.commands.rename_entity.display_plan")
    mock_execute = mocker.patch("zigporter.commands.rename_entity.execute_rename", new=AsyncMock())
    mocker.patch(
        "zigporter.commands.rename_entity._prompt_apply_confirm", new=AsyncMock(return_value=False)
    )

    from zigporter.commands.rename_entity import run_rename  # noqa: PLC0415

    result = await run_rename(
        "https://ha.test", "token", True, "switch.old", "switch.new", apply=False
    )

    assert result is True
    mock_execute.assert_not_awaited()
