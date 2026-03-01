import asyncio
import json
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from zigporter.z2m_client import Z2MClient

console = Console()


class SortField(str, Enum):
    lqi = "lqi"
    battery = "battery"
    last_seen = "last-seen"


def _parse_last_seen(value: Any) -> datetime | None:
    """Parse Z2M last_seen into a timezone-aware datetime.

    Z2M can emit last_seen as:
    - An integer (milliseconds since epoch, when last_seen: ISO_8601_local)
    - An ISO 8601 string (when last_seen: ISO_8601_local or ISO_8601_UTC)
    - None / "N/A" / missing when disabled
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=UTC)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str):
        if value in ("N/A", ""):
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _format_relative(dt: datetime | None, now: datetime) -> str:
    if dt is None:
        return "—"
    delta = now - dt
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        return "just now"
    if total_seconds < 120:
        return f"{total_seconds}s ago"
    if total_seconds < 7200:
        return f"{total_seconds // 60}m ago"
    if total_seconds < 86400:
        return f"{total_seconds // 3600}h ago"
    return f"{delta.days}d ago"


def _extract_health(device: dict[str, Any]) -> tuple[int | None, int | None, datetime | None]:
    """Extract (lqi, battery_pct, last_seen_dt) from a Z2M device record."""
    state: dict[str, Any] = device.get("state") or {}

    lqi_raw = device.get("linkquality")
    if lqi_raw is None:
        lqi_raw = state.get("linkquality")
    lqi = int(lqi_raw) if lqi_raw is not None else None

    battery_raw = state.get("battery")
    if battery_raw is None:
        battery_raw = device.get("battery")
    battery = int(battery_raw) if battery_raw is not None else None

    last_seen_dt = _parse_last_seen(device.get("last_seen"))
    return lqi, battery, last_seen_dt


def _row_sort_key(
    row: dict[str, Any],
    sort: SortField | None,
) -> tuple:
    if sort == SortField.lqi:
        lqi = row["lqi"]
        return (lqi is None, lqi if lqi is not None else 0)
    if sort == SortField.battery:
        bat = row["battery"]
        return (bat is None, bat if bat is not None else 0)
    if sort == SortField.last_seen:
        dt = row["last_seen_dt"]
        return (dt is None, dt or datetime.min.replace(tzinfo=UTC))
    # Default: OFFLINE → WARN → OK, then LQI ascending within each group
    status_order = {"OFFLINE": 0, "WARN": 1, "OK": 2}
    lqi = row["lqi"]
    return (status_order.get(row["status"], 3), lqi if lqi is not None else 999)


async def run_z2m_health(
    ha_url: str,
    token: str,
    z2m_url: str,
    verify_ssl: bool,
    mqtt_topic: str,
    sort: SortField | None,
    warn_battery: int,
    warn_lqi: int,
    offline_after: int,
    output_format: str,
) -> bool:
    """Fetch Z2M device health and render a report.

    Returns True if there are no warnings and no offline devices.
    """
    client = Z2MClient(ha_url, token, z2m_url, verify_ssl, mqtt_topic)

    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as progress:
        t = progress.add_task("Fetching Z2M device health...", total=None)
        devices = await client.get_devices()
        progress.update(t, description="Done")

    devices = [d for d in devices if d.get("type") != "Coordinator"]

    now = datetime.now(tz=UTC)
    offline_delta = timedelta(minutes=offline_after)

    rows: list[dict[str, Any]] = []
    for d in devices:
        lqi, battery, last_seen_dt = _extract_health(d)
        name = d.get("friendly_name") or d.get("ieee_address") or "unknown"

        is_offline = last_seen_dt is not None and (now - last_seen_dt) > offline_delta
        has_low_battery = battery is not None and battery < warn_battery
        has_low_lqi = lqi is not None and lqi < warn_lqi

        if is_offline:
            status = "OFFLINE"
        elif has_low_battery or has_low_lqi:
            status = "WARN"
        else:
            status = "OK"

        rows.append(
            {
                "name": name,
                "lqi": lqi,
                "battery": battery,
                "last_seen_dt": last_seen_dt,
                "last_seen_rel": _format_relative(last_seen_dt, now),
                "status": status,
            }
        )

    rows.sort(key=lambda r: _row_sort_key(r, sort))

    n_warn = sum(1 for r in rows if r["status"] == "WARN")
    n_offline = sum(1 for r in rows if r["status"] == "OFFLINE")
    healthy = n_warn == 0 and n_offline == 0

    if output_format == "json":
        payload = [
            {
                "name": r["name"],
                "lqi": r["lqi"],
                "battery": r["battery"],
                "last_seen": r["last_seen_dt"].isoformat() if r["last_seen_dt"] else None,
                "status": r["status"],
            }
            for r in rows
        ]
        console.print(json.dumps(payload, indent=2))
        return healthy

    _render_table(rows, n_warn, n_offline)
    return healthy


def _render_table(rows: list[dict[str, Any]], n_warn: int, n_offline: int) -> None:
    console.rule("[bold cyan]Z2M NETWORK HEALTH[/bold cyan]")

    table = Table(show_header=True, header_style="bold")
    table.add_column("Device", no_wrap=True)
    table.add_column("LQI", justify="right")
    table.add_column("Battery", justify="right")
    table.add_column("Last Seen")
    table.add_column("Status")

    for r in rows:
        lqi_str = str(r["lqi"]) if r["lqi"] is not None else "—"
        battery_str = f"{r['battery']}%" if r["battery"] is not None else "—"

        status = r["status"]
        if status == "OK":
            status_str = "[green]OK[/green]"
            row_style = ""
        elif status == "WARN":
            status_str = "[yellow]⚠ WARN[/yellow]"
            row_style = ""
        else:
            status_str = "[red]✗ OFFLINE[/red]"
            row_style = "dim"

        table.add_row(
            r["name"], lqi_str, battery_str, r["last_seen_rel"], status_str, style=row_style
        )

    console.print(table)

    summary_parts = [f"[bold]{len(rows)}[/bold] device(s)"]
    if n_warn:
        summary_parts.append(f"[yellow]{n_warn} warning(s)[/yellow]")
    if n_offline:
        summary_parts.append(f"[red]{n_offline} offline[/red]")
    if not n_warn and not n_offline:
        summary_parts.append("[green]all healthy[/green]")
    console.print(" — ".join(summary_parts))


def z2m_health_command(
    ha_url: str,
    token: str,
    z2m_url: str,
    verify_ssl: bool,
    mqtt_topic: str,
    sort: SortField | None = None,
    warn_battery: int = 10,
    warn_lqi: int = 50,
    offline_after: int = 60,
    output_format: str = "table",
) -> bool:
    return asyncio.run(
        run_z2m_health(
            ha_url=ha_url,
            token=token,
            z2m_url=z2m_url,
            verify_ssl=verify_ssl,
            mqtt_topic=mqtt_topic,
            sort=sort,
            warn_battery=warn_battery,
            warn_lqi=warn_lqi,
            offline_after=offline_after,
            output_format=output_format,
        )
    )
