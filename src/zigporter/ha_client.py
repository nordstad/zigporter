"""Home Assistant WebSocket and REST API client.

All public methods are async. Registry queries use the WebSocket API (HA 2025+
dropped the REST ZHA endpoint); entity states use the REST API.

The ``YAML_MODE`` sentinel is returned by ``get_lovelace_config`` when HA
reports that the dashboard is managed in YAML mode rather than the UI storage
backend. Use ``is_yaml_mode()`` to distinguish it from a fetch failure (``None``).
"""

import json
import ssl
from contextlib import asynccontextmanager
from typing import Any

import httpx
import websockets

from zigporter.utils import normalize_ieee, parse_z2m_ieee_identifier


class _YamlMode:
    """Sentinel returned by get_lovelace_config when dashboard is in YAML mode."""

    def __repr__(self) -> str:
        return "YAML_MODE"


# Sentinel returned by get_lovelace_config when HA confirms the dashboard is in YAML mode.
# Use is_yaml_mode() to distinguish this from a genuine fetch failure (None).
YAML_MODE = _YamlMode()


def is_yaml_mode(v: object) -> bool:
    """Return True iff v is the YAML_MODE sentinel from get_lovelace_config."""
    return isinstance(v, _YamlMode)


class HAClient:
    """Client for Home Assistant REST and WebSocket APIs.

    Most methods open a fresh WebSocket connection per call. Use
    ``get_all_ws_data`` when you need several registry datasets at once — it
    batches all commands on a single connection.

    Args:
        ha_url: Base URL of the Home Assistant instance
            (e.g. ``"http://homeassistant.local:8123"``).
        token: Long-lived access token created in your HA profile.
        verify_ssl: Set to ``False`` to disable TLS certificate verification
            for self-signed certificates.
    """

    def __init__(self, ha_url: str, token: str, verify_ssl: bool = True) -> None:
        self._ha_url = ha_url.rstrip("/")
        self._token = token
        self._verify_ssl = verify_ssl
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _ssl_context(self) -> bool | ssl.SSLContext:
        if self._verify_ssl:
            return True
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    @property
    def _ws_url(self) -> str:
        return (
            self._ha_url.replace("https://", "wss://").replace("http://", "ws://")
            + "/api/websocket"
        )

    @asynccontextmanager
    async def _ws_session(self):
        """Async context manager that opens an authenticated WebSocket session.

        Yields the WebSocket connection after completing the HA auth handshake.
        """
        ssl_ctx = self._ssl_context()
        async with websockets.connect(self._ws_url, ssl=ssl_ctx, max_size=16 * 1024 * 1024) as ws:
            msg = json.loads(await ws.recv())
            if msg.get("type") != "auth_required":
                raise RuntimeError(f"Expected auth_required, got: {msg}")
            await ws.send(json.dumps({"type": "auth", "access_token": self._token}))
            msg = json.loads(await ws.recv())
            if msg.get("type") != "auth_ok":
                raise RuntimeError(f"WebSocket authentication failed: {msg}")
            yield ws

    async def get_stale_check_data(self) -> dict[str, Any]:
        """Batch-fetch device registry, entity registry, and area registry.

        Opens a single WebSocket connection and fires three commands sequentially.
        Returns a dict with keys: ``device_registry``, ``entity_registry``,
        ``area_registry``.
        """
        commands = [
            ("device_registry", {"type": "config/device_registry/list"}),
            ("entity_registry", {"type": "config/entity_registry/list"}),
            ("area_registry", {"type": "config/area_registry/list"}),
        ]

        async with self._ws_session() as ws:
            results: dict[str, Any] = {}
            for cmd_id, (key, command) in enumerate(commands, start=1):
                await ws.send(json.dumps({"id": cmd_id, **command}))
                msg = json.loads(await ws.recv())
                if not msg.get("success"):
                    raise RuntimeError(f"WebSocket command '{command['type']}' failed: {msg}")
                results[key] = msg["result"]

        return results

    async def get_states(self) -> list[dict[str, Any]]:
        """Fetch all entity states via REST API."""
        async with httpx.AsyncClient(headers=self._headers, verify=self._ssl_context()) as client:
            resp = await client.get(f"{self._ha_url}/api/states")
            resp.raise_for_status()
            return resp.json()

    async def get_all_ws_data(self) -> dict[str, Any]:
        """Open a single WebSocket connection and fetch all registry + ZHA data.

        Returns a dict with keys: zha_devices, entity_registry, device_registry,
        area_registry, automation_configs.
        """
        commands = [
            ("zha_devices", {"type": "zha/devices"}),
            ("entity_registry", {"type": "config/entity_registry/list"}),
            ("device_registry", {"type": "config/device_registry/list"}),
            ("area_registry", {"type": "config/area_registry/list"}),
            ("automation_configs", {"type": "config/automation/list"}),
        ]

        async with self._ws_session() as ws:
            results: dict[str, Any] = {}
            for cmd_id, (key, command) in enumerate(commands, start=1):
                await ws.send(json.dumps({"id": cmd_id, **command}))
                msg = json.loads(await ws.recv())
                if not msg.get("success"):
                    if key == "automation_configs":
                        results[key] = []
                    else:
                        raise RuntimeError(f"WebSocket command '{command['type']}' failed: {msg}")
                else:
                    results[key] = msg["result"]

        return results

    # Individual methods kept for targeted use and testability

    async def _ws_command(self, command: dict[str, Any]) -> Any:
        """Send a single WebSocket command and return the result."""
        async with self._ws_session() as ws:
            cmd = {"id": 1, **command}
            await ws.send(json.dumps(cmd))
            msg = json.loads(await ws.recv())
            if not msg.get("success"):
                raise RuntimeError(f"WebSocket command failed: {msg}")
            return msg["result"]

    async def get_zha_devices(self) -> list[dict[str, Any]]:
        """Fetch all ZHA devices via WebSocket (REST endpoint removed in HA 2025+)."""
        return await self._ws_command({"type": "zha/devices"})

    async def get_zha_network_topology(self) -> dict[str, Any]:
        """Fetch cached ZHA network topology.

        Returns a dict keyed by IEEE address (colon-format) containing per-device
        topology data including the ``neighbors`` list.  Returns an empty dict if ZHA
        is not installed, if the command is unavailable in this HA version, or if no
        scan has been run yet.
        """
        try:
            result = await self._ws_command({"type": "zha/network_topology"})
            return result or {}
        except RuntimeError:
            return {}

    async def run_zha_topology_scan(self) -> dict[str, Any]:
        """Trigger a ZHA network topology scan.

        Sends ``zha/topology/update`` which fires an async background scan in HA
        and returns immediately (empty acknowledgement).  The updated neighbor
        tables become available in the next ``zha/devices`` response once the scan
        completes (typically 30–90 s on real networks).
        Returns ``{}`` always; caller should re-fetch ``get_zha_devices()`` after
        waiting if fresh data is needed.
        """
        try:
            await self._ws_command({"type": "zha/topology/update"})
        except RuntimeError:
            pass
        return {}

    async def get_entity_registry(self) -> list[dict[str, Any]]:
        """Fetch the full entity registry."""
        return await self._ws_command({"type": "config/entity_registry/list"})

    async def get_device_registry(self) -> list[dict[str, Any]]:
        """Fetch the full device registry."""
        return await self._ws_command({"type": "config/device_registry/list"})

    async def get_area_registry(self) -> list[dict[str, Any]]:
        """Fetch the full area registry."""
        return await self._ws_command({"type": "config/area_registry/list"})

    async def get_automation_configs(self) -> list[dict[str, Any]]:
        """Fetch automation configurations. Returns [] if unsupported."""
        try:
            return await self._ws_command({"type": "config/automation/list"})
        except RuntimeError:
            return []

    async def get_scripts(self) -> list[dict[str, Any]]:
        """Fetch UI-managed script configurations. Returns [] if unsupported."""
        try:
            return await self._ws_command({"type": "config/script/list"})
        except RuntimeError:
            return []

    async def get_scenes(self) -> list[dict[str, Any]]:
        """Fetch scene configurations. Returns [] if unsupported."""
        try:
            return await self._ws_command({"type": "config/scene/list"})
        except RuntimeError:
            return []

    async def get_panels(self) -> dict[str, Any]:
        """Fetch all registered frontend panels. Returns {} if unsupported."""
        try:
            return await self._ws_command({"type": "get_panels"})
        except RuntimeError:
            return {}

    async def get_config_entries(self) -> list[dict[str, Any]]:
        """Fetch all config entries (includes helpers like min_max, template, group).

        Returns [] if unsupported.
        """
        try:
            return await self._ws_command({"type": "config_entries/get"}) or []
        except RuntimeError:
            return []

    async def update_config_entry_options(self, entry_id: str, options: dict[str, Any]) -> None:
        """Update a config entry's options and trigger a reload."""
        await self._ws_command(
            {
                "type": "config_entries/update",
                "entry_id": entry_id,
                "options": options,
            }
        )

    async def get_lovelace_config(self, url_path: str | None = None) -> dict[str, Any] | None:
        """Fetch Lovelace config for one dashboard. url_path=None → default dashboard.

        Tries WebSocket first with force=True to bypass HA's in-memory cache.
        Falls back to REST API if the WS command fails.

        Returns the YAML_MODE sentinel (check with is_yaml_mode()) when HA confirms
        the dashboard is in YAML mode. Returns None on other fetch failures.
        """
        cmd: dict[str, Any] = {"type": "lovelace/config", "force": True}
        if url_path is not None:
            cmd["url_path"] = url_path
        _yaml_mode = False
        try:
            result = await self._ws_command(cmd)
            if result is not None:
                if "strategy" in result:
                    return YAML_MODE  # auto-generated dashboard, cannot be saved via WS
                return result
        except RuntimeError as exc:
            if "mode_not_storage" in str(exc) or "config_requires_reload" in str(exc):
                _yaml_mode = True

        # REST fallback — force=true ensures HA reads from .storage/ not memory cache
        try:
            params: dict[str, str] = {"force": "true"}
            if url_path is not None:
                params["url_path"] = url_path
            async with httpx.AsyncClient(
                headers=self._headers, verify=self._ssl_context()
            ) as client:
                resp = await client.get(f"{self._ha_url}/api/lovelace/config", params=params)
                resp.raise_for_status()
                data = resp.json()
                if "strategy" in data:
                    return YAML_MODE  # auto-generated dashboard, cannot be saved via WS
                return data
        except (httpx.HTTPError, ValueError, RuntimeError, OSError):
            return YAML_MODE if _yaml_mode else None

    async def get_z2m_device_id(self, ieee: str) -> str | None:
        """Find the HA device_id for a Z2M-paired device by IEEE address.

        Z2M registers devices with MQTT identifiers like 'zigbee2mqtt_0x<hex>'.
        Returns the HA device_id string, or None if not found.
        """
        norm = normalize_ieee(ieee)

        registry = await self.get_device_registry()
        for entry in registry:
            for platform, identifier in entry.get("identifiers", []):
                if platform != "mqtt":
                    continue
                ident = parse_z2m_ieee_identifier(identifier)
                if ident == norm:
                    return entry["id"]
        return None

    async def get_entity_ids_for_device(self, device_id: str) -> list[str]:
        """Return all entity IDs registered to a given HA device."""
        registry = await self.get_entity_registry()
        return [e["entity_id"] for e in registry if e.get("device_id") == device_id]

    async def get_entities_for_device(self, device_id: str) -> list[dict[str, Any]]:
        """Return full entity registry entries for a given HA device."""
        registry = await self.get_entity_registry()
        return [e for e in registry if e.get("device_id") == device_id]

    async def rename_device_name(self, device_id: str, name_by_user: str) -> None:
        """Set the user-facing name for a device in the HA device registry."""
        await self._ws_command(
            {
                "type": "config/device_registry/update",
                "device_id": device_id,
                "name_by_user": name_by_user,
            }
        )

    async def remove_zha_device(self, ieee: str) -> None:
        """Remove a ZHA device by IEEE address via the zha.remove service."""
        await self.call_service("zha", "remove", {"ieee": ieee})

    async def update_device_area(self, device_id: str, area_id: str) -> None:
        """Assign a device to an area in the HA device registry."""
        await self._ws_command(
            {
                "type": "config/device_registry/update",
                "device_id": device_id,
                "area_id": area_id,
            }
        )

    async def rename_entity_id(self, current_entity_id: str, new_entity_id: str) -> None:
        """Rename an entity ID in the HA entity registry."""
        await self._ws_command(
            {
                "type": "config/entity_registry/update",
                "entity_id": current_entity_id,
                "new_entity_id": new_entity_id,
            }
        )

    async def delete_entity(self, entity_id: str) -> None:
        """Remove an entity from the HA entity registry."""
        await self._ws_command({"type": "config/entity_registry/remove", "entity_id": entity_id})

    async def remove_device(self, device_id: str) -> None:
        """Remove a device entry from the HA device registry."""
        await self._ws_command({"type": "config/device_registry/remove", "device_id": device_id})

    async def reload_config_entry(self, entry_id: str) -> None:
        """Reload a config entry by its ID."""
        await self._ws_command({"type": "config_entries/reload", "entry_id": entry_id})

    async def get_z2m_config_entry_id(self) -> str | None:
        """Find the config entry ID for the Zigbee2MQTT integration.

        Looks for an entry with domain 'mqtt' whose title contains 'zigbee2mqtt'
        (case-insensitive). Returns the entry_id, or None if not found.
        """
        entries = await self.get_config_entries()
        for entry in entries:
            if entry.get("domain") == "mqtt" and "zigbee2mqtt" in entry.get("title", "").lower():
                return entry.get("entry_id")
        return None

    async def save_lovelace_config(
        self, config: dict[str, Any], url_path: str | None = None
    ) -> None:
        """Save (overwrite) a Lovelace dashboard config."""
        cmd: dict[str, Any] = {"type": "lovelace/config/save", "config": config}
        if url_path is not None:
            cmd["url_path"] = url_path
        await self._ws_command(cmd)

    async def update_automation(self, automation_id: str, config: dict[str, Any]) -> None:
        """Update a UI-managed automation config by ID."""
        await self._ws_command(
            {
                "type": "config/automation/update",
                "automation_id": automation_id,
                "config": config,
            }
        )

    async def update_script(self, script_id: str, config: dict[str, Any]) -> None:
        """Update a UI-managed script config by ID."""
        await self._ws_command(
            {
                "type": "config/script/update",
                "script_id": script_id,
                "config": config,
            }
        )

    async def update_scene(self, scene_id: str, config: dict[str, Any]) -> None:
        """Update a UI-managed scene config by ID."""
        await self._ws_command(
            {
                "type": "config/scene/update",
                "scene_id": scene_id,
                "config": config,
            }
        )

    async def call_service(self, domain: str, service: str, service_data: dict[str, Any]) -> None:
        """Call a Home Assistant service via WebSocket."""
        await self._ws_command(
            {
                "type": "call_service",
                "domain": domain,
                "service": service,
                "service_data": service_data,
            }
        )
