import json
from typing import Any

import httpx

from zigporter.utils import normalize_ieee


def _ieee_from_z2m_identifier(identifier: str) -> str | None:
    """Extract a normalized IEEE string from a Z2M MQTT discovery identifier.

    Z2M registers HA devices with identifiers like 'zigbee2mqtt_0x001234567890abcd'.
    Returns a 16-char lowercase hex string, or None if not a Z2M-style identifier.
    """
    prefix = "zigbee2mqtt_"
    if identifier.lower().startswith(prefix):
        candidate = normalize_ieee(identifier[len(prefix) :])
        if len(candidate) == 16:
            return candidate
    # Bare IEEE address (some setups omit the prefix)
    candidate = normalize_ieee(identifier)
    if len(candidate) == 16 and all(c in "0123456789abcdef" for c in candidate):
        return candidate
    return None


class Z2MClient:
    """Zigbee2MQTT client.

    Auth strategy for ingress requests:
      1. Try the request with ``Authorization: Bearer <token>`` directly.
         Works when HA ingress forwards the header (some reverse-proxy setups).
      2. If the response is not JSON, exchange the Bearer token for an ingress
         session cookie via ``POST /api/hassio/ingress/session`` and retry.
      3. If the session exchange fails (no Supervisor, proxy blocks the path,
         non-admin token), fall back to HA-native APIs:
         - Device listing / lookup: HA device registry via WebSocket
         - permit_join / rename: ``mqtt.publish`` service call via WebSocket

    The ``mqtt_topic`` parameter (default ``"zigbee2mqtt"``) must match the
    base topic configured in Z2M. Override via the ``Z2M_MQTT_TOPIC`` env var.
    """

    def __init__(
        self,
        ha_url: str,
        ha_token: str,
        z2m_url: str,
        verify_ssl: bool = True,
        mqtt_topic: str = "zigbee2mqtt",
    ) -> None:
        self._ha_url = ha_url.rstrip("/")
        self._ha_token = ha_token
        self._z2m_url = z2m_url.rstrip("/")
        self._verify_ssl = verify_ssl
        self._mqtt_topic = mqtt_topic
        self._session_token: str | None = None
        self._ha_client_instance: Any = None

    # ------------------------------------------------------------------
    # Ingress HTTP path
    # ------------------------------------------------------------------

    async def _get_ingress_session(self) -> str:
        """Exchange the HA Bearer token for an ingress session cookie."""
        async with httpx.AsyncClient(verify=self._verify_ssl) as client:
            resp = await client.post(
                f"{self._ha_url}/api/hassio/ingress/session",
                headers={
                    "Authorization": f"Bearer {self._ha_token}",
                    "Content-Type": "application/json",
                },
                json={},
            )
            if resp.status_code == 401:
                raise RuntimeError(
                    "Could not create an ingress session (401). "
                    "Falling back to HA device-registry and MQTT service calls."
                )
            resp.raise_for_status()
            data = resp.json()
            return data["data"]["session"] if "data" in data else data["session"]

    async def _session_headers(self) -> dict[str, str]:
        if not self._session_token:
            self._session_token = await self._get_ingress_session()
        return {"Cookie": f"ingress_session={self._session_token}"}

    @staticmethod
    def _is_json_response(resp: httpx.Response) -> bool:
        return "application/json" in resp.headers.get("content-type", "")

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Execute an HTTP request with 3-tier auth fallback (Bearer → session cookie → refresh)."""
        bearer = {"Authorization": f"Bearer {self._ha_token}"}
        url = f"{self._z2m_url}{path}"
        async with httpx.AsyncClient(verify=self._verify_ssl) as client:
            resp = await client.request(method, url, headers=bearer, **kwargs)
            if resp.status_code not in (401, 403) and self._is_json_response(resp):
                resp.raise_for_status()
                return resp.json()

            # Bearer not accepted — fall back to ingress session cookie
            headers = await self._session_headers()
            resp = await client.request(method, url, headers=headers, **kwargs)
            if resp.status_code == 401:
                self._session_token = None
                headers = await self._session_headers()
                resp = await client.request(method, url, headers=headers, **kwargs)
            resp.raise_for_status()
            return resp.json()

    async def _get(self, path: str) -> Any:
        return await self._request("GET", path)

    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        return await self._request("POST", path, json=body)

    # ------------------------------------------------------------------
    # HA-native fallback path
    # ------------------------------------------------------------------

    def _ha_client(self) -> Any:
        if self._ha_client_instance is None:
            from zigporter.ha_client import HAClient

            self._ha_client_instance = HAClient(self._ha_url, self._ha_token, self._verify_ssl)
        return self._ha_client_instance

    async def _get_devices_via_ha(self) -> list[dict[str, Any]]:
        """Get Z2M devices from the HA device registry.

        Z2M registers each device with an MQTT-platform identifier of the form
        ``zigbee2mqtt_0x<ieee_hex>``.  We filter for those and reconstruct the
        same dict shape that the Z2M REST API returns.
        """
        registry = await self._ha_client().get_device_registry()
        devices = []
        for entry in registry:
            ieee_hex = None
            for platform, identifier in entry.get("identifiers", []):
                if platform == "mqtt":
                    ieee_hex = _ieee_from_z2m_identifier(identifier)
                    if ieee_hex:
                        break
            if ieee_hex is None:
                continue

            manufacturer = entry.get("manufacturer") or ""
            model = entry.get("model") or ""
            devices.append(
                {
                    "ieee_address": f"0x{ieee_hex}",
                    "friendly_name": entry.get("name_by_user")
                    or entry.get("name")
                    or f"0x{ieee_hex}",
                    "type": "EndDevice",
                    "manufacturer": manufacturer,
                    "model_id": model,
                    "definition": {"vendor": manufacturer, "model": model},
                    "power_source": "",
                    "supported": True,
                }
            )
        return devices

    async def _mqtt_publish(self, topic: str, payload: str) -> None:
        """Publish an MQTT message via HA's ``mqtt.publish`` service."""
        await self._ha_client().call_service(
            "mqtt", "publish", {"topic": topic, "payload": payload}
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_devices(self) -> list[dict[str, Any]]:
        """Return the full Z2M device list."""
        try:
            return await self._get("/api/devices")
        except (RuntimeError, httpx.HTTPStatusError, httpx.RequestError):
            return await self._get_devices_via_ha()

    async def get_device_by_ieee(self, ieee: str) -> dict[str, Any] | None:
        """Find a Z2M device by IEEE address. Returns None if not found."""
        devices = await self.get_devices()
        target = normalize_ieee(ieee)
        for device in devices:
            if normalize_ieee(device.get("ieee_address", "")) == target:
                return device
        return None

    async def enable_permit_join(self, seconds: int = 120) -> None:
        """Open the Z2M network for new devices to join."""
        try:
            await self._post("/api/permit_join", {"time": seconds, "device": None})
        except RuntimeError:
            await self._mqtt_publish(
                f"{self._mqtt_topic}/bridge/request/permit_join",
                json.dumps({"time": seconds}),
            )

    async def disable_permit_join(self) -> None:
        """Close the Z2M network to new joiners."""
        try:
            await self._post("/api/permit_join", {"time": 0, "device": None})
        except RuntimeError:
            await self._mqtt_publish(
                f"{self._mqtt_topic}/bridge/request/permit_join",
                json.dumps({"time": 0}),
            )

    async def rename_device(self, current_name: str, new_name: str) -> None:
        """Rename a Z2M device by its current friendly name."""
        try:
            await self._post("/api/device", {"id": current_name, "rename": new_name})
        except RuntimeError:
            # homeassistant_rename=True also updates HA entity IDs
            await self._mqtt_publish(
                f"{self._mqtt_topic}/bridge/request/device/rename",
                json.dumps({"from": current_name, "to": new_name, "homeassistant_rename": True}),
            )
