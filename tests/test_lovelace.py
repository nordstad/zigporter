"""Tests for zigporter.lovelace shared helpers."""

from zigporter.lovelace import cards_from_view, discover_dashboards


# ---------------------------------------------------------------------------
# discover_dashboards
# ---------------------------------------------------------------------------


def test_discover_dashboards_empty_panels():
    url_paths, titles = discover_dashboards({})
    assert url_paths == [None]
    assert titles[None] == "Overview"


def test_discover_dashboards_default_panel():
    panels = {"lovelace": {"component_name": "lovelace", "url_path": "", "title": None}}
    url_paths, titles = discover_dashboards(panels)
    assert None in url_paths
    assert titles[None] == "Overview"


def test_discover_dashboards_extra_panel():
    panels = {
        "lovelace": {"component_name": "lovelace", "url_path": ""},
        "mobile": {"component_name": "lovelace", "url_path": "mobile", "title": "Mobile"},
    }
    url_paths, titles = discover_dashboards(panels)
    assert None in url_paths
    assert "mobile" in url_paths
    assert titles["mobile"] == "Mobile"


def test_discover_dashboards_ignores_non_lovelace():
    # "config" is in _NON_LOVELACE_PANELS so it's excluded via URL blocklist.
    panels = {
        "config": {"component_name": "config"},
        "lovelace": {"component_name": "lovelace", "url_path": ""},
    }
    url_paths, _ = discover_dashboards(panels)
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
    url_paths, titles = discover_dashboards(panels)
    assert "dashboard-mushroom" in url_paths
    assert titles["dashboard-mushroom"] == "Mushroom"


# ---------------------------------------------------------------------------
# cards_from_view
# ---------------------------------------------------------------------------


def test_cards_from_view_classic_layout():
    view = {"cards": [{"type": "entity"}, {"type": "button"}]}
    assert cards_from_view(view) == [{"type": "entity"}, {"type": "button"}]


def test_cards_from_view_sections_layout():
    view = {
        "sections": [
            {"cards": [{"type": "tile"}]},
            {"cards": [{"type": "button"}]},
        ]
    }
    result = cards_from_view(view)
    assert len(result) == 2
    assert result[0] == {"type": "tile"}


def test_cards_from_view_mixed_layout():
    view = {
        "cards": [{"type": "entity"}],
        "sections": [{"cards": [{"type": "tile"}]}],
    }
    result = cards_from_view(view)
    assert len(result) == 2


def test_cards_from_view_empty():
    assert cards_from_view({}) == []
