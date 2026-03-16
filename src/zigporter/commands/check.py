import asyncio
import sys

import httpx
import questionary
from rich.console import Console

from zigporter.ha_client import HAClient
from zigporter.models import CheckResult, CheckStatus
from zigporter.ui import QUESTIONARY_STYLE

console = Console()

_STYLE = QUESTIONARY_STYLE

_STATUS_ICON = {
    CheckStatus.OK: "[green]✓[/green]",
    CheckStatus.FAILED: "[red]✗[/red]",
    CheckStatus.WARNING: "[yellow]![/yellow]",
    CheckStatus.SKIPPED: "[dim]–[/dim]",
}


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


async def _check_config(ha_url: str, token: str, z2m_url: str) -> CheckResult:
    missing = [
        name
        for name, val in [("HA_URL", ha_url), ("HA_TOKEN", token), ("Z2M_URL", z2m_url)]
        if not val
    ]
    if missing:
        return CheckResult(
            name="Configuration",
            status=CheckStatus.FAILED,
            message=f"Missing: {', '.join(missing)} — add to .env or set as environment variables",
        )
    return CheckResult(
        name="Configuration", status=CheckStatus.OK, message="HA_URL, HA_TOKEN, Z2M_URL are set"
    )


async def _check_ha_reachable(ha_url: str, token: str, verify_ssl: bool) -> CheckResult:
    if not ha_url:
        return CheckResult(
            name="HA reachable",
            status=CheckStatus.SKIPPED,
            message="Skipped (no HA_URL configured)",
        )
    try:
        async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}"},
            verify=verify_ssl,
            timeout=10,
        ) as client:
            resp = await client.get(f"{ha_url}/api/")
            resp.raise_for_status()
        return CheckResult(name="HA reachable", status=CheckStatus.OK, message=ha_url)
    except (httpx.HTTPError, OSError, RuntimeError) as exc:
        return CheckResult(
            name="HA reachable",
            status=CheckStatus.FAILED,
            message=f"Cannot reach {ha_url} — {exc}",
        )


async def _check_zha_active(ha_url: str, token: str, verify_ssl: bool) -> CheckResult:
    if not ha_url:
        return CheckResult(
            name="ZHA active",
            status=CheckStatus.SKIPPED,
            message="Skipped (no HA_URL configured)",
        )
    try:
        client = HAClient(ha_url, token, verify_ssl)
        devices = await client.get_zha_devices()
        count = len(devices)
        if count == 0:
            return CheckResult(
                name="ZHA active",
                status=CheckStatus.WARNING,
                message="ZHA is reachable but no devices found — is ZHA configured?",
                blocking=False,
            )
        return CheckResult(
            name="ZHA active",
            status=CheckStatus.OK,
            message=f"{count} device(s) found",
        )
    except (httpx.HTTPError, OSError, RuntimeError) as exc:
        return CheckResult(
            name="ZHA active",
            status=CheckStatus.FAILED,
            message=f"Could not query ZHA — {exc}",
        )


async def _check_z2m_running(
    ha_url: str, token: str, z2m_url: str, verify_ssl: bool
) -> CheckResult:
    if not z2m_url:
        return CheckResult(
            name="Z2M running",
            status=CheckStatus.SKIPPED,
            message="Skipped (no Z2M_URL configured)",
        )
    try:
        async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}"},
            verify=verify_ssl,
            timeout=10,
        ) as client:
            resp = await client.get(f"{z2m_url}/api/devices")
            # Any HTTP response (even 401) means the server is reachable
            if resp.status_code < 500:
                try:
                    devices = resp.json()
                    count = len(devices) if isinstance(devices, list) else "?"
                    return CheckResult(
                        name="Z2M running",
                        status=CheckStatus.OK,
                        message=f"{count} device(s) paired",
                    )
                except ValueError:
                    return CheckResult(
                        name="Z2M running",
                        status=CheckStatus.OK,
                        message="Z2M is responding",
                    )
            resp.raise_for_status()
            return CheckResult(
                name="Z2M running", status=CheckStatus.OK, message="Z2M is responding"
            )
    except (httpx.HTTPError, OSError, RuntimeError) as exc:
        return CheckResult(
            name="Z2M running",
            status=CheckStatus.FAILED,
            message=f"Cannot reach Zigbee2MQTT at {z2m_url} — {exc}",
        )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def _run_checks(
    ha_url: str,
    token: str,
    verify_ssl: bool,
    z2m_url: str,
) -> list[CheckResult]:
    results: list[CheckResult] = []

    config_result = await _check_config(ha_url, token, z2m_url)
    results.append(config_result)

    # Only run network checks if config is valid
    if config_result.status == CheckStatus.OK:
        ha_result = await _check_ha_reachable(ha_url, token, verify_ssl)
        results.append(ha_result)

        # ZHA depends on HA being reachable
        if ha_result.status == CheckStatus.OK:
            results.append(await _check_zha_active(ha_url, token, verify_ssl))
        else:
            results.append(
                CheckResult(
                    name="ZHA active",
                    status=CheckStatus.SKIPPED,
                    message="Skipped (HA not reachable)",
                )
            )

        results.append(await _check_z2m_running(ha_url, token, z2m_url, verify_ssl))
    else:
        for name in ("HA reachable", "ZHA active", "Z2M running"):
            results.append(
                CheckResult(
                    name=name, status=CheckStatus.SKIPPED, message="Skipped (invalid config)"
                )
            )

    return results


def _print_results(results: list[CheckResult]) -> None:
    console.print()
    for r in results:
        icon = _STATUS_ICON[r.status]
        label = f"[bold]{r.name:<20}[/bold]"
        console.print(f"  {icon}  {label}  {r.message}")
    console.print()


def check_command(
    ha_url: str,
    token: str,
    verify_ssl: bool,
    z2m_url: str,
) -> bool:
    """Run all preflight checks. Returns True if the user should proceed, False to abort."""
    console.rule("[bold cyan]Pre-flight checks[/bold cyan]")

    results = asyncio.run(_run_checks(ha_url, token, verify_ssl, z2m_url))
    _print_results(results)

    blocking_failures = [r for r in results if r.status == CheckStatus.FAILED and r.blocking]
    if blocking_failures:
        console.print("[yellow]One or more checks failed.[/yellow]")
        if not sys.stdin.isatty():
            console.print("[yellow]Non-interactive environment — aborting.[/yellow]")
            return False
        proceed = questionary.confirm("Proceed anyway?", default=False, style=_STYLE).ask()
        if not proceed:
            return False

    console.rule()
    return True
