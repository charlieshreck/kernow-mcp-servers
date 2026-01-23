"""OPNsense firewall, AdGuard Home, Unbound DNS, and Caddy management tools."""

import os
import logging
from typing import Optional, List

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
# NOTE: OPNsense requires SNI (Server Name Indication) - must use hostname, not IP
OPNSENSE_URL = os.environ.get("OPNSENSE_URL", "https://opnsense.kernow.io")
OPNSENSE_API_KEY = os.environ.get("OPNSENSE_API_KEY", "")
OPNSENSE_API_SECRET = os.environ.get("OPNSENSE_API_SECRET", "")

ADGUARD_URL = os.environ.get("ADGUARD_URL", "http://10.10.0.1:3000")
ADGUARD_USERNAME = os.environ.get("ADGUARD_USERNAME", "admin")
ADGUARD_PASSWORD = os.environ.get("ADGUARD_PASSWORD", "")


async def opnsense_api(endpoint: str, method: str = "GET", data: dict = None) -> dict:
    """Make authenticated API call to OPNsense.

    Note: OPNsense search endpoints require POST with pagination params.
    """
    auth = (OPNSENSE_API_KEY, OPNSENSE_API_SECRET)
    url = f"{OPNSENSE_URL}/api{endpoint}"

    async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
        if method == "GET":
            resp = await client.get(url, auth=auth)
        elif method == "POST":
            resp = await client.post(url, auth=auth, json=data or {})
        else:
            raise ValueError(f"Unsupported method: {method}")

        resp.raise_for_status()
        return resp.json()


async def adguard_api(endpoint: str, method: str = "GET", data: dict = None) -> dict:
    """Make authenticated API call to AdGuard Home."""
    auth = (ADGUARD_USERNAME, ADGUARD_PASSWORD)
    url = f"{ADGUARD_URL}{endpoint}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        if method == "GET":
            resp = await client.get(url, auth=auth)
        elif method == "POST":
            resp = await client.post(url, auth=auth, json=data)
        else:
            raise ValueError(f"Unsupported method: {method}")

        resp.raise_for_status()
        return resp.json() if resp.text else {}


async def get_status() -> dict:
    """Get OPNsense/AdGuard status for health checks."""
    statuses = {}

    try:
        await opnsense_api("/core/system/status")
        statuses["opnsense"] = "healthy"
    except Exception as e:
        statuses["opnsense"] = f"unhealthy: {str(e)[:30]}"

    try:
        await adguard_api("/control/status")
        statuses["adguard"] = "healthy"
    except Exception as e:
        statuses["adguard"] = f"unhealthy: {str(e)[:30]}"

    all_healthy = all("healthy" in str(s) for s in statuses.values())
    return {"status": "healthy" if all_healthy else "degraded", "components": statuses}


def register_tools(mcp: FastMCP):
    """Register OPNsense tools with the MCP server."""

    # =========================================================================
    # OPNsense Core
    # =========================================================================

    @mcp.tool()
    async def get_interfaces() -> List[dict]:
        """List all network interfaces with traffic statistics."""
        result = await opnsense_api("/interfaces/overview/export")
        # This endpoint returns a list directly, not wrapped in 'rows'
        return result if isinstance(result, list) else result.get("rows", [])

    @mcp.tool()
    async def get_firewall_rules() -> List[dict]:
        """List firewall filter rules."""
        # OPNsense search endpoints require POST with pagination
        result = await opnsense_api("/firewall/filter/searchRule", method="POST", data={"current": 1, "rowCount": 500})
        return result.get("rows", [])

    @mcp.tool()
    async def get_firewall_aliases() -> List[dict]:
        """List firewall aliases (IP lists, port groups, etc.)."""
        result = await opnsense_api("/firewall/alias/searchItem", method="POST", data={"current": 1, "rowCount": 200})
        return result.get("rows", [])

    @mcp.tool()
    async def get_dhcp_leases() -> List[dict]:
        """List active DHCP leases."""
        result = await opnsense_api("/kea/leases4/search", method="POST", data={"current": 1, "rowCount": 500})
        return result.get("rows", [])

    @mcp.tool()
    async def get_gateway_status() -> List[dict]:
        """Get gateway status and latency."""
        result = await opnsense_api("/routes/gateway/status")
        return result.get("items", [])

    @mcp.tool()
    async def get_system_status() -> dict:
        """Get overall OPNsense system status."""
        return await opnsense_api("/core/system/status")

    @mcp.tool()
    async def get_services() -> List[dict]:
        """List all services and their running status."""
        result = await opnsense_api("/core/service/search", method="POST", data={"current": 1, "rowCount": 100})
        return result.get("rows", [])

    # =========================================================================
    # Service Control
    # =========================================================================

    @mcp.tool()
    async def start_service(service_id: str) -> str:
        """Start a service by ID.

        Args:
            service_id: Service ID (e.g., 'unbound', 'caddy', 'AdGuardHome')"""
        result = await opnsense_api(f"/core/service/start/{service_id}", method="POST")
        return f"Started {service_id}: {result.get('result', 'unknown')}"

    @mcp.tool()
    async def stop_service(service_id: str) -> str:
        """Stop a service by ID.

        Args:
            service_id: Service ID (e.g., 'unbound', 'caddy', 'AdGuardHome')"""
        result = await opnsense_api(f"/core/service/stop/{service_id}", method="POST")
        return f"Stopped {service_id}: {result.get('result', 'unknown')}"

    @mcp.tool()
    async def restart_service(service_id: str) -> str:
        """Restart a service by ID.

        Args:
            service_id: Service ID (e.g., 'unbound', 'caddy', 'AdGuardHome')"""
        result = await opnsense_api(f"/core/service/restart/{service_id}", method="POST")
        return f"Restarted {service_id}: {result.get('result', 'unknown')}"

    # =========================================================================
    # Firewall Alias Management
    # =========================================================================

    @mcp.tool()
    async def add_firewall_alias(
        name: str,
        alias_type: str,
        content: str,
        description: str = ""
    ) -> str:
        """Add a new firewall alias.

        Args:
            name: Alias name (letters, numbers, underscores only)
            alias_type: Type - 'host' (IPs), 'network' (CIDRs), 'port' (ports/ranges)
            content: Comma-separated values (e.g., '10.0.0.1,10.0.0.2' or '80,443,8080-8090')
            description: Optional description"""
        data = {
            "alias": {
                "enabled": "1",
                "name": name,
                "type": alias_type,
                "content": content,
                "description": description
            }
        }
        result = await opnsense_api("/firewall/alias/addItem", method="POST", data=data)
        # Apply changes
        await opnsense_api("/firewall/alias/reconfigure", method="POST")
        return f"Added alias '{name}' (UUID: {result.get('uuid', 'unknown')})"

    @mcp.tool()
    async def update_firewall_alias(
        uuid: str,
        content: str = None,
        description: str = None,
        enabled: bool = None
    ) -> str:
        """Update an existing firewall alias.

        Args:
            uuid: UUID of the alias to update
            content: New comma-separated values (optional)
            description: New description (optional)
            enabled: Enable/disable the alias (optional)"""
        data = {"alias": {}}
        if content is not None:
            data["alias"]["content"] = content
        if description is not None:
            data["alias"]["description"] = description
        if enabled is not None:
            data["alias"]["enabled"] = "1" if enabled else "0"

        await opnsense_api(f"/firewall/alias/setItem/{uuid}", method="POST", data=data)
        await opnsense_api("/firewall/alias/reconfigure", method="POST")
        return f"Updated alias {uuid}"

    @mcp.tool()
    async def delete_firewall_alias(uuid: str) -> str:
        """Delete a firewall alias by UUID.

        Args:
            uuid: UUID of the alias to delete"""
        await opnsense_api(f"/firewall/alias/delItem/{uuid}", method="POST")
        await opnsense_api("/firewall/alias/reconfigure", method="POST")
        return f"Deleted alias {uuid}"

    # =========================================================================
    # AdGuard Home
    # =========================================================================

    @mcp.tool()
    async def get_adguard_stats() -> dict:
        """Get AdGuard Home statistics including query counts, blocked queries, and response times."""
        return await adguard_api("/control/stats")

    @mcp.tool()
    async def get_adguard_status() -> dict:
        """Get AdGuard Home protection status and version info."""
        return await adguard_api("/control/status")

    @mcp.tool()
    async def get_adguard_dns_config() -> dict:
        """Get AdGuard Home DNS configuration including upstream servers, cache settings, and rate limits."""
        return await adguard_api("/control/dns_info")

    @mcp.tool()
    async def get_adguard_query_log(limit: int = 100, search: str = "") -> dict:
        """Get recent DNS query log from AdGuard Home.

        Args:
            limit: Maximum number of entries to return (default 100)
            search: Optional search string to filter queries"""
        params = f"?limit={limit}"
        if search:
            params += f"&search={search}"
        return await adguard_api(f"/control/querylog{params}")

    @mcp.tool()
    async def get_adguard_top_clients(count: int = 10) -> dict:
        """Get top DNS clients by query count.

        Args:
            count: Number of top clients to return (default 10)"""
        stats = await adguard_api("/control/stats")
        return stats.get("top_clients", [])[:count]

    @mcp.tool()
    async def get_adguard_filters() -> List[dict]:
        """Get list of active DNS filter/blocklists in AdGuard Home."""
        result = await adguard_api("/control/filtering/status")
        return result.get("filters", [])

    @mcp.tool()
    async def set_adguard_protection(enabled: bool) -> str:
        """Enable or disable AdGuard Home DNS protection.

        Args:
            enabled: True to enable protection, False to disable"""
        await adguard_api("/control/dns_config", method="POST", data={"protection_enabled": enabled})
        return f"AdGuard protection {'enabled' if enabled else 'disabled'}"

    @mcp.tool()
    async def get_adguard_safebrowsing_status() -> dict:
        """Get AdGuard Home safe browsing and parental control status."""
        return await adguard_api("/control/safebrowsing/status")

    @mcp.tool()
    async def get_adguard_blocked_services() -> List[str]:
        """Get list of blocked services (e.g., TikTok, Facebook) in AdGuard Home."""
        result = await adguard_api("/control/blocked_services/list")
        return result if isinstance(result, list) else []

    @mcp.tool()
    async def get_adguard_rewrites() -> List[dict]:
        """Get DNS rewrites/custom rules in AdGuard Home."""
        return await adguard_api("/control/rewrite/list")

    @mcp.tool()
    async def add_adguard_rewrite(domain: str, answer: str) -> str:
        """Add a DNS rewrite rule in AdGuard Home.

        Args:
            domain: Domain to rewrite (e.g., 'example.com' or '*.example.com')
            answer: Target IP address or hostname to resolve to"""
        await adguard_api("/control/rewrite/add", method="POST", data={"domain": domain, "answer": answer})
        return f"Added rewrite: {domain} → {answer}"

    @mcp.tool()
    async def delete_adguard_rewrite(domain: str, answer: str) -> str:
        """Delete a DNS rewrite rule from AdGuard Home.

        Args:
            domain: Domain of the rewrite to delete
            answer: Target answer of the rewrite to delete"""
        await adguard_api("/control/rewrite/delete", method="POST", data={"domain": domain, "answer": answer})
        return f"Deleted rewrite: {domain} → {answer}"

    # =========================================================================
    # Unbound DNS
    # =========================================================================

    @mcp.tool()
    async def get_unbound_stats() -> dict:
        """Get Unbound DNS resolver statistics."""
        return await opnsense_api("/unbound/diagnostics/stats")

    @mcp.tool()
    async def get_unbound_overrides() -> List[dict]:
        """Get Unbound DNS host overrides (local DNS entries)."""
        result = await opnsense_api("/unbound/settings/searchHostOverride", method="POST", data={"current": 1, "rowCount": 200})
        return result.get("rows", [])

    @mcp.tool()
    async def get_unbound_config() -> dict:
        """Get Unbound DNS general configuration."""
        return await opnsense_api("/unbound/settings/get")

    @mcp.tool()
    async def flush_unbound_cache() -> str:
        """Flush the Unbound DNS cache."""
        await opnsense_api("/unbound/service/flushCache", method="POST")
        return "Unbound cache flushed"

    @mcp.tool()
    async def add_unbound_override(
        hostname: str,
        domain: str,
        server: str,
        description: str = ""
    ) -> str:
        """Add a new DNS host override in Unbound.

        Args:
            hostname: Hostname (e.g., 'www' or '*' for wildcard)
            domain: Domain (e.g., 'example.com')
            server: IP address to resolve to
            description: Optional description"""
        data = {
            "host": {"hostname": hostname, "domain": domain, "server": server, "description": description}
        }
        result = await opnsense_api("/unbound/settings/addHostOverride", method="POST", data=data)
        # Apply changes
        await opnsense_api("/unbound/service/reconfigure", method="POST")
        return f"Added override: {hostname}.{domain} → {server}"

    @mcp.tool()
    async def update_unbound_override(
        uuid: str,
        description: str = None,
        server: str = None,
        enabled: bool = None
    ) -> str:
        """Update an existing DNS host override in Unbound.

        Args:
            uuid: UUID of the override to update
            description: New description (optional)
            server: New IP address (optional)
            enabled: Enable/disable the override (optional)"""
        data = {"host": {}}
        if description is not None:
            data["host"]["description"] = description
        if server is not None:
            data["host"]["server"] = server
        if enabled is not None:
            data["host"]["enabled"] = "1" if enabled else "0"

        await opnsense_api(f"/unbound/settings/setHostOverride/{uuid}", method="POST", data=data)
        await opnsense_api("/unbound/service/reconfigure", method="POST")
        return f"Updated override {uuid}"

    @mcp.tool()
    async def delete_unbound_override(uuid: str) -> str:
        """Delete a DNS host override from Unbound.

        Args:
            uuid: UUID of the override to delete"""
        await opnsense_api(f"/unbound/settings/delHostOverride/{uuid}", method="POST")
        await opnsense_api("/unbound/service/reconfigure", method="POST")
        return f"Deleted override {uuid}"

    # =========================================================================
    # Caddy Reverse Proxy
    # =========================================================================

    @mcp.tool()
    async def list_caddy_reverse_proxies() -> List[dict]:
        """List all Caddy reverse proxy domain entries."""
        result = await opnsense_api("/caddy/ReverseProxy/searchReverseProxy", method="POST", data={"current": 1, "rowCount": 200})
        return result.get("rows", [])

    @mcp.tool()
    async def list_caddy_handles() -> List[dict]:
        """List all Caddy backend handlers."""
        result = await opnsense_api("/caddy/ReverseProxy/searchHandle", method="POST", data={"current": 1, "rowCount": 200})
        return result.get("rows", [])

    @mcp.tool()
    async def add_caddy_reverse_proxy(
        domain: str,
        description: str = "",
        dns_challenge: bool = True
    ) -> str:
        """Add a new Caddy reverse proxy domain entry.

        Args:
            domain: Domain name (e.g., 'app.kernow.io')
            description: Optional description
            dns_challenge: Use DNS challenge for TLS (default True)

        Returns:
            UUID of created entry (use with add_caddy_handle)"""
        data = {
            "reverseproxy": {
                "enabled": "1",
                "FromDomain": domain,
                "FromPort": "443",
                "description": description,
                "DnsChallenge": "1" if dns_challenge else "0",
                "AcmePassthrough": "0"
            }
        }
        result = await opnsense_api("/caddy/ReverseProxy/addReverseProxy", method="POST", data=data)
        uuid = result.get("uuid", "")
        return f"Created reverse proxy for {domain} (UUID: {uuid})"

    @mcp.tool()
    async def add_caddy_handle(
        reverse_uuid: str,
        backend_host: str,
        backend_port: int,
        description: str = "Backend"
    ) -> str:
        """Add a backend handler to a Caddy reverse proxy entry.

        Args:
            reverse_uuid: UUID from add_caddy_reverse_proxy
            backend_host: Backend IP or hostname (e.g., '10.20.0.40')
            backend_port: Backend port (e.g., 31095)
            description: Optional description"""
        data = {
            "handle": {
                "enabled": "1",
                "reverse": reverse_uuid,
                "HandleType": "handle",
                "HandlePath": "",
                "ToDomain": backend_host,
                "ToPort": str(backend_port),
                "HttpTls": "0",
                "description": description
            }
        }
        result = await opnsense_api("/caddy/ReverseProxy/addHandle", method="POST", data=data)
        return f"Added handler → {backend_host}:{backend_port}"

    @mcp.tool()
    async def delete_caddy_reverse_proxy(uuid: str) -> str:
        """Delete a Caddy reverse proxy entry by UUID.

        Args:
            uuid: UUID of the reverse proxy entry to delete"""
        await opnsense_api(f"/caddy/ReverseProxy/delReverseProxy/{uuid}", method="POST")
        return f"Deleted reverse proxy {uuid}"

    @mcp.tool()
    async def delete_caddy_handle(uuid: str) -> str:
        """Delete a Caddy backend handler by UUID.

        Args:
            uuid: UUID of the handle to delete"""
        await opnsense_api(f"/caddy/ReverseProxy/delHandle/{uuid}", method="POST")
        return f"Deleted handle {uuid}"

    @mcp.tool()
    async def reconfigure_caddy() -> str:
        """Apply pending Caddy configuration changes."""
        await opnsense_api("/caddy/service/reconfigure", method="POST")
        return "Caddy reconfigured"

    # =========================================================================
    # Combined DNS Summary
    # =========================================================================

    @mcp.tool()
    async def get_dns_summary() -> dict:
        """Get a combined summary of both AdGuard Home and Unbound DNS status."""
        adguard_stats = await adguard_api("/control/stats")
        adguard_status = await adguard_api("/control/status")

        try:
            unbound_stats = await opnsense_api("/unbound/diagnostics/stats")
        except Exception:
            unbound_stats = {}

        return {
            "adguard": {
                "enabled": adguard_status.get("protection_enabled"),
                "total_queries": adguard_stats.get("num_dns_queries", 0),
                "blocked_queries": adguard_stats.get("num_blocked_filtering", 0),
                "block_rate": f"{adguard_stats.get('num_blocked_filtering', 0) / max(adguard_stats.get('num_dns_queries', 1), 1) * 100:.1f}%"
            },
            "unbound": unbound_stats
        }
