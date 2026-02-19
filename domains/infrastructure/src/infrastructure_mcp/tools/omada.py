"""TP-Link Omada SDN Controller management tools.

Manages switches, VLANs, port profiles, and LAG groups via the Omada
Software Controller REST API (v2).
"""

import os
import logging
import time
from typing import Optional, List

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
OMADA_URL = os.environ.get("OMADA_URL", "https://10.10.0.3:8043")
OMADA_USERNAME = os.environ.get("OMADA_USERNAME", "admin")
OMADA_PASSWORD = os.environ.get("OMADA_PASSWORD", "")

# Session state (module-level, refreshed on auth failure)
_session = {
    "omadac_id": None,
    "token": None,
    "cookies": {},
    "site_id": None,
    "last_auth": 0,
}

# Read-only fields stripped before PATCH on port objects
_PORT_READONLY_FIELDS = frozenset({
    "portStatus", "portCap", "portSpeedCap", "switchId", "switchMac",
    "site", "maxSpeed", "supportPoe", "supportLocate", "poeDisplayType",
    "bandCtrl",  # contains limitRange which is read-only
})


async def _get_client() -> httpx.AsyncClient:
    """Create an httpx client with TLS verification disabled (self-signed cert)."""
    return httpx.AsyncClient(verify=False, timeout=30.0)


async def _ensure_auth() -> None:
    """Authenticate to the Omada controller if needed. Refreshes every 10 minutes."""
    if _session["token"] and (time.time() - _session["last_auth"]) < 600:
        return

    async with await _get_client() as client:
        # Step 1: Get controller ID
        if not _session["omadac_id"]:
            resp = await client.get(f"{OMADA_URL}/api/info")
            resp.raise_for_status()
            info = resp.json()
            _session["omadac_id"] = info["result"]["omadacId"]

        oid = _session["omadac_id"]

        # Step 2: Login
        resp = await client.post(
            f"{OMADA_URL}/{oid}/api/v2/login",
            json={"username": OMADA_USERNAME, "password": OMADA_PASSWORD},
        )
        resp.raise_for_status()
        result = resp.json()
        if result.get("errorCode") != 0:
            raise RuntimeError(f"Omada login failed: {result.get('msg')}")

        _session["token"] = result["result"]["token"]
        _session["cookies"] = dict(resp.cookies)
        _session["last_auth"] = time.time()

        # Step 3: Discover site ID if not cached
        if not _session["site_id"]:
            sites_resp = await client.get(
                f"{OMADA_URL}/{oid}/api/v2/sites",
                params={"token": _session["token"], "currentPage": 1, "currentPageSize": 10},
                headers={"Csrf-Token": _session["token"]},
                cookies=_session["cookies"],
            )
            sites_resp.raise_for_status()
            sites = sites_resp.json()
            if sites.get("errorCode") == 0 and sites["result"]["data"]:
                _session["site_id"] = sites["result"]["data"][0]["id"]


async def _api(method: str, path: str, data: dict = None, paginate: bool = False) -> dict:
    """Make an authenticated API call to the Omada controller.

    Args:
        method: HTTP method (GET, POST, PATCH, DELETE)
        path: API path relative to site (e.g., 'devices' or 'setting/lan/networks')
        data: Request body for POST/PATCH
        paginate: Add pagination params for list endpoints
    """
    await _ensure_auth()

    oid = _session["omadac_id"]
    site = _session["site_id"]
    token = _session["token"]

    url = f"{OMADA_URL}/{oid}/api/v2/sites/{site}/{path}"
    params = {"token": token}
    if paginate:
        params["currentPage"] = 1
        params["currentPageSize"] = 100

    headers = {"Csrf-Token": token}

    async with await _get_client() as client:
        resp = await client.request(
            method, url,
            params=params,
            headers=headers,
            cookies=_session["cookies"],
            json=data,
        )

        # Handle session expiry -> re-auth and retry once
        if resp.status_code == 302:
            _session["token"] = None
            _session["last_auth"] = 0
            await _ensure_auth()

            params["token"] = _session["token"]
            headers["Csrf-Token"] = _session["token"]
            resp = await client.request(
                method, url,
                params=params,
                headers=headers,
                cookies=_session["cookies"],
                json=data,
            )

        resp.raise_for_status()
        return resp.json()


async def _api_global(method: str, path: str, data: dict = None) -> dict:
    """Make an API call not scoped to a site (e.g., /users/current)."""
    await _ensure_auth()

    oid = _session["omadac_id"]
    token = _session["token"]

    url = f"{OMADA_URL}/{oid}/api/v2/{path}"
    params = {"token": token}
    headers = {"Csrf-Token": token}

    async with await _get_client() as client:
        resp = await client.request(
            method, url,
            params=params,
            headers=headers,
            cookies=_session["cookies"],
            json=data,
        )
        resp.raise_for_status()
        return resp.json()


async def get_status() -> dict:
    """Get Omada controller status for health checks."""
    try:
        await _ensure_auth()
        user = await _api_global("GET", "users/current")
        if user.get("errorCode") == 0:
            return {"status": "healthy", "site": _session.get("site_id")}
        return {"status": "degraded", "error": user.get("msg")}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)[:50]}


def register_tools(mcp: FastMCP):
    """Register Omada controller tools with the MCP server."""

    # =========================================================================
    # Devices
    # =========================================================================

    @mcp.tool()
    async def omada_get_devices() -> List[dict]:
        """List all devices managed by the Omada controller (switches, APs, gateways).

        Returns device type, model, firmware, IP, MAC, and connection status."""
        result = await _api("GET", "devices")
        devices = result.get("result", [])
        return [{
            "type": d.get("type"),
            "mac": d.get("mac"),
            "name": d.get("name"),
            "model": d.get("showModel"),
            "firmwareVersion": d.get("firmwareVersion"),
            "ip": d.get("ip"),
            "status": d.get("status"),
            "statusCategory": d.get("statusCategory"),
        } for d in devices]

    @mcp.tool()
    async def omada_get_switch(mac: str) -> dict:
        """Get detailed info for a specific switch.

        Args:
            mac: Switch MAC address in format 'XX-XX-XX-XX-XX-XX'"""
        result = await _api("GET", f"switches/{mac}")
        return result.get("result", {})

    @mcp.tool()
    async def omada_rename_switch(mac: str, name: str) -> str:
        """Rename a managed switch.

        Args:
            mac: Switch MAC address in format 'XX-XX-XX-XX-XX-XX'
            name: New name for the switch"""
        result = await _api("PATCH", f"switches/{mac}", {"name": name})
        if result.get("errorCode") == 0:
            return f"Renamed switch {mac} to '{name}'"
        return f"Failed: {result.get('msg')}"

    # =========================================================================
    # Ports
    # =========================================================================

    @mcp.tool()
    async def omada_get_switch_ports(mac: str) -> List[dict]:
        """Get all port configurations for a switch.

        Returns port number, name, profile, VLAN settings, link status, and speed.

        Args:
            mac: Switch MAC address in format 'XX-XX-XX-XX-XX-XX'"""
        result = await _api("GET", f"switches/{mac}/ports")
        ports = result.get("result", [])
        return [{
            "port": p.get("port"),
            "name": p.get("name"),
            "type": p.get("type"),
            "profileId": p.get("profileId"),
            "profileName": p.get("profileName"),
            "nativeNetworkId": p.get("nativeNetworkId"),
            "tagNetworkIds": p.get("tagNetworkIds", []),
            "profileOverrideEnable": p.get("profileOverrideEnable"),
            "disable": p.get("disable"),
            "linkStatus": p.get("portStatus", {}).get("linkStatus"),
            "linkSpeed": p.get("portStatus", {}).get("linkSpeed"),
            "tx": p.get("portStatus", {}).get("tx"),
            "rx": p.get("portStatus", {}).get("rx"),
        } for p in ports]

    @mcp.tool()
    async def omada_get_port_detail(mac: str, port: int) -> dict:
        """Get full configuration detail for a specific port.

        Args:
            mac: Switch MAC address in format 'XX-XX-XX-XX-XX-XX'
            port: Port number (1-28)"""
        result = await _api("GET", f"switches/{mac}/ports")
        for p in result.get("result", []):
            if p.get("port") == port:
                return p
        return {"error": f"Port {port} not found"}

    @mcp.tool()
    async def omada_set_port_profile(
        mac: str,
        port: int,
        profile_id: str
    ) -> str:
        """Assign a port profile to a switch port.

        This is the primary way to configure port VLAN behavior. Create profiles
        with omada_create_port_profile first, then assign them to ports.

        Args:
            mac: Switch MAC address in format 'XX-XX-XX-XX-XX-XX'
            port: Port number (1-28)
            profile_id: Profile ID from omada_get_port_profiles"""
        # Get current port config (PATCH requires full object)
        result = await _api("GET", f"switches/{mac}/ports")
        port_obj = None
        for p in result.get("result", []):
            if p.get("port") == port:
                port_obj = dict(p)
                break
        if not port_obj:
            return f"Port {port} not found on switch {mac}"

        # Strip read-only fields
        for field in _PORT_READONLY_FIELDS:
            port_obj.pop(field, None)

        # Update profile
        port_obj["profileId"] = profile_id
        port_obj["profileOverrideEnable"] = False

        patch_result = await _api("PATCH", f"switches/{mac}/ports/{port}", port_obj)
        if patch_result.get("errorCode") == 0:
            return f"Assigned profile {profile_id} to port {port}"
        return f"Failed: {patch_result.get('msg')}"

    @mcp.tool()
    async def omada_set_port_name(mac: str, port: int, name: str) -> str:
        """Rename a switch port.

        Args:
            mac: Switch MAC address in format 'XX-XX-XX-XX-XX-XX'
            port: Port number (1-28)
            name: New name for the port"""
        result = await _api("GET", f"switches/{mac}/ports")
        port_obj = None
        for p in result.get("result", []):
            if p.get("port") == port:
                port_obj = dict(p)
                break
        if not port_obj:
            return f"Port {port} not found"

        for field in _PORT_READONLY_FIELDS:
            port_obj.pop(field, None)

        port_obj["name"] = name

        patch_result = await _api("PATCH", f"switches/{mac}/ports/{port}", port_obj)
        if patch_result.get("errorCode") == 0:
            return f"Renamed port {port} to '{name}'"
        return f"Failed: {patch_result.get('msg')}"

    @mcp.tool()
    async def omada_disable_port(mac: str, port: int, disable: bool = True) -> str:
        """Enable or disable a switch port.

        Args:
            mac: Switch MAC address in format 'XX-XX-XX-XX-XX-XX'
            port: Port number (1-28)
            disable: True to disable, False to enable"""
        result = await _api("GET", f"switches/{mac}/ports")
        port_obj = None
        for p in result.get("result", []):
            if p.get("port") == port:
                port_obj = dict(p)
                break
        if not port_obj:
            return f"Port {port} not found"

        for field in _PORT_READONLY_FIELDS:
            port_obj.pop(field, None)

        port_obj["disable"] = disable

        patch_result = await _api("PATCH", f"switches/{mac}/ports/{port}", port_obj)
        if patch_result.get("errorCode") == 0:
            return f"Port {port} {'disabled' if disable else 'enabled'}"
        return f"Failed: {patch_result.get('msg')}"

    # =========================================================================
    # Networks (VLANs)
    # =========================================================================

    @mcp.tool()
    async def omada_get_networks() -> List[dict]:
        """List all networks (VLANs) configured in Omada.

        Returns network name, VLAN ID, purpose, gateway subnet, and DHCP settings."""
        result = await _api("GET", "setting/lan/networks", paginate=True)
        networks = result.get("result", {}).get("data", [])
        return [{
            "id": n.get("id"),
            "name": n.get("name"),
            "vlan": n.get("vlan"),
            "purpose": n.get("purpose"),
            "gatewaySubnet": n.get("gatewaySubnet"),
            "igmpSnoopEnable": n.get("igmpSnoopEnable"),
            "primary": n.get("primary", False),
        } for n in networks]

    @mcp.tool()
    async def omada_create_network(
        name: str,
        vlan: int,
        purpose: str = "vlan",
        igmp_snoop: bool = False,
    ) -> str:
        """Create a new VLAN network.

        Networks define VLAN IDs that can be assigned to port profiles. DHCP is
        handled externally (OPNsense), so we create VLAN-only networks.

        Args:
            name: Network name (e.g., 'Servers', 'IoT', 'Clients')
            vlan: VLAN ID (1-4094)
            purpose: Network purpose - 'vlan' for VLAN-only (default)
            igmp_snoop: Enable IGMP snooping (for multicast traffic)"""
        result = await _api("POST", "setting/lan/networks", {
            "name": name,
            "purpose": purpose,
            "vlan": vlan,
            "igmpSnoopEnable": igmp_snoop,
            "portal": False,
            "isolation": False,
        })
        if result.get("errorCode") == 0:
            net_id = result.get("result", "")
            return f"Created network '{name}' (VLAN {vlan}, ID: {net_id})"
        return f"Failed: {result.get('msg')}"

    @mcp.tool()
    async def omada_delete_network(network_id: str) -> str:
        """Delete a VLAN network.

        Args:
            network_id: Network ID from omada_get_networks"""
        result = await _api("DELETE", f"setting/lan/networks/{network_id}")
        if result.get("errorCode") == 0:
            return f"Deleted network {network_id}"
        return f"Failed: {result.get('msg')}"

    # =========================================================================
    # Port Profiles
    # =========================================================================

    @mcp.tool()
    async def omada_get_port_profiles() -> List[dict]:
        """List all port profiles.

        Port profiles define VLAN tagging behavior for switch ports. Each profile
        has a native (untagged) network and optional tagged networks.

        Default profiles: 'All' (all VLANs), 'Default' (default VLAN only), 'Disable'."""
        result = await _api("GET", "setting/lan/profiles", paginate=True)
        profiles = result.get("result", {}).get("data", [])
        return [{
            "id": p.get("id"),
            "name": p.get("name"),
            "nativeNetworkId": p.get("nativeNetworkId"),
            "tagNetworkIds": p.get("tagNetworkIds", []),
            "untagNetworkIds": p.get("untagNetworkIds", []),
            "type": p.get("type"),
            "prohibitModify": p.get("prohibitModify", False),
        } for p in profiles]

    @mcp.tool()
    async def omada_create_port_profile(
        name: str,
        native_network_id: str,
        tag_network_ids: List[str] = None,
    ) -> str:
        """Create a new port profile for VLAN assignment.

        Port profiles define how a switch port handles VLAN tagging:
        - Native network: untagged traffic uses this VLAN (PVID)
        - Tagged networks: additional VLANs carried as 802.1Q tagged frames

        Examples:
        - Access port: native=VLAN10, no tagged (server on VLAN 10)
        - Trunk port: native=VLAN10, tagged=[VLAN20, VLAN60, VLAN70] (router uplink)
        - AP trunk: native=VLAN10, tagged=[VLAN60, VLAN70] (UniFi AP)

        Args:
            name: Profile name (e.g., 'Servers-Access', 'Router-Trunk')
            native_network_id: Network ID for untagged/native VLAN (PVID)
            tag_network_ids: List of network IDs for tagged VLANs (optional)"""
        profile_data = {
            "name": name,
            "nativeNetworkId": native_network_id,
            "tagNetworkIds": tag_network_ids or [],
            "untagNetworkIds": [],
            "poe": 2,
            "dot1x": 2,
            "portIsolationEnable": False,
            "lldpMedEnable": True,
            "topoNotifyEnable": False,
            "bandWidthCtrlType": 0,
            "spanningTreeEnable": True,
            "spanningTreeSetting": {
                "priority": 128,
                "extPathCost": 0,
                "intPathCost": 0,
                "edgePort": False,
                "p2pLink": 0,
                "mcheck": False,
                "loopProtect": False,
                "rootProtect": False,
                "tcGuard": False,
                "bpduProtect": False,
                "bpduFilter": False,
                "bpduForward": True,
            },
            "loopbackDetectEnable": False,
            "eeeEnable": False,
            "flowControlEnable": False,
        }

        result = await _api("POST", "setting/lan/profiles", profile_data)
        if result.get("errorCode") == 0:
            profile_id = result.get("result", {})
            if isinstance(profile_id, dict):
                profile_id = profile_id.get("id", "")
            return f"Created profile '{name}' (ID: {profile_id})"
        return f"Failed: {result.get('msg')}"

    @mcp.tool()
    async def omada_delete_port_profile(profile_id: str) -> str:
        """Delete a port profile.

        Cannot delete built-in profiles (All, Default, Disable).

        Args:
            profile_id: Profile ID from omada_get_port_profiles"""
        result = await _api("DELETE", f"setting/lan/profiles/{profile_id}")
        if result.get("errorCode") == 0:
            return f"Deleted profile {profile_id}"
        return f"Failed: {result.get('msg')}"

    # =========================================================================
    # LAG Groups
    # =========================================================================

    @mcp.tool()
    async def omada_get_lags(mac: str) -> List[dict]:
        """List LAG (Link Aggregation) groups on a switch.

        Args:
            mac: Switch MAC address in format 'XX-XX-XX-XX-XX-XX'"""
        result = await _api("GET", f"switches/{mac}/lags")
        return result.get("result", [])

    @mcp.tool()
    async def omada_create_lag(
        mac: str,
        master_port: int,
        member_ports: List[int],
        lag_id: int,
        lag_type: int = 2,
        profile_id: Optional[str] = None,
    ) -> str:
        """Create a LAG (Link Aggregation Group) on a switch.

        Configures the master port to aggregating mode and adds member ports
        to form a bonded link. All member ports must be the same speed.

        Args:
            mac: Switch MAC in 'XX-XX-XX-XX-XX-XX' format
            master_port: Port number that initiates the LAG (lowest port recommended)
            member_ports: Other port numbers to add to the LAG (NOT including master_port)
            lag_id: LAG group ID (1-8 on TL-SG3428X-M2)
            lag_type: 1=Static LAG, 2=LACP (default: 2)
            profile_id: Optional port profile ID to apply (uses current if omitted)
        """
        if lag_id < 1 or lag_id > 8:
            return "Failed: lagId must be 1-8"
        if lag_type not in (1, 2):
            return "Failed: lagType must be 1 (Static) or 2 (LACP)"
        if not member_ports:
            return "Failed: at least one member port required"
        if master_port in member_ports:
            return "Failed: master_port must not be in member_ports list"

        # Get current port state for required fields
        ports_result = await _api("GET", f"switches/{mac}/ports")
        port_obj = None
        for p in ports_result.get("result", []):
            if p.get("port") == master_port:
                port_obj = p
                break
        if not port_obj:
            return f"Failed: port {master_port} not found"

        payload = {
            "name": port_obj["name"],
            "profileId": profile_id or port_obj["profileId"],
            "profileOverrideEnable": True,
            "dot1pPriority": port_obj.get("dot1pPriority", 0),
            "trustMode": port_obj.get("trustMode", 0),
            "operation": "aggregating",
            "topoNotifyEnable": port_obj.get("topoNotifyEnable", False),
            "lagSetting": {
                "lagId": lag_id,
                "lagType": lag_type,
                "ports": [master_port] + member_ports,
            },
        }

        result = await _api("PATCH", f"switches/{mac}/ports/{master_port}", payload)
        if result.get("errorCode") == 0:
            mode = "LACP" if lag_type == 2 else "Static"
            all_ports = [master_port] + member_ports
            return f"Created LAG{lag_id} ({mode}) with ports {all_ports}"
        return f"Failed: {result.get('msg')}"

    @mcp.tool()
    async def omada_delete_lag(mac: str, lag_id: int) -> str:
        """Delete a LAG group, returning member ports to normal switching mode.

        Args:
            mac: Switch MAC in 'XX-XX-XX-XX-XX-XX' format
            lag_id: LAG group ID to delete (1-14)
        """
        result = await _api("DELETE", f"switches/{mac}/lags/{lag_id}")
        if result.get("errorCode") == 0:
            return f"Deleted LAG{lag_id}"
        return f"Failed: {result.get('msg')}"

    # =========================================================================
    # Site Info
    # =========================================================================

    @mcp.tool()
    async def omada_get_site_info() -> dict:
        """Get information about the Omada site including device counts."""
        result = await _api_global("GET", f"sites?currentPage=1&currentPageSize=10")
        sites = result.get("result", {}).get("data", [])
        if sites:
            s = sites[0]
            return {
                "id": s.get("id"),
                "name": s.get("name"),
                "lanDeviceConnected": s.get("lanDeviceConnectedNum", 0),
                "lanDeviceDisconnected": s.get("lanDeviceDisconnectedNum", 0),
                "wlanDeviceConnected": s.get("wlanDeviceConnectedNum", 0),
            }
        return {"error": "No sites found"}

    @mcp.tool()
    async def omada_get_controller_info() -> dict:
        """Get Omada controller version and configuration status."""
        async with await _get_client() as client:
            resp = await client.get(f"{OMADA_URL}/api/info")
            resp.raise_for_status()
            info = resp.json()
            return info.get("result", {})
