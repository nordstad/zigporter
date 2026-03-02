"""Helpers for collecting Home Assistant entity references in nested configs."""

from typing import Any


def collect_config_entity_ids(node: Any) -> set[str]:
    """Recursively collect all `entity_id` references from dict/list config trees."""
    ids: set[str] = set()
    if isinstance(node, dict):
        val = node.get("entity_id")
        if isinstance(val, str):
            ids.add(val)
        elif isinstance(val, list):
            ids.update(v for v in val if isinstance(v, str))
        for v in node.values():
            ids.update(collect_config_entity_ids(v))
    elif isinstance(node, list):
        for item in node:
            ids.update(collect_config_entity_ids(item))
    return ids
