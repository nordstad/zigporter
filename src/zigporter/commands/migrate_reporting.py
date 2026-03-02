"""Reporting and checklist helpers for the migrate wizard."""

import asyncio

from rich.console import Console

from zigporter.entity_refs import collect_config_entity_ids
from zigporter.ha_client import HAClient
from zigporter.models import ZHADevice


async def step_show_test_checklist(
    device: ZHADevice,
    ha_client: HAClient,
    console: Console,
) -> None:
    """Display a checklist of automations, scripts, and scenes to test after migration."""
    old_ids = {e.entity_id for e in device.entities}
    try:
        scripts, scenes = await asyncio.gather(ha_client.get_scripts(), ha_client.get_scenes())
    except (RuntimeError, OSError):
        return

    matching_scripts: list[tuple[str, list[str]]] = []
    for script in scripts:
        refs = collect_config_entity_ids(script) & old_ids
        if refs:
            name = script.get("alias") or script.get("id", "?")
            matching_scripts.append((name, sorted(refs)))

    matching_scenes: list[tuple[str, list[str]]] = []
    for scene in scenes:
        refs = set(scene.get("entities", {}).keys()) & old_ids
        if refs:
            name = scene.get("name") or scene.get("id", "?")
            matching_scenes.append((name, sorted(refs)))

    has_automations = bool(device.automations)
    has_scripts = bool(matching_scripts)
    has_scenes = bool(matching_scenes)
    if not has_automations and not has_scripts and not has_scenes:
        return

    console.print("\n")
    console.rule("[bold cyan]Post-migration test checklist[/bold cyan]")

    if has_automations:
        console.print("\n  [bold]Automations[/bold]")
        for auto in device.automations:
            console.print(f"  [cyan]□[/cyan]  {auto.alias}")
            for eid in auto.entity_references:
                console.print(f"       [dim]{eid}[/dim]")

    if has_scripts:
        console.print("\n  [bold]Scripts[/bold]")
        for name, refs in matching_scripts:
            console.print(f"  [cyan]□[/cyan]  {name}")
            for eid in refs:
                console.print(f"       [dim]{eid}[/dim]")

    if has_scenes:
        console.print("\n  [bold]Scenes[/bold]")
        for name, refs in matching_scenes:
            console.print(f"  [cyan]□[/cyan]  {name}")
            for eid in refs:
                console.print(f"       [dim]{eid}[/dim]")

    console.print(
        "\n  [dim]Tip: Also check your Lovelace dashboards for cards referencing these entities.[/dim]"
    )
    console.rule()


async def step_show_inspect_summary(
    device: ZHADevice,
    ha_client: HAClient,
    console: Console,
) -> None:
    """Show current entities and dashboard cards for the migrated Z2M device."""
    from zigporter.commands.inspect import show_migrate_inspect_summary  # noqa: PLC0415

    try:
        z2m_device_id = await ha_client.get_z2m_device_id(device.ieee)
        if z2m_device_id is None:
            return

        full_registry = await ha_client.get_entity_registry()
        entity_ids = [
            e["entity_id"]
            for e in full_registry
            if e.get("device_id") == z2m_device_id and not e.get("disabled_by")
        ]
        if not entity_ids:
            return

        console.print()
        console.rule(f"[bold cyan]{device.name}[/bold cyan]")
        await show_migrate_inspect_summary(entity_ids, ha_client)
        console.rule()
    except (RuntimeError, OSError, KeyError, ValueError):
        # Non-critical — never interrupt the wizard.
        pass


async def show_device_dependencies(
    device: ZHADevice,
    ha_client: HAClient,
    console: Console,
) -> None:
    """Show automations, scripts, and scenes that reference this device pre-migration."""
    old_ids = {e.entity_id for e in device.entities}
    matching_scripts: list[dict] = []
    matching_scenes: list[dict] = []

    try:
        scripts, scenes = await asyncio.gather(ha_client.get_scripts(), ha_client.get_scenes())
        matching_scripts = [s for s in scripts if collect_config_entity_ids(s) & old_ids]
        matching_scenes = [s for s in scenes if set(s.get("entities", {}).keys()) & old_ids]
    except (RuntimeError, OSError):
        # Optional dependency preview only.
        pass

    has_automations = bool(device.automations)
    has_scripts = bool(matching_scripts)
    has_scenes = bool(matching_scenes)
    if not has_automations and not has_scripts and not has_scenes:
        return

    console.print()
    console.rule("[bold cyan]Dependencies[/bold cyan]")
    console.print("[dim]These will need testing after migration:[/dim]\n")

    if has_automations:
        console.print(f"  [bold]Automations[/bold] ({len(device.automations)})")
        for auto in device.automations:
            console.print(f"  [cyan]□[/cyan]  {auto.alias}")

    if has_scripts:
        console.print(f"\n  [bold]Scripts[/bold] ({len(matching_scripts)})")
        for script in matching_scripts:
            name = script.get("alias") or script.get("id", "?")
            console.print(f"  [cyan]□[/cyan]  {name}")

    if has_scenes:
        console.print(f"\n  [bold]Scenes[/bold] ({len(matching_scenes)})")
        for scene in matching_scenes:
            name = scene.get("name") or scene.get("id", "?")
            console.print(f"  [cyan]□[/cyan]  {name}")

    console.print(
        "\n  [dim]Tip: Run [bold]zigporter inspect[/bold] for dashboard cards "
        "and full entity details.[/dim]"
    )
    console.rule()
