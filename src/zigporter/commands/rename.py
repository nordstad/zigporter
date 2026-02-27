"""rename command — rename an HA entity ID and cascade the change everywhere."""

import asyncio
import sys
from dataclasses import dataclass, field
from typing import Any

import questionary
from rich.console import Console
from rich.table import Table

from zigporter.ha_client import HAClient

console = Console()

_STYLE = questionary.Style(
    [
        ("qmark", "fg:ansicyan bold"),
        ("question", "bold"),
        ("answer", "fg:ansicyan bold"),
        ("pointer", "fg:ansicyan bold"),
        ("highlighted", "fg:ansicyan bold"),
        ("selected", "fg:ansicyan"),
        ("separator", "fg:ansibrightblack"),
        ("instruction", "fg:ansibrightblack"),
        ("text", ""),
        ("disabled", "fg:ansibrightblack italic"),
    ]
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class RenameLocation:
    context: str  # "registry", "automation", "script", "scene", "lovelace"
    name: str  # human label
    item_id: str  # automation/script/scene ID, or lovelace url_path ("" = default dashboard)
    occurrences: int
    raw_config: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class RenamePlan:
    old_entity_id: str
    new_entity_id: str
    locations: list[RenameLocation]

    @property
    def total_occurrences(self) -> int:
        return sum(loc.occurrences for loc in self.locations)


# ---------------------------------------------------------------------------
# Tree walkers
# ---------------------------------------------------------------------------


def _count_occurrences(node: Any, target_id: str) -> int:
    """Count exact string matches of target_id in a nested dict/list structure.

    Checks both dict keys and values to handle scene entity dicts where the
    entity ID is used as a key.
    """
    if isinstance(node, str):
        return 1 if node == target_id else 0
    if isinstance(node, dict):
        key_hits = sum(1 for k in node if k == target_id)
        val_hits = sum(_count_occurrences(v, target_id) for v in node.values())
        return key_hits + val_hits
    if isinstance(node, list):
        return sum(_count_occurrences(item, target_id) for item in node)
    return 0


def _deep_replace(node: Any, old_id: str, new_id: str) -> Any:
    """Recursively replace all exact occurrences of old_id with new_id (keys and values)."""
    if isinstance(node, str):
        return new_id if node == old_id else node
    if isinstance(node, dict):
        return {
            (new_id if k == old_id else k): _deep_replace(v, old_id, new_id)
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [_deep_replace(item, old_id, new_id) for item in node]
    return node


# ---------------------------------------------------------------------------
# Dashboard discovery
# ---------------------------------------------------------------------------


def _discover_dashboards(
    panels: dict[str, Any],
) -> tuple[list[str | None], dict[str | None, str]]:
    """Return (url_paths, titles) for all Lovelace dashboards discovered from panels."""
    url_paths: list[str | None] = []
    titles: dict[str | None, str] = {}

    for panel_key, panel in panels.items():
        if panel.get("component_name") != "lovelace":
            continue
        panel_url = panel.get("url_path") or panel_key
        if panel_url in ("lovelace", ""):
            lv_path: str | None = None
            title = panel.get("title") or "Default"
        else:
            lv_path = panel_url
            title = panel.get("title") or panel_url
        if lv_path not in url_paths:
            url_paths.append(lv_path)
            titles[lv_path] = title

    if None not in url_paths:
        url_paths.insert(0, None)
        titles[None] = "Default"

    return url_paths, titles


# ---------------------------------------------------------------------------
# Plan builder
# ---------------------------------------------------------------------------


async def build_rename_plan(
    ha_client: HAClient,
    old_entity_id: str,
    new_entity_id: str,
) -> RenamePlan:
    """Scan all HA data and build a plan for renaming old_entity_id → new_entity_id."""
    entity_registry = await ha_client.get_entity_registry()
    existing_ids = {e["entity_id"] for e in entity_registry}

    if old_entity_id not in existing_ids:
        raise ValueError(f"Entity '{old_entity_id}' not found in the HA entity registry.")
    if new_entity_id in existing_ids:
        raise ValueError(f"Entity '{new_entity_id}' already exists in the HA entity registry.")

    automations, scripts, scenes, panels = await asyncio.gather(
        ha_client.get_automation_configs(),
        ha_client.get_scripts(),
        ha_client.get_scenes(),
        ha_client.get_panels(),
    )

    url_paths, titles = _discover_dashboards(panels)
    lovelace_configs = await asyncio.gather(*[ha_client.get_lovelace_config(p) for p in url_paths])

    locations: list[RenameLocation] = []

    # Registry rename is always included
    locations.append(
        RenameLocation(
            context="registry",
            name="HA entity registry",
            item_id=old_entity_id,
            occurrences=1,
        )
    )

    for auto in automations:
        count = _count_occurrences(auto, old_entity_id)
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

    for script in scripts:
        count = _count_occurrences(script, old_entity_id)
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

    for scene in scenes:
        count = _count_occurrences(scene, old_entity_id)
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

    for url_path, config in zip(url_paths, lovelace_configs, strict=True):
        if config is None:
            continue
        count = _count_occurrences(config, old_entity_id)
        if count:
            title = titles.get(url_path, url_path or "Default")
            locations.append(
                RenameLocation(
                    context="lovelace",
                    name=title,
                    item_id=url_path or "",
                    occurrences=count,
                    raw_config=config,
                )
            )

    return RenamePlan(
        old_entity_id=old_entity_id,
        new_entity_id=new_entity_id,
        locations=locations,
    )


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

_CONTEXT_LABEL: dict[str, str] = {
    "registry": "registry",
    "automation": "automation",
    "script": "script",
    "scene": "scene",
    "lovelace": "dashboard",
}


def display_plan(plan: RenamePlan) -> None:
    """Render the rename plan as a rich table."""
    console.print(
        f"\n  [bold]{plan.old_entity_id}[/bold]"
        f"  [dim]→[/dim]  "
        f"[bold cyan]{plan.new_entity_id}[/bold cyan]\n"
    )

    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("Type", style="dim", width=12)
    table.add_column("Name")
    table.add_column("Hits", justify="right", style="dim")

    for loc in plan.locations:
        label = _CONTEXT_LABEL.get(loc.context, loc.context)
        table.add_row(label, loc.name, str(loc.occurrences))

    console.print(table)

    non_registry = [loc for loc in plan.locations if loc.context != "registry"]
    total_refs = sum(loc.occurrences for loc in non_registry)
    console.print(
        f"\n  [dim]{len(non_registry)} location(s) · {total_refs} reference(s) to update[/dim]"
    )


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


async def execute_rename(ha_client: HAClient, plan: RenamePlan) -> None:
    """Apply all changes from the rename plan."""
    old = plan.old_entity_id
    new = plan.new_entity_id

    for loc in plan.locations:
        if loc.context == "registry":
            console.print("  Renaming entity in HA registry...", end=" ")
            await ha_client.rename_entity_id(old, new)
            console.print("[green]✓[/green]")

        elif loc.context == "automation":
            console.print(f"  Updating automation [dim]{loc.name!r}[/dim]...", end=" ")
            patched = _deep_replace(loc.raw_config, old, new)
            await ha_client.update_automation(loc.item_id, patched)
            console.print("[green]✓[/green]")

        elif loc.context == "script":
            console.print(f"  Updating script [dim]{loc.name!r}[/dim]...", end=" ")
            patched = _deep_replace(loc.raw_config, old, new)
            await ha_client.update_script(loc.item_id, patched)
            console.print("[green]✓[/green]")

        elif loc.context == "scene":
            console.print(f"  Updating scene [dim]{loc.name!r}[/dim]...", end=" ")
            patched = _deep_replace(loc.raw_config, old, new)
            await ha_client.update_scene(loc.item_id, patched)
            console.print("[green]✓[/green]")

        elif loc.context == "lovelace":
            url_path = loc.item_id or None
            console.print(f"  Updating dashboard [dim]{loc.name!r}[/dim]...", end=" ")
            patched = _deep_replace(loc.raw_config, old, new)
            await ha_client.save_lovelace_config(patched, url_path)
            console.print("[green]✓[/green]")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run_rename(
    ha_url: str,
    token: str,
    verify_ssl: bool,
    old_entity_id: str,
    new_entity_id: str,
    apply: bool,
) -> bool:
    ha_client = HAClient(ha_url, token, verify_ssl)

    console.print(f"\nScanning for references to [bold]{old_entity_id}[/bold]...", end=" ")
    try:
        plan = await build_rename_plan(ha_client, old_entity_id, new_entity_id)
    except ValueError as exc:
        console.print(f"\n[red]Error:[/red] {exc}")
        return False
    console.print("[green]✓[/green]")

    display_plan(plan)

    if not apply:
        console.print(
            "\n  [dim]Dry run — pass [bold]--apply[/bold] or confirm below to apply.[/dim]\n"
        )

        if not sys.stdin.isatty():
            console.print(
                "[red]Error:[/red] stdin is not a TTY. "
                "Use [bold]--apply[/bold] to apply changes non-interactively."
            )
            return False

        confirmed = await questionary.confirm(
            "Apply these changes?",
            default=False,
            style=_STYLE,
        ).unsafe_ask_async()

        if not confirmed:
            console.print("[dim]Aborted.[/dim]")
            return True

    console.print()
    await execute_rename(ha_client, plan)
    console.print(
        f"\n[green]✓[/green] Renamed [bold]{old_entity_id}[/bold]"
        f" → [bold cyan]{new_entity_id}[/bold cyan]"
    )
    return True


def rename_command(
    ha_url: str,
    token: str,
    verify_ssl: bool,
    old_entity_id: str,
    new_entity_id: str,
    apply: bool,
) -> None:
    import typer  # noqa: PLC0415

    ok = asyncio.run(run_rename(ha_url, token, verify_ssl, old_entity_id, new_entity_id, apply))
    if not ok:
        raise typer.Exit(code=1)
