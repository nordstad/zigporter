"""rename-entity command — rename an HA entity ID and cascade the change everywhere."""

import asyncio
import re
import sys
from typing import Any

import questionary
from rich.console import Console
from rich.table import Table

from zigporter.ha_client import HAClient
from zigporter.rename_plan import (
    CONTEXT_LABEL,
    RenamePlan,
    apply_location_update,
    fetch_ha_snapshot,
    build_rename_plan_from_snapshot,
)
from zigporter.ui import QUESTIONARY_STYLE

console = Console()

_STYLE = QUESTIONARY_STYLE


# ---------------------------------------------------------------------------
# Plan builder
# ---------------------------------------------------------------------------


async def build_rename_plan(
    ha_client: HAClient,
    old_entity_id: str,
    new_entity_id: str,
) -> RenamePlan:
    """Scan all HA data and build a plan for renaming old_entity_id → new_entity_id."""
    from zigporter.ha_client import is_yaml_mode as _is_yaml_mode  # noqa: PLC0415

    snapshot = await fetch_ha_snapshot(ha_client)
    plan = build_rename_plan_from_snapshot(snapshot, old_entity_id, new_entity_id)
    # Carry over dashboard scan metadata (yaml-mode, scanned names) from snapshot
    plan.scanned_dashboard_names = [
        snapshot.titles.get(p, p or "Overview")
        for p, c in zip(snapshot.url_paths, snapshot.lovelace_configs, strict=True)
        if c is not None and not _is_yaml_mode(c)
    ]
    plan.yaml_mode_dashboard_names = [
        snapshot.titles.get(p, p or "Overview")
        for p, c in zip(snapshot.url_paths, snapshot.lovelace_configs, strict=True)
        if _is_yaml_mode(c)
    ]
    plan.yaml_mode_dashboard_paths = [
        p
        for p, c in zip(snapshot.url_paths, snapshot.lovelace_configs, strict=True)
        if _is_yaml_mode(c)
    ]
    return plan


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------


def _suggest_entity_ids(name: str, registry: list[dict[str, Any]]) -> list[str]:
    """Return entity IDs whose name_by_user or name matches `name` (case-insensitive)."""
    needle = name.strip().lower()
    return [
        e["entity_id"]
        for e in registry
        if (e.get("name_by_user") or "").strip().lower() == needle
        or (e.get("name") or "").strip().lower() == needle
    ]


_ENTITY_ID_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9][a-z0-9_]*$")


def _validate_entity_id(value: str) -> bool | str:
    """Return True if valid, or an error string if not."""
    v = value.strip()
    if not v:
        return "Entity ID cannot be empty"
    if not _ENTITY_ID_RE.match(v):
        return "Must be 'domain.entity_name' using only lowercase letters, digits, and underscores"
    return True


_SEARCH_SENTINEL = "🔍  Search..."


async def pick_entity_interactively(ha_client: HAClient) -> str | None:
    """Two-step picker: choose a domain, then browse or search within it."""
    registry = await ha_client.get_entity_registry()
    sorted_entities = sorted(registry, key=lambda e: e["entity_id"])

    # Group entities by domain.
    by_domain: dict[str, list[dict[str, Any]]] = {}
    for entity in sorted_entities:
        entity_id = entity["entity_id"]
        domain = entity_id.split(".")[0] if "." in entity_id else entity_id
        by_domain.setdefault(domain, []).append(entity)

    # Step 1: domain picker.
    domain_choices = [
        questionary.Choice(f"{domain}  ({len(entities)} entities)", value=domain)
        for domain, entities in sorted(by_domain.items())
    ]
    selected_domain = await questionary.select(
        "Select a domain:",
        choices=domain_choices,
        style=_STYLE,
    ).unsafe_ask_async()
    if not selected_domain:
        return None

    # Build labels for the chosen domain.
    domain_entities = by_domain[selected_domain]
    labels: list[str] = []
    label_to_id: dict[str, str] = {}
    for entity in domain_entities:
        entity_id = entity["entity_id"]
        display = entity.get("name_by_user") or entity.get("name") or ""
        label = f"{display}  ({entity_id})" if display and display != entity_id else entity_id
        labels.append(label)
        label_to_id[label] = entity_id

    # Step 2: full browsable list with a Search sentinel at the top.
    browse_choices: list[questionary.Choice | questionary.Separator] = [
        questionary.Choice(_SEARCH_SENTINEL, value=_SEARCH_SENTINEL),
        questionary.Separator(),
        *[questionary.Choice(label, value=label) for label in labels],
    ]
    picked = await questionary.select(
        f"Select a {selected_domain} entity:",
        choices=browse_choices,
        style=_STYLE,
    ).unsafe_ask_async()
    if not picked:
        return None

    # Step 2b (optional): user chose to search instead of browse.
    if picked == _SEARCH_SENTINEL:
        valid_labels = set(labels)

        def _validate(value: str) -> bool | str:
            if value in valid_labels:
                return True
            return "Type to search, then select an entry from the list"

        picked = await questionary.autocomplete(
            f"Search {selected_domain} entities:",
            choices=labels,
            match_middle=True,
            validate=_validate,
            style=_STYLE,
        ).unsafe_ask_async()
        if not picked:
            return None

    return label_to_id.get(picked)


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
        label = CONTEXT_LABEL.get(loc.context, loc.context)
        table.add_row(label, loc.name, str(loc.occurrences))

    console.print(table)

    # Dashboard scan footer — 0-ref scanned dashboards and YAML-mode dashboards
    auto_dashboard_names = {loc.name for loc in plan.locations if loc.context == "lovelace"}
    for db_name in plan.scanned_dashboard_names:
        if db_name not in auto_dashboard_names:
            console.print(
                f"  [dim]–[/dim]  [dim]{'dashboard':12}[/dim]"
                f"  {db_name}  [dim](0 references — scanned, no matches)[/dim]"
            )
    for db_name in plan.yaml_mode_dashboard_names:
        console.print(
            f"  [dim]–[/dim]  [dim]{'dashboard':12}[/dim]"
            f"  {db_name}  [yellow]⚠ YAML mode — see manual steps below[/yellow]"
        )
    console.print(
        f"  [dim]–[/dim]  [dim]{'energy':12}[/dim]"
        f"  [dim](auto-updated by HA on entity rename)[/dim]"
    )

    non_registry = [loc for loc in plan.locations if loc.context != "registry"]
    total_refs = sum(loc.occurrences for loc in non_registry)
    console.print(
        f"\n  [dim]{len(non_registry)} location(s) · {total_refs} reference(s) to update[/dim]"
    )

    # Manual steps for YAML-mode dashboards
    if plan.yaml_mode_dashboard_names:
        n = len(plan.yaml_mode_dashboard_names)
        s = "s" if n != 1 else ""
        console.print(
            f"\n  [yellow bold]⚠  {n} dashboard{s} stored in YAML — cannot be updated automatically[/yellow bold]\n"
        )
        console.print(
            f"  [dim]Search your HA config files for [bold]{plan.old_entity_id}[/bold]:[/dim]\n"
            f"  [dim]• Studio Code Server add-on → [bold]Ctrl+Shift+F[/bold][/dim]\n"
            f'  [dim]• SSH/terminal → [bold]grep -rn "{plan.old_entity_id}" /config/ --include="*.yaml"[/bold][/dim]\n'
        )
        paths = plan.yaml_mode_dashboard_paths or [None] * len(plan.yaml_mode_dashboard_names)
        for i, (name, url_path) in enumerate(zip(plan.yaml_mode_dashboard_names, paths), 1):
            url = f"/lovelace/{url_path}" if url_path else "/lovelace"
            console.print(f"  [yellow][ ] {i}.[/yellow]  [bold]{name}[/bold]  [dim]{url}[/dim]")
            console.print("\n  [dim]Find and replace:[/dim]\n")
            replace_table = Table(
                show_header=True, header_style="bold dim", box=None, padding=(0, 2)
            )
            replace_table.add_column("Find", style="dim")
            replace_table.add_column("")
            replace_table.add_column("Replace with", style="cyan")
            replace_table.add_row(plan.old_entity_id, "→", plan.new_entity_id)
            console.print(replace_table)
            if i < len(plan.yaml_mode_dashboard_names):
                console.print()


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
        else:
            label = CONTEXT_LABEL.get(loc.context, loc.context)
            console.print(f"  Updating {label} [dim]{loc.name!r}[/dim]...", end=" ")
            warning = await apply_location_update(
                ha_client, loc.context, loc.item_id, loc.raw_config, [(old, new)]
            )
            if warning:
                console.print(f"[yellow]{warning}[/yellow]")
            else:
                console.print("[green]✓[/green]")


# ---------------------------------------------------------------------------
# UI prompt functions (thin wrappers — mock these in tests)
# ---------------------------------------------------------------------------


async def _prompt_new_entity_id(default: str) -> str | None:
    """Prompt for a new entity ID. Returns None if the user cancels."""
    return await questionary.text(
        "New entity ID:",
        default=default,
        validate=_validate_entity_id,
        style=_STYLE,
    ).unsafe_ask_async()


async def _prompt_apply_confirm() -> bool:
    """Ask whether to apply the rename. Returns False if the user declines."""
    return (
        await questionary.confirm(
            "Apply these changes?",
            default=False,
            style=_STYLE,
        ).unsafe_ask_async()
        or False
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run_rename(
    ha_url: str,
    token: str,
    verify_ssl: bool,
    old_entity_id: str | None,
    new_entity_id: str | None,
    apply: bool,
) -> bool:
    ha_client = HAClient(ha_url, token, verify_ssl)

    # 1. Resolve old entity ID
    if old_entity_id is None:
        if not sys.stdin.isatty():
            console.print(
                "[red]Error:[/red] stdin is not a TTY. "
                "Provide an entity ID as the first argument to run non-interactively."
            )
            return False
        old_entity_id = await pick_entity_interactively(ha_client)
        if not old_entity_id:
            return False
    else:
        err = _validate_entity_id(old_entity_id)
        if err is not True:
            console.print(f"[red]Error:[/red] Invalid entity ID {old_entity_id!r}: {err}")
            return False

    # 2. Resolve new entity ID
    if new_entity_id is None:
        if not sys.stdin.isatty():
            console.print(
                "[red]Error:[/red] stdin is not a TTY. "
                "Provide a new entity ID as the second argument to run non-interactively."
            )
            return False
        new_entity_id = await _prompt_new_entity_id(old_entity_id)
        if not new_entity_id or not new_entity_id.strip():
            console.print("[dim]Aborted.[/dim]")
            return True
        new_entity_id = new_entity_id.strip()
    else:
        err = _validate_entity_id(new_entity_id)
        if err is not True:
            console.print(f"[red]Error:[/red] Invalid new entity ID {new_entity_id!r}: {err}")
            return False

    console.print(f"\nScanning for references to [bold]{old_entity_id}[/bold]...", end=" ")
    try:
        plan = await build_rename_plan(ha_client, old_entity_id, new_entity_id)
    except ValueError as exc:
        console.print(f"\n[red]Error:[/red] {exc}")
        if "not found" in str(exc):
            registry = await ha_client.get_entity_registry()
            suggestions = _suggest_entity_ids(old_entity_id, registry)
            if suggestions:
                for suggestion in suggestions:
                    console.print(f"\n  Hint: did you mean [bold]{suggestion}[/bold]?")
                    console.print(f"  Re-run:  zigporter rename-entity {suggestion} <new-id>")
        return False
    console.print("[green]✓[/green]")

    display_plan(plan)

    if not apply:
        if not sys.stdin.isatty():
            console.print(
                "[red]Error:[/red] stdin is not a TTY. "
                "Use [bold]--apply[/bold] to apply changes non-interactively."
            )
            return False

        if not await _prompt_apply_confirm():
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
    old_entity_id: str | None,
    new_entity_id: str | None,
    apply: bool,
) -> None:
    import typer  # noqa: PLC0415

    ok = asyncio.run(run_rename(ha_url, token, verify_ssl, old_entity_id, new_entity_id, apply))
    if not ok:
        raise typer.Exit(code=1)
