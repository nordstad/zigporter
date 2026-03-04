"""Shared Lovelace dashboard discovery helpers."""

from typing import Any

# HA panels that are never Lovelace dashboards — skip without attempting a config fetch.
_NON_LOVELACE_PANELS: frozenset[str] = frozenset(
    {
        "energy",
        "history",
        "logbook",
        "map",
        "developer-tools",
        "profile",
        "config",
        "hacs",
        "notifications",
        "todo",
    }
)


def discover_dashboards(
    panels: dict[str, Any],
) -> tuple[list[str | None], dict[str | None, str]]:
    """Return (url_paths, titles) for all potential Lovelace dashboards from panels.

    Excludes known non-Lovelace panels by URL. The component_name check is intentionally
    omitted so that HACS/custom frontend panels (e.g. dashboard-mushroom) are included;
    panels without a valid lovelace config are silently dropped when the fetch returns None.
    """
    url_paths: list[str | None] = []
    titles: dict[str | None, str] = {}

    for panel_key, panel in panels.items():
        panel_url = panel.get("url_path") or panel_key
        if panel_url in _NON_LOVELACE_PANELS:
            continue
        if panel_url in ("lovelace", ""):
            lv_path: str | None = None
            title = panel.get("title") or "Overview"
        else:
            lv_path = panel_url
            title = panel.get("title") or panel_url
        if lv_path not in url_paths:
            url_paths.append(lv_path)
            titles[lv_path] = title

    if None not in url_paths:
        url_paths.insert(0, None)
        titles[None] = "Overview"

    return url_paths, titles


def cards_from_view(view: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract top-level cards from a view regardless of layout type.

    HA has two layouts:
    - Classic: view.cards  (list of card dicts)
    - Sections (2024+): view.sections[*].cards  (cards nested inside sections)
    Both can exist on the same dashboard so we collect from both.
    """
    cards: list[dict[str, Any]] = list(view.get("cards", []))
    for section in view.get("sections", []):
        cards.extend(section.get("cards", []))
    return cards
