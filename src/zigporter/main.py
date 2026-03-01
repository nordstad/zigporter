import json
import os
from importlib.metadata import version as pkg_version
from pathlib import Path

import questionary
import typer
from rich.console import Console

from zigporter.commands.check import check_command
from zigporter.commands.export import export_command, run_export
from zigporter.commands.inspect import inspect_command
from zigporter.commands.list_z2m import list_z2m_command
from zigporter.commands.migrate import migrate_command
from zigporter.commands.setup import setup_command
from zigporter.config import (
    default_export_path,
    default_state_path,
    load_config,
    load_z2m_config,
)

app = typer.Typer(
    name="zigporter",
    help="Migrate Zigbee devices from ZHA to Zigbee2MQTT in a controlled manner.",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
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


def _get_config() -> tuple[str, str, bool]:
    if os.environ.get("ZIGPORTER_DEMO"):
        return ("", "", True)
    _ensure_config()
    try:
        return load_config()
    except ValueError as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        console.print("  Run [bold]zigporter setup[/bold] to update your config.")
        raise typer.Exit(code=1) from exc


def _get_z2m_config() -> tuple[str, str]:
    if os.environ.get("ZIGPORTER_DEMO"):
        return ("", "zigbee2mqtt")
    try:
        return load_z2m_config()
    except ValueError as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        console.print("  Run [bold]zigporter setup[/bold] to update your config.")
        raise typer.Exit(code=1) from exc


def _get_config_optional() -> tuple[str, str, bool]:
    """Like _get_config but returns empty strings instead of exiting on missing values."""
    try:
        return load_config()
    except ValueError:
        return "", "", True


def _get_z2m_config_optional() -> tuple[str, str]:
    try:
        return load_z2m_config()
    except ValueError:
        return "", "zigbee2mqtt"


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
    ha_url, token, verify_ssl = _get_config_optional()
    z2m_url, _ = _get_z2m_config_optional()

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


@app.command(name="list-z2m")
def list_z2m() -> None:
    """List all devices currently paired with Zigbee2MQTT."""
    ha_url, token, verify_ssl = _get_config()
    z2m_url, mqtt_topic = _get_z2m_config()
    list_z2m_command(
        ha_url=ha_url, token=token, z2m_url=z2m_url, verify_ssl=verify_ssl, mqtt_topic=mqtt_topic
    )


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
        except Exception:
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


@app.command()
def migrate(
    zha_export: Path = typer.Argument(
        None,
        help="Path to a ZHA export JSON file. Defaults to zha-export.json in the zigporter config directory.",
    ),
    state: Path = typer.Option(
        None,
        "--state",
        help="Path to the migration state file. Defaults to migration-state.json in the zigporter config directory.",
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
) -> None:
    """Interactive wizard to migrate ZHA devices to Zigbee2MQTT one at a time.

    Tracks progress in a state file so you can safely stop and resume across sessions.
    On first run the tool will check your setup, prompt for a backup, and fetch a
    ZHA export automatically if one is not found.
    """
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
    debug: bool = typer.Option(
        False, "--debug", help="Print Lovelace fetch diagnostics before the report."
    ),
) -> None:
    """Show all automations, scripts, scenes, and dashboard cards that depend on a ZHA device.

    Connects live to Home Assistant, lets you pick a device, and prints a full
    dependency report.
    """
    ha_url, token, verify_ssl = _get_config()
    inspect_command(ha_url=ha_url, token=token, verify_ssl=verify_ssl, debug=debug)


@app.command()
def rename_entity(
    old_entity_id: str = typer.Argument(..., help="Current entity ID to rename."),
    new_entity_id: str = typer.Argument(..., help="New entity ID to assign."),
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
    from zigporter.commands.rename import rename_command  # noqa: PLC0415

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
    old_name: str = typer.Argument(..., help="Current device name (or partial match)."),
    new_name: str = typer.Argument(..., help="New friendly name for the device."),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply the rename. Without this flag the command runs as a dry run.",
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
    from zigporter.commands.rename import rename_device_command  # noqa: PLC0415

    ha_url, token, verify_ssl = _get_config()
    rename_device_command(
        ha_url=ha_url,
        token=token,
        verify_ssl=verify_ssl,
        old_name=old_name,
        new_name=new_name,
        apply=apply,
    )


@app.command(name="fix-device")
def fix_device() -> None:
    """Remove stale ZHA device entries left behind after migration to Zigbee2MQTT.

    Scans HA for devices that have both a stale ZHA entry and an active Z2M entry,
    lets you pick one, deletes the stale ZHA entities, removes the ZHA device from
    the registry, and renames any Z2M entities that got a numeric suffix (e.g. _2)
    back to their original names so dashboard cards work again.
    """
    from zigporter.commands.fix_device import fix_device_command as _fix  # noqa: PLC0415

    ha_url, token, verify_ssl = _get_config()
    _fix(ha_url=ha_url, token=token, verify_ssl=verify_ssl)


if __name__ == "__main__":
    app()
