import json
import os
from datetime import datetime, timezone
from importlib.metadata import version as pkg_version
from pathlib import Path

import questionary
import typer
from rich.console import Console

from zigporter.commands.check import check_command
from zigporter.commands.export import export_command, run_export
from zigporter.commands.inspect import inspect_command
from zigporter.commands.list_devices import list_devices_command
from zigporter.commands.list_z2m import list_z2m_command
from zigporter.commands.migrate import migrate_command
from zigporter.commands.setup import setup_command
from zigporter.config import (
    backup_confirmed_path,
    default_export_path,
    default_state_path,
    load_config,
    load_z2m_config,
)
from zigporter.ui import QUESTIONARY_STYLE

app = typer.Typer(
    name="zigporter",
    help=(
        "Migrate Zigbee devices between ZHA and Zigbee2MQTT. "
        "Supports both ZHA → Z2M (default) and Z2M → ZHA (--direction z2m-to-zha)."
    ),
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
console = Console()
_STYLE = QUESTIONARY_STYLE


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(pkg_version("zigporter"))
        raise typer.Exit()


@app.callback()
def _app_options(
    _version: bool = typer.Option(
        None,
        "--version",
        "-v",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    pass


def _ensure_config() -> None:
    """Auto-run the setup wizard if no config file exists anywhere."""
    if os.environ.get("ZIGPORTER_DEMO"):
        return
    from zigporter.config import config_dir

    if not (config_dir() / ".env").exists() and not (Path.cwd() / ".env").exists():
        console.print("[yellow]No configuration found — starting setup...[/yellow]\n")
        if not setup_command():
            raise typer.Exit(code=1)


def _get_config(*, optional: bool = False) -> tuple[str, str, bool]:
    if os.environ.get("ZIGPORTER_DEMO"):
        return ("", "", True)
    if not optional:
        _ensure_config()
    try:
        return load_config()
    except ValueError as exc:
        if optional:
            return "", "", True
        console.print(f"[red]Configuration error:[/red] {exc}")
        console.print("  Run [bold]zigporter setup[/bold] to update your config.")
        raise typer.Exit(code=1) from exc


def _get_z2m_config(*, optional: bool = False) -> tuple[str, str]:
    if os.environ.get("ZIGPORTER_DEMO"):
        return ("", "zigbee2mqtt")
    try:
        return load_z2m_config()
    except ValueError as exc:
        if optional:
            return "", "zigbee2mqtt"
        console.print(f"[red]Configuration error:[/red] {exc}")
        console.print("  Run [bold]zigporter setup[/bold] to update your config.")
        raise typer.Exit(code=1) from exc


def _confirm_backup_once() -> None:
    """Require one-time confirmation that the user has taken a HA backup."""
    marker = backup_confirmed_path()
    if marker.exists():
        return

    console.print(
        "\n[yellow]Before migrating, create a full Home Assistant backup.[/yellow]\n"
        "[dim]Settings → System → Backups[/dim]"
    )
    confirmed = questionary.confirm(
        "I have created a backup and want to continue",
        default=False,
        style=_STYLE,
    ).ask()
    if not confirmed:
        console.print(
            "[red]Backup confirmation required.[/red] "
            "Run [bold]zigporter migrate[/bold] again after creating a backup."
        )
        raise typer.Exit(code=1)

    try:
        marker.write_text(datetime.now(tz=timezone.utc).isoformat() + "\n")
    except OSError:
        console.print(
            f"[yellow]Warning:[/yellow] Could not save backup confirmation marker at {marker}."
        )
    else:
        console.print("[green]✓[/green] Backup confirmation saved.")


@app.command()
def setup() -> None:
    """Create or update the configuration file in the zigporter config directory.

    Prompts for Home Assistant URL and token (required), and optionally for
    Zigbee2MQTT ingress URL (needed only for migrate and list-z2m), then
    writes them to the user config directory and tests the connection.
    """
    if not setup_command():
        raise typer.Exit(code=1)


@app.command()
def check() -> None:
    """Verify that all requirements are in place before migrating.

    Checks HA connectivity, ZHA status, and Z2M availability.
    Run this before your first migration session.
    """
    _ensure_config()
    ha_url, token, verify_ssl = _get_config(optional=True)
    z2m_url, _ = _get_z2m_config(optional=True)

    ok = check_command(
        ha_url=ha_url,
        token=token,
        verify_ssl=verify_ssl,
        z2m_url=z2m_url,
    )
    if not ok:
        raise typer.Exit(code=1)


@app.command()
def export(
    output: Path = typer.Option(
        None,
        "--output",
        "-o",
        help="Output file path. Defaults to zha-export.json in the zigporter config directory.",
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Pretty-print JSON output."),
) -> None:
    """Export current ZHA devices, entities, areas, and automation references to JSON."""
    ha_url, token, verify_ssl = _get_config()

    if output is None:
        output = default_export_path()

    export_command(output=output, pretty=pretty, ha_url=ha_url, token=token, verify_ssl=verify_ssl)


@app.command(name="export-z2m")
def export_z2m(
    output: Path = typer.Option(
        None,
        "--output",
        "-o",
        help="Output file path. Defaults to z2m-export.json in the zigporter config directory.",
    ),
    pretty: bool = typer.Option(False, "--pretty", help="Pretty-print JSON output."),
) -> None:
    """Export current Z2M devices, entities, areas, and automation references to JSON."""
    from zigporter.commands.export_z2m import z2m_export_command  # noqa: PLC0415
    from zigporter.config import default_z2m_export_path  # noqa: PLC0415

    ha_url, token, verify_ssl = _get_config()
    z2m_url, mqtt_topic = _get_z2m_config()

    if output is None:
        output = default_z2m_export_path()

    z2m_export_command(
        output=output,
        pretty=pretty,
        ha_url=ha_url,
        token=token,
        verify_ssl=verify_ssl,
        z2m_url=z2m_url,
        mqtt_topic=mqtt_topic,
    )


@app.command(name="list-z2m")
def list_z2m(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON instead of a table."),
) -> None:
    """List all devices currently paired with Zigbee2MQTT."""
    ha_url, token, verify_ssl = _get_config()
    z2m_url, mqtt_topic = _get_z2m_config()
    list_z2m_command(
        ha_url=ha_url,
        token=token,
        z2m_url=z2m_url,
        verify_ssl=verify_ssl,
        mqtt_topic=mqtt_topic,
        json_output=json_output,
    )


@app.command(name="list-devices")
def list_devices(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON instead of a table."),
) -> None:
    """List all Home Assistant devices across all integrations."""
    ha_url, token, verify_ssl = _get_config()
    list_devices_command(ha_url=ha_url, token=token, verify_ssl=verify_ssl, json_output=json_output)


def _resolve_or_fetch_export(
    explicit_path: Path | None,
    ha_url: str,
    token: str,
    verify_ssl: bool,
) -> Path:
    """Return the export file path, fetching from HA if needed."""
    if explicit_path is not None:
        return explicit_path

    default = default_export_path()

    if default.exists():
        try:
            data = json.loads(default.read_text())
            exported_at = data.get("exported_at", "unknown date")
            device_count = len(data.get("devices", []))
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            exported_at = "unknown date"
            device_count = 0

        console.print(
            f"\n[dim]Found export from [bold]{exported_at}[/bold] "
            f"({device_count} device(s))[/dim]\n"
        )
        choice = questionary.select(
            "Use existing export or refresh from Home Assistant?",
            choices=[
                questionary.Choice("Use existing export", value="use"),
                questionary.Choice("Refresh — fetch a new export from HA", value="refresh"),
            ],
            style=_STYLE,
        ).ask()

        if choice == "use" or choice is None:
            return default

    else:
        console.print("\n[yellow]No ZHA export found.[/yellow]")
        fetch = questionary.confirm(
            "Fetch a fresh export from Home Assistant now?",
            default=True,
            style=_STYLE,
        ).ask()
        if not fetch:
            console.print(
                "[red]Cannot proceed without a ZHA export.[/red]\n"
                "Run [bold]zigporter export[/bold] first."
            )
            raise typer.Exit(code=1)

    # Run the export and save to the default path
    import asyncio

    console.print()
    export_data = asyncio.run(run_export(ha_url, token, verify_ssl))
    default.write_text(export_data.model_dump_json(indent=2))
    console.print(
        f"[green]✓[/green] Exported [bold]{len(export_data.devices)}[/bold] devices "
        f"to [dim]{default}[/dim]\n"
    )
    return default


def _resolve_or_fetch_z2m_export(
    explicit_path: Path | None,
    ha_url: str,
    token: str,
    verify_ssl: bool,
    z2m_url: str,
    mqtt_topic: str,
) -> Path:
    """Return the Z2M export file path, fetching from HA if needed."""
    from zigporter.commands.export_z2m import run_z2m_export  # noqa: PLC0415
    from zigporter.config import default_z2m_export_path  # noqa: PLC0415

    if explicit_path is not None:
        return explicit_path

    default = default_z2m_export_path()

    if default.exists():
        try:
            data = json.loads(default.read_text())
            exported_at = data.get("exported_at", "unknown date")
            device_count = len(data.get("devices", []))
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            exported_at = "unknown date"
            device_count = 0

        console.print(
            f"\n[dim]Found Z2M export from [bold]{exported_at}[/bold] "
            f"({device_count} device(s))[/dim]\n"
        )
        choice = questionary.select(
            "Use existing Z2M export or refresh from Home Assistant?",
            choices=[
                questionary.Choice("Use existing export", value="use"),
                questionary.Choice("Refresh — fetch a new export from HA", value="refresh"),
            ],
            style=_STYLE,
        ).ask()

        if choice == "use" or choice is None:
            return default

    else:
        console.print("\n[yellow]No Z2M export found.[/yellow]")
        fetch = questionary.confirm(
            "Fetch a fresh Z2M export from Home Assistant now?",
            default=True,
            style=_STYLE,
        ).ask()
        if not fetch:
            console.print(
                "[red]Cannot proceed without a Z2M export.[/red]\n"
                "Run [bold]zigporter export-z2m[/bold] first."
            )
            raise typer.Exit(code=1)

    import asyncio  # noqa: PLC0415

    console.print()
    export_data = asyncio.run(run_z2m_export(ha_url, token, verify_ssl, z2m_url, mqtt_topic))
    default.write_text(export_data.model_dump_json(indent=2))
    console.print(
        f"[green]✓[/green] Exported [bold]{len(export_data.devices)}[/bold] Z2M devices "
        f"to [dim]{default}[/dim]\n"
    )
    return default


@app.command()
def migrate(
    zha_export: Path = typer.Argument(
        None,
        help="Path to an export JSON file. For ZHA→Z2M: ZHA export. "
        "For Z2M→ZHA: Z2M export. Defaults to the appropriate file in the config directory.",
    ),
    state: Path = typer.Option(
        None,
        "--state",
        help="Path to the migration state file. Defaults to the appropriate file "
        "in the zigporter config directory.",
    ),
    status: bool = typer.Option(
        False,
        "--status",
        help="Show migration progress summary and exit without entering the wizard.",
    ),
    skip_checks: bool = typer.Option(
        False,
        "--skip-checks",
        help="Skip pre-flight checks (use for re-runs when you have already verified setup).",
    ),
    direction: str = typer.Option(
        "zha-to-z2m",
        "--direction",
        "-d",
        help="Migration direction: 'zha-to-z2m' (default) or 'z2m-to-zha'.",
    ),
) -> None:
    """Interactive wizard to migrate devices between ZHA and Zigbee2MQTT.

    By default migrates ZHA → Z2M. Use --direction z2m-to-zha for the reverse.
    Tracks progress in a state file so you can safely stop and resume across sessions.
    On first run the tool will check your setup, prompt for a backup, and fetch an
    export automatically if one is not found.
    """
    if direction not in ("zha-to-z2m", "z2m-to-zha"):
        console.print(
            f"[red]Invalid direction:[/red] {direction}\n"
            "  Valid options: 'zha-to-z2m' or 'z2m-to-zha'"
        )
        raise typer.Exit(code=1)

    if os.environ.get("ZIGPORTER_DEMO"):
        from zigporter.demo import demo_migrate_status  # noqa: PLC0415

        demo_migrate_status()
        return

    ha_url, token, verify_ssl = _get_config()
    z2m_url, mqtt_topic = _get_z2m_config()

    if not skip_checks and not status:
        ok = check_command(
            ha_url=ha_url,
            token=token,
            verify_ssl=verify_ssl,
            z2m_url=z2m_url,
        )
        if not ok:
            raise typer.Exit(code=1)

    if not status:
        _confirm_backup_once()

    if direction == "z2m-to-zha":
        from zigporter.commands.migrate_reverse import reverse_migrate_command  # noqa: PLC0415
        from zigporter.config import default_reverse_state_path  # noqa: PLC0415

        export_path = _resolve_or_fetch_z2m_export(
            zha_export, ha_url, token, verify_ssl, z2m_url, mqtt_topic
        )
        state_path = state if state is not None else default_reverse_state_path()

        reverse_migrate_command(
            z2m_export_path=export_path,
            state_path=state_path,
            status_only=status,
            ha_url=ha_url,
            token=token,
            verify_ssl=verify_ssl,
            z2m_url=z2m_url,
            mqtt_topic=mqtt_topic,
        )
    else:
        export_path = _resolve_or_fetch_export(zha_export, ha_url, token, verify_ssl)
        state_path = state if state is not None else default_state_path()

        migrate_command(
            zha_export_path=export_path,
            state_path=state_path,
            status_only=status,
            ha_url=ha_url,
            token=token,
            verify_ssl=verify_ssl,
            z2m_url=z2m_url,
            mqtt_topic=mqtt_topic,
        )


@app.command()
def inspect(
    device: str | None = typer.Argument(
        None,
        help="Device to inspect: entity ID (sensor.x), IEEE address (0x…), or partial name. "
        "Omit to pick interactively.",
    ),
    debug: bool = typer.Option(
        False, "--debug", help="Print Lovelace fetch diagnostics before the report."
    ),
    backend: str = typer.Option(
        "zha",
        "--backend",
        help="Device source to search: zha, z2m (Zigbee2MQTT), or all (every HA device).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Output result as JSON."),
) -> None:
    """Show all automations, scripts, scenes, and dashboard cards that depend on a device.

    Connects live to Home Assistant, lets you pick a device, and prints a full
    dependency report.  Pass a device argument to skip the interactive picker.
    Use --backend z2m or --backend all to inspect non-ZHA devices.
    """
    ha_url, token, verify_ssl = _get_config()
    inspect_command(
        ha_url=ha_url,
        token=token,
        verify_ssl=verify_ssl,
        debug=debug,
        device=device,
        backend=backend,
        json_output=json_output,
    )


@app.command()
def rename_entity(
    old_entity_id: str | None = typer.Argument(
        None, help="Current entity ID to rename. Omit to pick interactively."
    ),
    new_entity_id: str | None = typer.Argument(
        None, help="New entity ID to assign. Omit to be prompted."
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply the rename. Without this flag the command runs as a dry run and prompts for confirmation.",
    ),
) -> None:
    """Rename an entity ID and update all references in automations, scripts, scenes, and dashboards.

    Defaults to a dry run that shows what would change. Pass --apply or confirm
    the prompt to write the changes.

    Note: Jinja2 template strings (e.g. {{ states('old.id') }}) are not patched
    automatically — review them manually after renaming.
    """
    from zigporter.commands.rename_entity import rename_command  # noqa: PLC0415

    ha_url, token, verify_ssl = _get_config()
    rename_command(
        ha_url=ha_url,
        token=token,
        verify_ssl=verify_ssl,
        old_entity_id=old_entity_id,
        new_entity_id=new_entity_id,
        apply=apply,
    )


@app.command()
def rename_device(
    old_name: str | None = typer.Argument(
        None, help="Current device name (or partial match). Omit to pick interactively."
    ),
    new_name: str | None = typer.Argument(
        None, help="New friendly name for the device. Omit to be prompted."
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply the rename. Without this flag the command runs as a dry run.",
    ),
    device_filter: str | None = typer.Option(
        None,
        "--filter",
        help="Restrict picker to a device protocol. Valid values: 'zigbee' (ZHA + Z2M), 'matter' (Matter + Thread).",
    ),
) -> None:
    """Rename a device and cascade the change to all its entities, automations, scripts, scenes, and dashboards.

    Finds the device by name (partial match supported), computes new entity IDs by
    replacing the old device name slug in each entity ID, and interactively prompts for
    any entities whose IDs don't follow the device name pattern.

    Defaults to a dry run. Pass --apply or confirm the prompt to write changes.

    Note: Jinja2 template strings (e.g. {{ states('old.id') }}) are not patched
    automatically — review them manually after renaming.
    """
    from zigporter.commands.rename_device import rename_device_command  # noqa: PLC0415

    ha_url, token, verify_ssl = _get_config()
    rename_device_command(
        ha_url=ha_url,
        token=token,
        verify_ssl=verify_ssl,
        old_name=old_name,
        new_name=new_name,
        apply=apply,
        device_filter=device_filter,
    )


@app.command()
def stale(
    device: str | None = typer.Argument(
        None,
        help="Device name (partial) or HA device ID to target directly. "
        "Omit to pick interactively.",
    ),
    action: str | None = typer.Option(
        None,
        "--action",
        help="Headless action to execute: remove, ignore, mark-stale, suppress, or clear. "
        "Requires a device argument.",
    ),
    note: str | None = typer.Option(
        None,
        "--note",
        help="Note text attached when using --action mark-stale.",
    ),
) -> None:
    """Identify and manage offline/stale devices across all integrations.

    Connects to Home Assistant, lists devices where all entities are
    unavailable, and lets you remove, annotate or ignore them.
    State is persisted to ~/.config/zigporter/stale.json.

    Pass a device argument to skip the picker (semi-headless).
    Add --action to execute without any prompts (fully headless).
    """
    from zigporter.commands.stale import stale_command  # noqa: PLC0415

    ha_url, token, verify_ssl = _get_config()
    stale_command(
        ha_url=ha_url,
        token=token,
        verify_ssl=verify_ssl,
        device=device,
        action=action,
        note=note,
    )


@app.command(name="fix-device")
def fix_device(
    device: str | None = typer.Argument(
        None,
        help="Device to fix: partial name or IEEE address. Omit to pick interactively.",
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply the fix without a confirmation prompt.",
    ),
) -> None:
    """Remove stale ZHA device entries left behind after migration to Zigbee2MQTT.

    Scans HA for devices that have both a stale ZHA entry and an active Z2M entry,
    lets you pick one, deletes the stale ZHA entities, removes the ZHA device from
    the registry, and renames any Z2M entities that got a numeric suffix (e.g. _2)
    back to their original names so dashboard cards work again.

    Pass a device argument to skip the picker.  Add --apply to also skip the
    confirmation prompt (fully non-interactive).
    """
    from zigporter.commands.fix_device import fix_device_command as _fix  # noqa: PLC0415

    ha_url, token, verify_ssl = _get_config()
    _fix(ha_url=ha_url, token=token, verify_ssl=verify_ssl, device=device, apply=apply)


@app.command(name="network-map")
def network_map(
    output_format: str = typer.Option("tree", "--format", help="Output format: tree or table."),
    warn_lqi: int = typer.Option(80, "--warn-lqi", help="LQI below this is flagged WEAK."),
    critical_lqi: int = typer.Option(
        30, "--critical-lqi", help="LQI below this is flagged CRITICAL."
    ),
    output_svg: Path | None = typer.Option(
        None, "--output", help="Write a radial SVG map to this path (e.g. network.svg)."
    ),
    backend: str = typer.Option(
        "auto",
        "--backend",
        help="Zigbee backend: auto (detect), z2m (Zigbee2MQTT), or zha (ZHA).",
    ),
) -> None:
    """Show Zigbee mesh topology with signal strength (LQI) for each device.

    Supports both Zigbee2MQTT and ZHA backends. Use --backend to select one
    explicitly, or let auto-detection pick — if both are available you will be
    prompted to choose. Default view is a router-centric tree. Use
    --format=table for a flat list sorted by LQI ascending (weakest links
    first). Use --output to save a radial SVG diagram with LQI-encoded edges.
    """
    from zigporter.commands.network_map import network_map_command as _nm  # noqa: PLC0415

    ha_url, token, verify_ssl = _get_config(optional=True)
    z2m_url, mqtt_topic = _get_z2m_config(optional=True)
    z2m_frontend_token = os.environ.get("Z2M_FRONTEND_TOKEN")
    _nm(
        ha_url=ha_url,
        token=token,
        z2m_url=z2m_url,
        verify_ssl=verify_ssl,
        mqtt_topic=mqtt_topic,
        output_format=output_format,
        warn_lqi=warn_lqi,
        critical_lqi=critical_lqi,
        output_svg=output_svg,
        backend=backend,
        z2m_frontend_token=z2m_frontend_token,
    )


if __name__ == "__main__":
    app()
