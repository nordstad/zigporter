"""Shared rename infrastructure used by rename-entity and rename-device commands."""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from zigporter.ha_client import HAClient, is_yaml_mode
from zigporter.lovelace import discover_dashboards as _discover_dashboards


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class RenameLocation:
    context: str  # "registry", "automation", "script", "scene", "lovelace", "config_entry"
    name: str  # human label
    item_id: str  # automation/script/scene ID, or lovelace url_path ("" = default dashboard)
    occurrences: int
    raw_config: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class RenamePlan:
    old_entity_id: str
    new_entity_id: str
    locations: list[RenameLocation]
    scanned_dashboard_names: list[str] = field(default_factory=list)
    yaml_mode_dashboard_names: list[str] = field(default_factory=list)
    yaml_mode_dashboard_paths: list[str | None] = field(default_factory=list)
    # Items where old_entity_id appears inside a template string (not patched automatically)
    jinja_template_names: list[tuple[str, str]] = field(default_factory=list)

    @property
    def total_occurrences(self) -> int:
        return sum(loc.occurrences for loc in self.locations)


# ---------------------------------------------------------------------------
# Display label map
# ---------------------------------------------------------------------------

CONTEXT_LABEL: dict[str, str] = {
    "registry": "registry",
    "automation": "automation",
    "script": "script",
    "scene": "scene",
    "lovelace": "dashboard",
    "config_entry": "helper",
}


# ---------------------------------------------------------------------------
# Tree walkers
# ---------------------------------------------------------------------------


def count_occurrences(node: Any, target_id: str) -> int:
    """Count exact string matches of target_id in a nested dict/list structure.

    Checks both dict keys and values to handle scene entity dicts where the
    entity ID is used as a key.
    """
    if isinstance(node, str):
        return 1 if node == target_id else 0
    if isinstance(node, dict):
        key_hits = sum(1 for k in node if k == target_id)
        val_hits = sum(count_occurrences(v, target_id) for v in node.values())
        return key_hits + val_hits
    if isinstance(node, list):
        return sum(count_occurrences(item, target_id) for item in node)
    return 0


def _has_template_substring(node: Any, target_id: str) -> bool:
    """Return True if target_id appears as a substring inside any string value in the tree.

    Only matches strings where target_id is embedded (not an exact match), catching
    Jinja2 expressions like {{ states('sensor.old') }} that deep_replace cannot patch.
    Checks values only — template expressions never appear as dict keys.
    """
    if isinstance(node, str):
        return target_id in node and node != target_id
    if isinstance(node, dict):
        return any(_has_template_substring(v, target_id) for v in node.values())
    if isinstance(node, list):
        return any(_has_template_substring(item, target_id) for item in node)
    return False


def deep_replace(node: Any, old_id: str, new_id: str) -> Any:
    """Recursively replace all exact occurrences of old_id with new_id (keys and values)."""
    if isinstance(node, str):
        return new_id if node == old_id else node
    if isinstance(node, dict):
        return {
            (new_id if k == old_id else k): deep_replace(v, old_id, new_id) for k, v in node.items()
        }
    if isinstance(node, list):
        return [deep_replace(item, old_id, new_id) for item in node]
    return node


# ---------------------------------------------------------------------------
# HA snapshot (fetches all config data in one pass)
# ---------------------------------------------------------------------------


@dataclass
class HASnapshot:
    entity_registry: list[dict[str, Any]]
    automations: list[dict[str, Any]]
    scripts: list[dict[str, Any]]
    scenes: list[dict[str, Any]]
    url_paths: list[str | None]
    titles: dict[str | None, str]
    lovelace_configs: list[dict[str, Any] | None]
    config_entries: list[dict[str, Any]]


async def fetch_ha_snapshot(ha_client: HAClient) -> HASnapshot:
    """Fetch all HA config data in parallel."""
    entity_registry, automations, scripts, scenes, panels, config_entries = await asyncio.gather(
        ha_client.get_entity_registry(),
        ha_client.get_automation_configs(),
        ha_client.get_scripts(),
        ha_client.get_scenes(),
        ha_client.get_panels(),
        ha_client.get_config_entries(),
    )
    url_paths, titles = _discover_dashboards(panels)
    lovelace_configs = list(
        await asyncio.gather(*[ha_client.get_lovelace_config(p) for p in url_paths])
    )
    return HASnapshot(
        entity_registry=entity_registry,
        automations=automations,
        scripts=scripts,
        scenes=scenes,
        url_paths=url_paths,
        titles=titles,
        lovelace_configs=lovelace_configs,
        config_entries=config_entries,
    )


async def apply_location_update(
    ha_client: HAClient,
    context: str,
    item_id: str,
    config: dict[str, Any],
    pairs: list[tuple[str, str]],
) -> str | None:
    """Apply deep_replace for all pairs and write to the correct HA API.

    Returns None on success, or a warning string if the write was skipped.
    """
    patched = config
    for old_id, new_id in pairs:
        patched = deep_replace(patched, old_id, new_id)

    if context == "automation":
        await ha_client.update_automation(item_id, patched)
    elif context == "script":
        await ha_client.update_script(item_id, patched)
    elif context == "scene":
        await ha_client.update_scene(item_id, patched)
    elif context == "lovelace":
        try:
            await ha_client.save_lovelace_config(patched, item_id or None)
        except RuntimeError:
            return "⚠ skipped (dashboard is read-only — update manually)"
    elif context == "config_entry":
        await ha_client.update_config_entry_options(item_id, patched)
    return None


def build_rename_plan_from_snapshot(
    snapshot: HASnapshot,
    old_entity_id: str,
    new_entity_id: str,
) -> RenamePlan:
    """Build a RenamePlan from pre-fetched HA data."""
    existing_ids = {e["entity_id"] for e in snapshot.entity_registry}
    if old_entity_id not in existing_ids:
        raise ValueError(f"Entity '{old_entity_id}' not found in the HA entity registry.")
    if new_entity_id in existing_ids:
        raise ValueError(f"Entity '{new_entity_id}' already exists in the HA entity registry.")

    locations: list[RenameLocation] = [
        RenameLocation(
            context="registry",
            name="HA entity registry",
            item_id=old_entity_id,
            occurrences=1,
        )
    ]

    for auto in snapshot.automations:
        count = count_occurrences(auto, old_entity_id)
        if count:
            locations.append(
                RenameLocation(
                    context="automation",
                    name=auto.get("alias") or auto.get("id", "?"),
                    item_id=str(auto.get("id", "")),
                    occurrences=count,
                    raw_config=auto,
                )
            )

    for script in snapshot.scripts:
        count = count_occurrences(script, old_entity_id)
        if count:
            locations.append(
                RenameLocation(
                    context="script",
                    name=script.get("alias") or script.get("id", "?"),
                    item_id=str(script.get("id", "")),
                    occurrences=count,
                    raw_config=script,
                )
            )

    for scene in snapshot.scenes:
        count = count_occurrences(scene, old_entity_id)
        if count:
            locations.append(
                RenameLocation(
                    context="scene",
                    name=scene.get("name") or scene.get("id", "?"),
                    item_id=str(scene.get("id", "")),
                    occurrences=count,
                    raw_config=scene,
                )
            )

    for url_path, config in zip(snapshot.url_paths, snapshot.lovelace_configs, strict=True):
        if config is None or is_yaml_mode(config):
            continue
        count = count_occurrences(config, old_entity_id)
        if count:
            title = snapshot.titles.get(url_path, url_path or "Overview")
            locations.append(
                RenameLocation(
                    context="lovelace",
                    name=title,
                    item_id=url_path or "",
                    occurrences=count,
                    raw_config=config,
                )
            )

    for entry in snapshot.config_entries:
        options = entry.get("options") or {}
        count = count_occurrences(options, old_entity_id)
        if count:
            locations.append(
                RenameLocation(
                    context="config_entry",
                    name=entry.get("title") or entry.get("entry_id", "?"),
                    item_id=entry["entry_id"],
                    occurrences=count,
                    raw_config=options,
                )
            )

    # Scan for Jinja2 template substring references — deep_replace won't patch these.
    jinja_names: list[tuple[str, str]] = []
    for auto in snapshot.automations:
        if _has_template_substring(auto, old_entity_id):
            jinja_names.append(("automation", auto.get("alias") or auto.get("id", "?")))
    for script in snapshot.scripts:
        if _has_template_substring(script, old_entity_id):
            jinja_names.append(("script", script.get("alias") or script.get("id", "?")))
    for scene in snapshot.scenes:
        if _has_template_substring(scene, old_entity_id):
            jinja_names.append(("scene", scene.get("name") or scene.get("id", "?")))

    return RenamePlan(
        old_entity_id=old_entity_id,
        new_entity_id=new_entity_id,
        locations=locations,
        jinja_template_names=jinja_names,
    )
