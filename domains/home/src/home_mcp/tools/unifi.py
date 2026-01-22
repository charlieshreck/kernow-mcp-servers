"""UniFi network management tools."""

import os
import ssl
import logging
from typing import Optional, List, Dict, Any

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
UNIFI_HOST = os.environ.get("UNIFI_HOST", "https://10.10.0.154:11443")
UNIFI_API_KEY = os.environ.get("UNIFI_API_KEY", "")
UNIFI_USER = os.environ.get("UNIFI_USER", "")
UNIFI_PASSWORD = os.environ.get("UNIFI_PASSWORD", "")
UNIFI_SITE = os.environ.get("UNIFI_SITE", "default")


def _get_ssl_context():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.set_ciphers('DEFAULT:@SECLEVEL=1')
    return ctx


SSL_CONTEXT = _get_ssl_context()


class UniFiSession:
    """Session manager for operations requiring CSRF token."""

    def __init__(self):
        self.cookies = {}
        self.csrf_token = None

    async def login(self) -> bool:
        """Login with username/password to get session cookies and CSRF token."""
        if not UNIFI_USER or not UNIFI_PASSWORD:
            return False
        try:
            async with httpx.AsyncClient(verify=SSL_CONTEXT, timeout=30.0) as client:
                response = await client.post(
                    f"{UNIFI_HOST}/api/auth/login",
                    json={"username": UNIFI_USER, "password": UNIFI_PASSWORD}
                )
                response.raise_for_status()
                self.cookies = {k: v for k, v in response.cookies.items()}
                self.csrf_token = response.headers.get("X-CSRF-Token", "")
                return True
        except Exception as e:
            logger.error(f"Session login failed: {e}")
            return False

    async def ensure_session(self) -> bool:
        if not self.cookies or not self.csrf_token:
            return await self.login()
        return True


SESSION = UniFiSession()


async def unifi_api(endpoint: str, method: str = "GET", data: dict = None,
                    require_session: bool = False) -> Any:
    """Make request to UniFi API."""
    async with httpx.AsyncClient(verify=SSL_CONTEXT, timeout=30.0) as client:
        headers = {}
        cookies = {}

        if UNIFI_API_KEY:
            headers["X-API-KEY"] = UNIFI_API_KEY
        elif require_session:
            if await SESSION.ensure_session():
                cookies = SESSION.cookies
                if SESSION.csrf_token:
                    headers["X-CSRF-Token"] = SESSION.csrf_token

        url = f"{UNIFI_HOST}/proxy/network/api/s/{UNIFI_SITE}/{endpoint}"
        if method == "GET":
            response = await client.get(url, headers=headers, cookies=cookies)
        elif method == "POST":
            response = await client.post(url, headers=headers, cookies=cookies, json=data)
        elif method == "PUT":
            response = await client.put(url, headers=headers, cookies=cookies, json=data)
        elif method == "DELETE":
            response = await client.delete(url, headers=headers, cookies=cookies)
        else:
            return {"error": f"Unsupported method: {method}"}

        response.raise_for_status()
        result = response.json()
        return result.get("data", result)


async def get_status() -> dict:
    """Get UniFi status for health checks."""
    try:
        health = await unifi_api("stat/health")
        if isinstance(health, list):
            return {"status": "healthy", "subsystems": len(health)}
        return {"status": "unhealthy", "error": "Unexpected response"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


def register_tools(mcp: FastMCP):
    """Register UniFi tools with the MCP server."""

    @mcp.tool()
    async def unifi_list_clients(search: Optional[str] = None) -> List[dict]:
        """List connected WiFi clients with signal, traffic, and connection info."""
        try:
            clients = await unifi_api("stat/sta")
            result = []
            for c in clients:
                name = c.get("name") or c.get("hostname") or c.get("mac")
                if search and search.lower() not in str(name).lower() and search not in c.get("ip", ""):
                    continue
                result.append({
                    "name": name,
                    "mac": c.get("mac"),
                    "ip": c.get("ip"),
                    "signal": c.get("signal"),
                    "rx_bytes": c.get("rx_bytes"),
                    "tx_bytes": c.get("tx_bytes"),
                    "essid": c.get("essid"),
                    "ap_mac": c.get("ap_mac")
                })
            return result
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def unifi_list_devices() -> List[dict]:
        """List UniFi network devices (APs, switches, gateways) with status and stats."""
        try:
            devices = await unifi_api("stat/device")
            return [{
                "name": d.get("name"),
                "mac": d.get("mac"),
                "ip": d.get("ip"),
                "type": d.get("type"),
                "model": d.get("model"),
                "state": d.get("state"),
                "adopted": d.get("adopted"),
                "uptime": d.get("uptime"),
                "clients": d.get("num_sta", 0)
            } for d in devices]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def unifi_list_events(limit: int = 20, event_type: Optional[str] = None) -> List[dict]:
        """List recent network events (connections, disconnections, roams, alerts)."""
        try:
            events = await unifi_api(f"stat/event?_limit={limit}")
            result = []
            for e in events:
                if event_type and e.get("key") != event_type:
                    continue
                result.append({
                    "type": e.get("key"),
                    "msg": e.get("msg"),
                    "time": e.get("time"),
                    "user": e.get("user"),
                    "hostname": e.get("hostname")
                })
            return result
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def unifi_get_health() -> dict:
        """Get network health summary for WAN, LAN, WLAN subsystems."""
        try:
            health = await unifi_api("stat/health")
            return {h.get("subsystem"): {
                "status": h.get("status"),
                "num_user": h.get("num_user"),
                "num_ap": h.get("num_ap"),
                "num_sw": h.get("num_sw")
            } for h in health}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def unifi_get_alarms() -> List[dict]:
        """Get active alarms and alerts."""
        try:
            alarms = await unifi_api("stat/alarm")
            return [{
                "type": a.get("key"),
                "msg": a.get("msg"),
                "time": a.get("time"),
                "archived": a.get("archived")
            } for a in alarms if not a.get("archived")]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def unifi_list_rogueaps() -> List[dict]:
        """List detected rogue/neighbor access points for RF analysis."""
        try:
            rogues = await unifi_api("stat/rogueap")
            return [{
                "bssid": r.get("bssid"),
                "essid": r.get("essid"),
                "channel": r.get("channel"),
                "rssi": r.get("rssi"),
                "is_rogue": r.get("is_rogue"),
                "security": r.get("security")
            } for r in rogues[:50]]  # Limit to 50
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def unifi_get_dpi() -> dict:
        """Get Deep Packet Inspection traffic analysis by category."""
        try:
            dpi = await unifi_api("stat/dpi")
            return dpi
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def unifi_list_wlans() -> List[dict]:
        """List all configured WLANs (SSIDs) with detailed settings."""
        try:
            wlans = await unifi_api("rest/wlanconf")
            return [{
                "name": w.get("name"),
                "_id": w.get("_id"),
                "enabled": w.get("enabled"),
                "security": w.get("security"),
                "wpa_mode": w.get("wpa_mode"),
                "is_guest": w.get("is_guest"),
                "hide_ssid": w.get("hide_ssid")
            } for w in wlans]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def unifi_create_wlan(name: str, passphrase: str, band: str = "both",
                                 hide_ssid: bool = False, client_isolation: bool = False,
                                 iot_optimized: bool = False, enabled: bool = True,
                                 confirmation: bool = False) -> dict:
        """Create a new WLAN (SSID). Set confirmation=true to execute."""
        if not confirmation:
            return {"warning": "Set confirmation=true to create WLAN", "name": name}
        try:
            data = {
                "name": name,
                "x_passphrase": passphrase,
                "enabled": enabled,
                "hide_ssid": hide_ssid,
                "ap_group_ids": [],
                "wpa_mode": "wpa2",
                "security": "wpapsk"
            }
            if client_isolation:
                data["l2_isolation"] = True
            if iot_optimized:
                data["dtim_mode"] = "custom"
                data["dtim_ng"] = 3
                data["uapsd_enabled"] = True

            result = await unifi_api("rest/wlanconf", method="POST", data=data, require_session=True)
            return {"success": True, "wlan": result}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def unifi_update_wlan(wlan_id: str, name: Optional[str] = None,
                                 passphrase: Optional[str] = None, enabled: Optional[bool] = None,
                                 band: Optional[str] = None, client_isolation: Optional[bool] = None,
                                 confirmation: bool = False) -> dict:
        """Update an existing WLAN. Set confirmation=true to execute."""
        if not confirmation:
            return {"warning": "Set confirmation=true to update WLAN"}
        try:
            data = {}
            if name is not None:
                data["name"] = name
            if passphrase is not None:
                data["x_passphrase"] = passphrase
            if enabled is not None:
                data["enabled"] = enabled
            if client_isolation is not None:
                data["l2_isolation"] = client_isolation

            result = await unifi_api(f"rest/wlanconf/{wlan_id}", method="PUT", data=data, require_session=True)
            return {"success": True, "wlan": result}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def unifi_delete_wlan(wlan_id: str, confirmation: bool = False) -> dict:
        """Delete a WLAN. DESTRUCTIVE - requires confirmation=true."""
        if not confirmation:
            return {"warning": "Set confirmation=true to delete WLAN"}
        try:
            await unifi_api(f"rest/wlanconf/{wlan_id}", method="DELETE", require_session=True)
            return {"success": True, "message": f"WLAN {wlan_id} deleted"}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def unifi_restart_device(mac: str, confirmation: bool = False) -> dict:
        """Restart a UniFi device. DESTRUCTIVE - requires confirmation=true."""
        if not confirmation:
            return {"warning": "Set confirmation=true to restart device"}
        try:
            await unifi_api("cmd/devmgr", method="POST",
                           data={"cmd": "restart", "mac": mac}, require_session=True)
            return {"success": True, "message": f"Restart command sent to {mac}"}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def unifi_locate_device(mac: str) -> dict:
        """Flash the LED on a device to locate it physically."""
        try:
            await unifi_api("cmd/devmgr", method="POST",
                           data={"cmd": "set-locate", "mac": mac}, require_session=True)
            return {"success": True, "message": f"Locate mode enabled for {mac}"}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def unifi_block_client(mac: str, confirmation: bool = False) -> dict:
        """Block a client from connecting. DESTRUCTIVE - requires confirmation=true."""
        if not confirmation:
            return {"warning": "Set confirmation=true to block client"}
        try:
            await unifi_api("cmd/stamgr", method="POST",
                           data={"cmd": "block-sta", "mac": mac}, require_session=True)
            return {"success": True, "message": f"Client {mac} blocked"}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def unifi_unblock_client(mac: str, confirmation: bool = False) -> dict:
        """Unblock a previously blocked client. Requires confirmation=true."""
        if not confirmation:
            return {"warning": "Set confirmation=true to unblock client"}
        try:
            await unifi_api("cmd/stamgr", method="POST",
                           data={"cmd": "unblock-sta", "mac": mac}, require_session=True)
            return {"success": True, "message": f"Client {mac} unblocked"}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def unifi_name_client(mac: str, name: str) -> dict:
        """Assign a friendly name to a client for easier identification."""
        try:
            await unifi_api("cmd/stamgr", method="POST",
                           data={"cmd": "set-fingerprint", "mac": mac, "name": name}, require_session=True)
            return {"success": True, "message": f"Named client {mac} as '{name}'"}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def unifi_get_rf_settings() -> dict:
        """Get current RF optimization settings (Radio AI, Roaming Assistant, etc.)."""
        try:
            settings = await unifi_api("rest/setting/super_mgmt")
            if isinstance(settings, list) and settings:
                s = settings[0]
                return {
                    "radio_ai": s.get("radio_ai_enabled", False),
                    "roaming_assistant": s.get("roaming_assistant_enabled", False),
                    "band_steering": s.get("band_steering_enabled", False)
                }
            return settings
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def unifi_set_rf_setting(setting: str, enabled: bool, confirmation: bool = False) -> dict:
        """Configure RF optimization settings. Requires confirmation=true."""
        if not confirmation:
            return {"warning": "Set confirmation=true to change RF settings"}

        setting_map = {
            "radio_ai": "radio_ai_enabled",
            "roaming_assistant": "roaming_assistant_enabled",
            "band_steering": "band_steering_enabled"
        }

        if setting not in setting_map:
            return {"error": f"Unknown setting: {setting}. Use: radio_ai, roaming_assistant, band_steering"}

        try:
            await unifi_api("rest/setting/super_mgmt", method="PUT",
                           data={setting_map[setting]: enabled}, require_session=True)
            return {"success": True, "message": f"Set {setting} to {enabled}"}
        except Exception as e:
            return {"error": str(e)}
