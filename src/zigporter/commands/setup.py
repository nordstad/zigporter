"""Interactive setup wizard — creates or updates .env in the zigporter config directory."""

import asyncio
import ssl
from pathlib import Path

import httpx
import questionary
from rich.console import Console

from zigporter.config import config_dir

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


def _read_current(env_path: Path) -> dict[str, str]:
    from dotenv import dotenv_values

    if env_path.exists():
        return {k: (v or "") for k, v in dotenv_values(env_path).items()}
    return {}


def _mask_token(token: str) -> str:
    if not token:
        return ""
    n = len(token)
    visible = token[-4:] if n >= 4 else token
    return "•" * min(12, n - len(visible)) + visible


def _write_env(
    path: Path,
    ha_url: str,
    ha_token: str,
    verify_ssl: bool,
    z2m_url: str,
    mqtt_topic: str,
) -> None:
    lines = [
        f"HA_URL={ha_url}",
        f"HA_TOKEN={ha_token}",
        f"HA_VERIFY_SSL={'true' if verify_ssl else 'false'}",
        f"Z2M_URL={z2m_url}",
    ]
    if mqtt_topic != "zigbee2mqtt":
        lines.append(f"Z2M_MQTT_TOPIC={mqtt_topic}")
    path.write_text("\n".join(lines) + "\n")


def _ssl_context(verify: bool) -> bool | ssl.SSLContext:
    if verify:
        return True
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def _test_connections(ha_url: str, ha_token: str, verify_ssl: bool, z2m_url: str) -> None:
    headers = {"Authorization": f"Bearer {ha_token}"}
    ssl_ctx = _ssl_context(verify_ssl)

    console.print()
    try:
        async with httpx.AsyncClient(headers=headers, verify=ssl_ctx, timeout=10) as client:
            resp = await client.get(f"{ha_url}/api/")
            if resp.status_code < 500:
                console.print("  [green]✓[/green]  HA reachable")
            else:
                console.print(
                    f"  [red]✗[/red]  HA returned {resp.status_code} — check HA_URL and HA_TOKEN"
                )
    except Exception as exc:
        console.print(f"  [red]✗[/red]  HA not reachable: {exc}")

    try:
        async with httpx.AsyncClient(headers=headers, verify=ssl_ctx, timeout=10) as client:
            resp = await client.get(f"{z2m_url}/api/devices")
            if resp.status_code < 500:
                console.print("  [green]✓[/green]  Z2M reachable")
            else:
                console.print(f"  [red]✗[/red]  Z2M returned {resp.status_code} — check Z2M_URL")
    except Exception as exc:
        console.print(f"  [red]✗[/red]  Z2M not reachable: {exc}")


async def run_setup() -> bool:
    env_path = config_dir() / ".env"
    current = _read_current(env_path)

    console.rule("[bold cyan]zigporter setup[/bold cyan]")
    if current:
        console.print(f"\n  Updating config at [bold]{env_path}[/bold]\n")
    else:
        console.print(
            f"\n  Config will be saved to [bold]{env_path}[/bold]\n"
            "\n"
            "  Where to find values:\n"
            "  [bold]HA token[/bold]:   "
            "Settings → People → your user → Long-Lived Access Tokens\n"
            "  [bold]Z2M URL[/bold]:    "
            "Open the Z2M add-on in HA and copy the URL from your browser\n"
        )

    # HA URL
    ha_url = await questionary.text(
        "Home Assistant URL",
        default=current.get("HA_URL", ""),
        validate=lambda v: (
            True if v.startswith("http") else "Enter a URL starting with http:// or https://"
        ),
        style=_STYLE,
    ).unsafe_ask_async()
    if ha_url is None:
        return False
    ha_url = ha_url.rstrip("/")

    # HA token
    current_token = current.get("HA_TOKEN", "")
    token_suffix = f"  (leave blank to keep {_mask_token(current_token)})" if current_token else ""
    ha_token = await questionary.password(
        f"HA long-lived access token{token_suffix}",
        style=_STYLE,
    ).unsafe_ask_async()
    if ha_token is None:
        return False
    if not ha_token:
        if not current_token:
            console.print("[red]Token is required.[/red]")
            return False
        ha_token = current_token

    # Verify SSL
    current_ssl = current.get("HA_VERIFY_SSL", "true").lower() != "false"
    verify_ssl = await questionary.confirm(
        "Verify SSL certificate",
        default=current_ssl,
        style=_STYLE,
    ).unsafe_ask_async()
    if verify_ssl is None:
        return False

    # Z2M URL
    z2m_url = await questionary.text(
        "Zigbee2MQTT ingress URL",
        default=current.get("Z2M_URL", ""),
        validate=lambda v: (
            True if v.startswith("http") else "Enter a URL starting with http:// or https://"
        ),
        style=_STYLE,
    ).unsafe_ask_async()
    if z2m_url is None:
        return False
    z2m_url = z2m_url.rstrip("/")

    # MQTT topic (optional, only show if non-default already set)
    current_topic = current.get("Z2M_MQTT_TOPIC", "zigbee2mqtt")
    mqtt_topic = await questionary.text(
        "MQTT base topic",
        default=current_topic,
        style=_STYLE,
    ).unsafe_ask_async()
    if mqtt_topic is None:
        return False

    # Save config
    _write_env(env_path, ha_url, ha_token, verify_ssl, z2m_url, mqtt_topic)
    console.print(f"\n[green]✓[/green] Saved to [bold]{env_path}[/bold]")

    # Quick connectivity test
    await _test_connections(ha_url, ha_token, verify_ssl, z2m_url)
    console.print()

    return True


def setup_command() -> bool:
    return asyncio.run(run_setup())
