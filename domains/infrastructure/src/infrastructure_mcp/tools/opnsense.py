"""OPNsense firewall, AdGuard Home, Unbound DNS, and Caddy management tools."""

import os
import logging
from typing import Optional, List

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
# NOTE: OPNsense requires SNI (Server Name Indication). When connecting from
# environments that can't resolve the internal DNS, use IP with SNI hostname.
OPNSENSE_HOST = os.environ.get("OPNSENSE_HOST", "10.10.0.1")
OPNSENSE_SNI_HOSTNAME = os.environ.get("OPNSENSE_SNI_HOSTNAME", "opnsense.kernow.io")
OPNSENSE_API_KEY = os.environ.get("OPNSENSE_API_KEY", "")
OPNSENSE_API_SECRET = os.environ.get("OPNSENSE_API_SECRET", "")

ADGUARD_URL = os.environ.get("ADGUARD_URL", "http://10.10.0.1:3000")
ADGUARD_USERNAME = os.environ.get("ADGUARD_USERNAME", "admin")
ADGUARD_PASSWORD = os.environ.get("ADGUARD_PASSWORD", "")


async def opnsense_api(endpoint: str, method: str = "GET", data: dict = None) -> dict:
    """Make authenticated API call to OPNsense.

    Note: OPNsense search endpoints require POST with pagination params.
    Uses IP address with SNI hostname header to work around split-DNS issues.
    """
    import ssl

    auth = (OPNSENSE_API_KEY, OPNSENSE_API_SECRET)
    # Connect to IP but use hostname for SNI and Host header
    url = f"https://{OPNSENSE_HOST}/api{endpoint}"

    # Create SSL context with SNI hostname
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    async with httpx.AsyncClient(
        verify=ssl_context,
        timeout=30.0,
        headers={"Host": OPNSENSE_SNI_HOSTNAME}
    ) as client:
        # httpx will use the Host header for SNI when connecting
        if method == "GET":
            resp = await client.get(url, auth=auth, extensions={"sni_hostname": OPNSENSE_SNI_HOSTNAME})
        elif method == "POST":
            resp = await client.post(url, auth=auth, json=data or {}, extensions={"sni_hostname": OPNSENSE_SNI_HOSTNAME})
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
        # OPNsense has rules in multiple places - try automation filter first, then legacy
        rules = []

        # Try automation filter rules (os-firewall plugin)
        try:
            result = await opnsense_api("/firewall/filter/searchRule", method="POST", data={"current": 1, "rowCount": 500})
            if result.get("rows"):
                rules.extend([{**r, "source": "automation"} for r in result.get("rows", [])])
        except Exception:
            pass

        # Get floating rules if available
        try:
            result = await opnsense_api("/firewall/filter/searchFloatingRule", method="POST", data={"current": 1, "rowCount": 500})
            if result.get("rows"):
                rules.extend([{**r, "source": "floating"} for r in result.get("rows", [])])
        except Exception:
            pass

        # If no rules found, note that config may be in legacy XML config
        if not rules:
            return [{"note": "No automation rules found. Firewall rules may be in legacy config (System > Firewall > Rules)."}]

        return rules

    @mcp.tool()
    async def get_firewall_aliases() -> List[dict]:
        """List firewall aliases (IP lists, port groups, etc.)."""
        result = await opnsense_api("/firewall/alias/searchItem", method="POST", data={"current": 1, "rowCount": 200})
        return result.get("rows", [])

    # =========================================================================
    # Firewall Rule Management (Automation API)
    # =========================================================================

    @mcp.tool()
    async def add_firewall_rule(
        interface: str,
        direction: str = "in",
        action: str = "pass",
        protocol: str = "any",
        source_net: str = "any",
        destination_net: str = "any",
        destination_port: str = "",
        description: str = "",
        enabled: bool = True
    ) -> str:
        """Add a firewall rule via the automation API.

        Args:
            interface: Interface name (e.g., 'opt2' for Production, 'opt1' for Agentic, 'opt3' for Monit)
            direction: Traffic direction - 'in' or 'out'
            action: Rule action - 'pass', 'block', or 'reject'
            protocol: Protocol - 'any', 'TCP', 'UDP', 'TCP/UDP', 'ICMP', etc.
            source_net: Source network/IP (e.g., '10.10.0.0/24', 'any', or alias name)
            destination_net: Destination network/IP (e.g., '10.20.0.0/24', 'any', or alias name)
            destination_port: Destination port(s) (e.g., '80', '443', '80,443', '8000-9000')
            description: Rule description
            enabled: Whether the rule is enabled

        Note: Rules are added to the automation filter, which works alongside legacy GUI rules.
        Use apply_firewall_rules() after adding rules to activate them."""
        data = {
            "rule": {
                "enabled": "1" if enabled else "0",
                "sequence": "1",
                "action": action,
                "quick": "1",
                "interface": interface,
                "direction": direction,
                "ipprotocol": "inet",
                "protocol": protocol,
                "source_net": source_net,
                "source_not": "0",
                "destination_net": destination_net,
                "destination_not": "0",
                "description": description
            }
        }
        if destination_port:
            data["rule"]["destination_port"] = destination_port

        result = await opnsense_api("/firewall/filter/addRule", method="POST", data=data)
        uuid = result.get("uuid", "unknown")
        return f"Added firewall rule (UUID: {uuid}). Run apply_firewall_rules() to activate."

    @mcp.tool()
    async def delete_firewall_rule(uuid: str) -> str:
        """Delete a firewall rule by UUID.

        Args:
            uuid: UUID of the rule to delete (from get_firewall_rules output)"""
        await opnsense_api(f"/firewall/filter/delRule/{uuid}", method="POST")
        return f"Deleted rule {uuid}. Run apply_firewall_rules() to activate changes."

    @mcp.tool()
    async def toggle_firewall_rule(uuid: str, enabled: bool) -> str:
        """Enable or disable a firewall rule.

        Args:
            uuid: UUID of the rule
            enabled: True to enable, False to disable"""
        endpoint = "toggleRule" if enabled else "toggleRule"
        data = {"rule": {"enabled": "1" if enabled else "0"}}
        await opnsense_api(f"/firewall/filter/setRule/{uuid}", method="POST", data=data)
        return f"Rule {uuid} {'enabled' if enabled else 'disabled'}. Run apply_firewall_rules() to activate."

    @mcp.tool()
    async def apply_firewall_rules() -> str:
        """Apply pending firewall rule changes.

        Call this after adding, modifying, or deleting firewall rules."""
        result = await opnsense_api("/firewall/filter/apply", method="POST")
        status = result.get("status", "unknown")
        return f"Firewall rules applied: {status}"

    @mcp.tool()
    async def get_nat_rules() -> List[dict]:
        """List NAT port forwarding rules."""
        try:
            result = await opnsense_api("/firewall/source_nat/searchRule", method="POST", data={"current": 1, "rowCount": 200})
            return result.get("rows", [])
        except Exception:
            return [{"note": "NAT rules API not available or empty"}]

    @mcp.tool()
    async def get_dhcp_leases() -> List[dict]:
        """List active DHCP leases."""
        # Try ISC DHCPd first (most common), then KEA
        try:
            result = await opnsense_api("/dhcpv4/leases/searchLease", method="POST", data={"current": 1, "rowCount": 500})
            if result.get("rows"):
                return result.get("rows", [])
        except Exception:
            pass

        # Fallback to KEA DHCP
        try:
            result = await opnsense_api("/kea/leases4/search", method="POST", data={"current": 1, "rowCount": 500})
            return result.get("rows", [])
        except Exception:
            return []

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
            "reverse": {
                "enabled": "1",
                "FromDomain": domain,
                "FromPort": "",
                "accesslist": "",
                "basicauth": "",
                "description": description,
                "DnsChallenge": "1" if dns_challenge else "0",
                "DnsChallengeOverrideDomain": "",
                "CustomCertificate": "",
                "AccessLog": "0",
                "DynDns": "0",
                "AcmePassthrough": "",
                "DisableTls": "0",
                "ClientAuthMode": "",
                "ClientAuthTrustPool": ""
            }
        }
        result = await opnsense_api("/caddy/ReverseProxy/addReverseProxy", method="POST", data=data)
        uuid = result.get("uuid", "")
        if not uuid and result.get("result") == "failed":
            validations = result.get("validations", {})
            return f"Failed to create reverse proxy for {domain}: {validations or 'unknown error'}"
        return f"Created reverse proxy for {domain} (UUID: {uuid})"

    @mcp.tool()
    async def add_caddy_handle(
        reverse_uuid: str,
        backend_host: str,
        backend_port: int,
        description: str = "Backend",
        https_backend: bool = False,
        skip_tls_verify: bool = False
    ) -> str:
        """Add a backend handler to a Caddy reverse proxy entry.

        Args:
            reverse_uuid: UUID from add_caddy_reverse_proxy
            backend_host: Backend IP or hostname (e.g., '10.20.0.40')
            backend_port: Backend port (e.g., 31095)
            description: Optional description
            https_backend: Whether backend uses HTTPS (default False)
            skip_tls_verify: Skip TLS verification for backend (default False)"""
        data = {
            "handle": {
                "enabled": "1",
                "reverse": reverse_uuid,
                "subdomain": "",
                "HandleType": "handle",
                "HandlePath": "",
                "HandleDirective": "reverse_proxy",
                "ToDomain": backend_host,
                "ToPort": str(backend_port),
                "ToPath": "",
                "ForwardAuth": "0",
                "HttpTls": "1" if https_backend else "0",
                "HttpVersion": "",
                "HttpKeepalive": "",
                "HttpNtlm": "0",
                "HttpTlsInsecureSkipVerify": "1" if skip_tls_verify else "0",
                "HttpTlsTrustedCaCerts": "",
                "HttpTlsServerName": "",
                "description": description
            }
        }
        result = await opnsense_api("/caddy/ReverseProxy/addHandle", method="POST", data=data)
        uuid = result.get("uuid", "")
        if not uuid and result.get("result") == "failed":
            validations = result.get("validations", {})
            return f"Failed to add handler: {validations or 'unknown error'}"
        return f"Added handler → {backend_host}:{backend_port} (UUID: {uuid})"

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

    # =========================================================================
    # Firmware & Repository Management
    # =========================================================================

    @mcp.tool()
    async def get_firmware_config() -> dict:
        """Get firmware/repository configuration including mirrors and update settings."""
        # Try multiple endpoints as API varies by version
        try:
            return await opnsense_api("/core/firmware/getfirmwareconfig")
        except Exception:
            pass
        try:
            return await opnsense_api("/core/firmware/getOptions")
        except Exception:
            pass
        # Fallback to status which has some config info
        return await opnsense_api("/core/firmware/status")

    @mcp.tool()
    async def get_firmware_options() -> dict:
        """Get available firmware mirrors, flavours, and repository options."""
        return await opnsense_api("/core/firmware/getOptions")

    @mcp.tool()
    async def get_pkg_audit() -> dict:
        """Get package security audit information."""
        return await opnsense_api("/core/firmware/audit")

    @mcp.tool()
    async def get_firmware_status() -> dict:
        """Get current firmware version and update status."""
        return await opnsense_api("/core/firmware/status")

    @mcp.tool()
    async def get_firmware_info() -> dict:
        """Get detailed firmware info including installed packages and available updates."""
        return await opnsense_api("/core/firmware/info")

    @mcp.tool()
    async def check_firmware_updates() -> dict:
        """Check for available firmware and package updates."""
        # Trigger update check
        await opnsense_api("/core/firmware/check", method="POST")
        # Return status
        return await opnsense_api("/core/firmware/status")

    @mcp.tool()
    async def set_firmware_mirror(mirror: str) -> str:
        """Set the firmware download mirror URL.

        Args:
            mirror: Mirror URL (e.g., 'https://mirror.ams1.nl.leaseweb.net/opnsense')"""
        data = {"firmware": {"mirror": mirror}}
        await opnsense_api("/core/firmware/setfirmwareconfig", method="POST", data=data)
        return f"Firmware mirror set to: {mirror}"

    @mcp.tool()
    async def set_firmware_flavour(flavour: str) -> str:
        """Set the firmware flavour/type.

        Args:
            flavour: Firmware type - 'OpenSSL' (default), 'LibreSSL', or empty for default"""
        data = {"firmware": {"flavour": flavour}}
        await opnsense_api("/core/firmware/setfirmwareconfig", method="POST", data=data)
        return f"Firmware flavour set to: {flavour}"

    @mcp.tool()
    async def set_firmware_subscription(subscription: str = "") -> str:
        """Set OPNsense business subscription key (optional).

        Args:
            subscription: Subscription key, or empty to disable"""
        data = {"firmware": {"subscription": subscription}}
        await opnsense_api("/core/firmware/setfirmwareconfig", method="POST", data=data)
        return "Subscription key updated" if subscription else "Subscription disabled"

    @mcp.tool()
    async def run_firmware_update() -> str:
        """Run firmware update. CAUTION: This will update the system and may require reboot."""
        result = await opnsense_api("/core/firmware/update", method="POST")
        return f"Firmware update initiated: {result}"

    # =========================================================================
    # Plugin Management
    # =========================================================================

    @mcp.tool()
    async def list_installed_plugins() -> List[dict]:
        """List installed plugins only (fast, no remote fetch).

        This uses the local package database without contacting remote repos.
        Use list_available_plugins() for full repo data (slower)."""
        try:
            # Use status endpoint which has local package info without remote fetch
            result = await opnsense_api("/core/firmware/status")
            installed = []

            # Check for local packages in status
            for pkg_name, pkg_info in result.get("all_packages", {}).items():
                if isinstance(pkg_info, dict):
                    installed.append({
                        "name": pkg_info.get("name", pkg_name),
                        "version": pkg_info.get("old", pkg_info.get("new", "")),
                        "repository": pkg_info.get("repository", ""),
                    })

            # If all_packages is empty, try to get from product info
            if not installed:
                product = result.get("product", {})
                # Return minimal info from what's available locally
                return [{"note": "Use list_available_plugins() for full plugin list (requires remote fetch)"}]

            return installed
        except Exception as e:
            logger.error(f"Failed to list installed plugins: {e}")
            return [{"error": str(e)}]

    @mcp.tool()
    async def list_plugins() -> dict:
        """List all installed and available plugins (slow - fetches from remote repos).

        NOTE: This contacts remote package repositories and may take 10-30 seconds.
        For quick checks, use list_installed_plugins() instead."""
        try:
            info = await opnsense_api("/core/firmware/info")
            installed = []
            available = []

            for pkg in info.get("package", []):
                if isinstance(pkg, dict):
                    installed.append({
                        "name": pkg.get("name", ""),
                        "version": pkg.get("version", ""),
                        "comment": pkg.get("comment", ""),
                        "locked": pkg.get("locked", "0") == "1"
                    })

            for plugin in info.get("plugin", []):
                if isinstance(plugin, dict):
                    available.append({
                        "name": plugin.get("name", ""),
                        "version": plugin.get("version", ""),
                        "comment": plugin.get("comment", ""),
                        "installed": plugin.get("installed", "0") == "1"
                    })

            return {"installed": installed, "available": available}
        except Exception as e:
            logger.error(f"Failed to list plugins: {e}")
            return {"error": str(e), "installed": [], "available": []}

    @mcp.tool()
    async def search_plugins(query: str) -> List[dict]:
        """Search for plugins by name or description.

        Args:
            query: Search term (e.g., 'tailscale', 'wireguard', 'haproxy')"""
        try:
            info = await opnsense_api("/core/firmware/info")
            all_plugins = info.get("plugin", [])
            query_lower = query.lower()
            results = []
            for p in all_plugins:
                if isinstance(p, dict):
                    name = p.get("name", "")
                    comment = p.get("comment", "")
                    if query_lower in name.lower() or query_lower in comment.lower():
                        results.append({
                            "name": name,
                            "version": p.get("version", ""),
                            "comment": comment,
                            "installed": p.get("installed", "0") == "1"
                        })
            return results
        except Exception as e:
            logger.error(f"Failed to search plugins: {e}")
            return [{"error": str(e)}]

    @mcp.tool()
    async def install_plugin(package_name: str) -> str:
        """Install an OPNsense plugin by package name.

        Args:
            package_name: Plugin package name (e.g., 'os-tailscale', 'os-wireguard')"""
        result = await opnsense_api(f"/core/firmware/install/{package_name}", method="POST")
        return f"Installing {package_name}: {result}"

    @mcp.tool()
    async def remove_plugin(package_name: str) -> str:
        """Remove an installed OPNsense plugin.

        Args:
            package_name: Plugin package name (e.g., 'os-tailscale')"""
        result = await opnsense_api(f"/core/firmware/remove/{package_name}", method="POST")
        return f"Removing {package_name}: {result}"

    @mcp.tool()
    async def reinstall_plugin(package_name: str) -> str:
        """Reinstall an OPNsense plugin.

        Args:
            package_name: Plugin package name (e.g., 'os-tailscale')"""
        result = await opnsense_api(f"/core/firmware/reinstall/{package_name}", method="POST")
        return f"Reinstalling {package_name}: {result}"

    @mcp.tool()
    async def lock_plugin(package_name: str) -> str:
        """Lock a plugin to prevent automatic updates.

        Args:
            package_name: Plugin package name to lock"""
        result = await opnsense_api(f"/core/firmware/lock/{package_name}", method="POST")
        return f"Locked {package_name}: {result}"

    @mcp.tool()
    async def unlock_plugin(package_name: str) -> str:
        """Unlock a plugin to allow automatic updates.

        Args:
            package_name: Plugin package name to unlock"""
        result = await opnsense_api(f"/core/firmware/unlock/{package_name}", method="POST")
        return f"Unlocked {package_name}: {result}"

    @mcp.tool()
    async def upgrade_all_plugins() -> str:
        """Upgrade all installed plugins to latest versions."""
        result = await opnsense_api("/core/firmware/upgrade", method="POST")
        return f"Upgrade initiated: {result}"

    @mcp.tool()
    async def get_plugin_changelog(package_name: str) -> dict:
        """Get changelog for a plugin.

        Args:
            package_name: Plugin package name"""
        return await opnsense_api(f"/core/firmware/changelog/{package_name}")

    # =========================================================================
    # Tailscale Management (requires os-tailscale plugin)
    # =========================================================================

    @mcp.tool()
    async def get_tailscale_status() -> dict:
        """Get Tailscale service status and connection info.

        Note: Requires os-tailscale plugin to be installed."""
        try:
            status = await opnsense_api("/tailscale/service/status")
            return status
        except Exception as e:
            return {"error": str(e), "hint": "Ensure os-tailscale plugin is installed"}

    @mcp.tool()
    async def get_tailscale_config() -> dict:
        """Get Tailscale configuration.

        Note: Requires os-tailscale plugin to be installed."""
        try:
            config = await opnsense_api("/tailscale/general/get")
            return config
        except Exception as e:
            return {"error": str(e), "hint": "Ensure os-tailscale plugin is installed"}

    @mcp.tool()
    async def set_tailscale_config(
        enabled: bool = None,
        authkey: str = None,
        advertise_routes: str = None,
        accept_routes: bool = None,
        advertise_exit_node: bool = None
    ) -> str:
        """Configure Tailscale settings.

        Args:
            enabled: Enable/disable Tailscale
            authkey: Tailscale auth key (from admin.tailscale.com)
            advertise_routes: Comma-separated CIDRs to advertise (e.g., '10.10.0.0/24,10.20.0.0/24')
            accept_routes: Accept routes from other Tailscale nodes
            advertise_exit_node: Advertise this node as an exit node

        Note: Requires os-tailscale plugin to be installed."""
        try:
            data = {"general": {}}
            if enabled is not None:
                data["general"]["enabled"] = "1" if enabled else "0"
            if authkey is not None:
                data["general"]["authkey"] = authkey
            if advertise_routes is not None:
                data["general"]["advertise_routes"] = advertise_routes
            if accept_routes is not None:
                data["general"]["accept_routes"] = "1" if accept_routes else "0"
            if advertise_exit_node is not None:
                data["general"]["advertise_exit_node"] = "1" if advertise_exit_node else "0"

            await opnsense_api("/tailscale/general/set", method="POST", data=data)
            return "Tailscale configuration updated"
        except Exception as e:
            return f"Error: {e}. Ensure os-tailscale plugin is installed."

    @mcp.tool()
    async def start_tailscale() -> str:
        """Start the Tailscale service.

        Note: Requires os-tailscale plugin to be installed."""
        try:
            result = await opnsense_api("/tailscale/service/start", method="POST")
            return f"Tailscale started: {result.get('result', 'unknown')}"
        except Exception as e:
            return f"Error: {e}. Ensure os-tailscale plugin is installed."

    @mcp.tool()
    async def stop_tailscale() -> str:
        """Stop the Tailscale service.

        Note: Requires os-tailscale plugin to be installed."""
        try:
            result = await opnsense_api("/tailscale/service/stop", method="POST")
            return f"Tailscale stopped: {result.get('result', 'unknown')}"
        except Exception as e:
            return f"Error: {e}. Ensure os-tailscale plugin is installed."

    @mcp.tool()
    async def restart_tailscale() -> str:
        """Restart the Tailscale service.

        Note: Requires os-tailscale plugin to be installed."""
        try:
            result = await opnsense_api("/tailscale/service/restart", method="POST")
            return f"Tailscale restarted: {result.get('result', 'unknown')}"
        except Exception as e:
            return f"Error: {e}. Ensure os-tailscale plugin is installed."

    @mcp.tool()
    async def reconfigure_tailscale() -> str:
        """Apply pending Tailscale configuration changes.

        Note: Requires os-tailscale plugin to be installed."""
        try:
            result = await opnsense_api("/tailscale/service/reconfigure", method="POST")
            return f"Tailscale reconfigured: {result.get('result', 'unknown')}"
        except Exception as e:
            return f"Error: {e}. Ensure os-tailscale plugin is installed."

    # =========================================================================
    # Gateway Monitoring (dpinger)
    # =========================================================================

    @mcp.tool()
    async def search_gateways() -> List[dict]:
        """List all gateways with their UUIDs and monitoring configuration.

        Returns gateway details including monitoring status, thresholds, and UUIDs
        needed for set_gateway operations."""
        result = await opnsense_api("/routing/settings/searchGateway", method="POST", data={"current": 1, "rowCount": 50})
        return result.get("rows", [])

    @mcp.tool()
    async def get_gateway(uuid: str) -> dict:
        """Get detailed gateway configuration by UUID.

        Args:
            uuid: Gateway UUID from search_gateways"""
        result = await opnsense_api(f"/routing/settings/getGateway/{uuid}")
        return result.get("gateway", {})

    @mcp.tool()
    async def set_gateway(
        uuid: str,
        monitor: str = None,
        monitor_disable: bool = None,
        latency_low: int = None,
        latency_high: int = None,
        loss_low: int = None,
        loss_high: int = None,
        interval: int = None,
        loss_interval: int = None,
        time_period: int = None
    ) -> str:
        """Configure gateway monitoring settings (dpinger).

        Args:
            uuid: Gateway UUID from search_gateways
            monitor: Monitor IP address (e.g., '1.1.1.1' for Cloudflare DNS)
            monitor_disable: Set True to disable monitoring, False to enable
            latency_low: Low latency threshold in ms (warning, default 200)
            latency_high: High latency threshold in ms (alarm, default 500)
            loss_low: Low packet loss % (warning, default 10)
            loss_high: High packet loss % (alarm, default 20)
            interval: Probe interval in seconds (default 1)
            loss_interval: Loss calculation interval (default 4)
            time_period: Time period for stats in seconds (default 60)

        Example:
            set_gateway(uuid="abc123", monitor="1.1.1.1", monitor_disable=False,
                       latency_low=200, latency_high=400, loss_low=10, loss_high=20)
        """
        data = {"gateway": {}}

        if monitor is not None:
            data["gateway"]["monitor"] = monitor
        if monitor_disable is not None:
            data["gateway"]["monitor_disable"] = "1" if monitor_disable else "0"
        if latency_low is not None:
            data["gateway"]["latencylow"] = str(latency_low)
        if latency_high is not None:
            data["gateway"]["latencyhigh"] = str(latency_high)
        if loss_low is not None:
            data["gateway"]["losslow"] = str(loss_low)
        if loss_high is not None:
            data["gateway"]["losshigh"] = str(loss_high)
        if interval is not None:
            data["gateway"]["interval"] = str(interval)
        if loss_interval is not None:
            data["gateway"]["loss_interval"] = str(loss_interval)
        if time_period is not None:
            data["gateway"]["time_period"] = str(time_period)

        await opnsense_api(f"/routing/settings/setGateway/{uuid}", method="POST", data=data)
        return f"Gateway {uuid} configuration updated. Run reconfigure_gateways() to apply."

    @mcp.tool()
    async def reconfigure_gateways() -> str:
        """Apply pending gateway configuration changes."""
        result = await opnsense_api("/routing/settings/reconfigure", method="POST")
        return f"Gateways reconfigured: {result.get('status', 'unknown')}"

    # =========================================================================
    # Unbound DNS-over-TLS (DoT) Forwarding
    # =========================================================================

    @mcp.tool()
    async def search_unbound_forwards() -> List[dict]:
        """List all Unbound DNS forward destinations (including DoT servers).

        Returns forward entries with UUIDs needed for enabling DoT."""
        result = await opnsense_api("/unbound/settings/searchForward", method="POST", data={"current": 1, "rowCount": 50})
        return result.get("rows", [])

    @mcp.tool()
    async def get_unbound_forward(uuid: str) -> dict:
        """Get detailed forward destination configuration.

        Args:
            uuid: Forward entry UUID from search_unbound_forwards"""
        result = await opnsense_api(f"/unbound/settings/getForward/{uuid}")
        return result.get("forward", {})

    @mcp.tool()
    async def set_unbound_forward(
        uuid: str,
        enabled: bool = None,
        server: str = None,
        port: int = None,
        forward_type: str = None,
        verify: str = None
    ) -> str:
        """Configure an Unbound forward destination (enable/configure DoT).

        Args:
            uuid: Forward entry UUID from search_unbound_forwards
            enabled: Enable or disable this forward destination
            server: DNS server IP address (e.g., '1.1.1.1')
            port: Port number (53 for plain DNS, 853 for DoT)
            forward_type: Type - 'forward' (plain) or 'dot' (DNS-over-TLS)
            verify: TLS verification hostname for DoT (e.g., 'cloudflare-dns.com')

        Example - Enable Cloudflare DoT:
            set_unbound_forward(uuid="abc123", enabled=True, forward_type="dot",
                               server="1.1.1.1", port=853, verify="cloudflare-dns.com")
        """
        data = {"forward": {}}

        if enabled is not None:
            data["forward"]["enabled"] = "1" if enabled else "0"
        if server is not None:
            data["forward"]["server"] = server
        if port is not None:
            data["forward"]["port"] = str(port)
        if forward_type is not None:
            data["forward"]["type"] = forward_type
        if verify is not None:
            data["forward"]["verify"] = verify

        await opnsense_api(f"/unbound/settings/setForward/{uuid}", method="POST", data=data)
        return f"Forward {uuid} updated. Run reconfigure_unbound() to apply."

    @mcp.tool()
    async def add_unbound_forward(
        server: str,
        port: int = 853,
        forward_type: str = "dot",
        verify: str = "",
        enabled: bool = True
    ) -> str:
        """Add a new Unbound forward destination (e.g., DoT server).

        Args:
            server: DNS server IP address (e.g., '1.1.1.1', '9.9.9.9')
            port: Port number (853 for DoT, 53 for plain DNS)
            forward_type: Type - 'dot' (DNS-over-TLS) or 'forward' (plain)
            verify: TLS verification hostname for DoT (e.g., 'cloudflare-dns.com')
            enabled: Enable immediately (default True)

        Example - Add Cloudflare DoT:
            add_unbound_forward(server="1.1.1.1", port=853, forward_type="dot",
                               verify="cloudflare-dns.com")
        """
        data = {
            "forward": {
                "enabled": "1" if enabled else "0",
                "server": server,
                "port": str(port),
                "type": forward_type,
                "verify": verify
            }
        }

        result = await opnsense_api("/unbound/settings/addForward", method="POST", data=data)
        uuid = result.get("uuid", "unknown")
        return f"Added forward to {server}:{port} (UUID: {uuid}). Run reconfigure_unbound() to apply."

    @mcp.tool()
    async def delete_unbound_forward(uuid: str) -> str:
        """Delete an Unbound forward destination.

        Args:
            uuid: Forward entry UUID from search_unbound_forwards"""
        await opnsense_api(f"/unbound/settings/delForward/{uuid}", method="POST")
        return f"Deleted forward {uuid}. Run reconfigure_unbound() to apply."

    @mcp.tool()
    async def set_unbound_general(
        forwarding_enabled: bool = None,
        dnssec: bool = None,
        dns64: bool = None
    ) -> str:
        """Configure Unbound general settings.

        Args:
            forwarding_enabled: Enable DNS forwarding mode (required for DoT)
            dnssec: Enable DNSSEC validation
            dns64: Enable DNS64 for IPv6 translation

        Note: Forwarding must be enabled for DoT forwards to work."""
        # Get current config first
        current = await opnsense_api("/unbound/settings/get")
        unbound = current.get("unbound", {})

        data = {"unbound": {}}
        if forwarding_enabled is not None:
            data["unbound"]["forwarding"] = {"enabled": "1" if forwarding_enabled else "0"}
        if dnssec is not None:
            data["unbound"]["dnssec"] = "1" if dnssec else "0"
        if dns64 is not None:
            data["unbound"]["dns64"] = "1" if dns64 else "0"

        await opnsense_api("/unbound/settings/set", method="POST", data=data)
        return "Unbound settings updated. Run reconfigure_unbound() to apply."

    @mcp.tool()
    async def reconfigure_unbound() -> str:
        """Apply pending Unbound DNS configuration changes."""
        result = await opnsense_api("/unbound/service/reconfigure", method="POST")
        return f"Unbound reconfigured: {result.get('status', 'unknown')}"

    # =========================================================================
    # Telegraf Metrics Export (requires os-telegraf plugin)
    # =========================================================================

    @mcp.tool()
    async def get_telegraf_config() -> dict:
        """Get Telegraf configuration including general, input, and output settings.

        Note: Requires os-telegraf plugin to be installed."""
        try:
            general = await opnsense_api("/telegraf/general/get")
            return general
        except Exception as e:
            return {"error": str(e), "hint": "Ensure os-telegraf plugin is installed"}

    @mcp.tool()
    async def set_telegraf_general(
        enabled: bool = None,
        interval: int = None,
        flush_interval: int = None
    ) -> str:
        """Configure Telegraf general settings.

        Args:
            enabled: Enable or disable Telegraf
            interval: Data collection interval in seconds (default 30)
            flush_interval: Metric flush interval in seconds (default 10)

        Note: Requires os-telegraf plugin to be installed."""
        try:
            data = {"general": {}}
            if enabled is not None:
                data["general"]["enabled"] = "1" if enabled else "0"
            if interval is not None:
                data["general"]["interval"] = str(interval)
            if flush_interval is not None:
                data["general"]["flush_interval"] = str(flush_interval)

            await opnsense_api("/telegraf/general/set", method="POST", data=data)
            return "Telegraf general settings updated. Run reconfigure_telegraf() to apply."
        except Exception as e:
            return f"Error: {e}. Ensure os-telegraf plugin is installed."

    @mcp.tool()
    async def set_telegraf_output(
        influx_enable: bool = None,
        influx_url: str = None,
        influx_database: str = None,
        influx_username: str = None,
        influx_password: str = None,
        influx_insecure_skip_verify: bool = None
    ) -> str:
        """Configure Telegraf InfluxDB/VictoriaMetrics output.

        Args:
            influx_enable: Enable InfluxDB output
            influx_url: InfluxDB/VictoriaMetrics URL (e.g., 'http://10.30.0.120:8428')
            influx_database: Database name (default 'opnsense')
            influx_username: Optional username for auth
            influx_password: Optional password for auth
            influx_insecure_skip_verify: Skip TLS verification

        Note: VictoriaMetrics supports InfluxDB line protocol on /write endpoint."""
        try:
            data = {"output": {}}
            if influx_enable is not None:
                data["output"]["influx_enable"] = "1" if influx_enable else "0"
            if influx_url is not None:
                data["output"]["influx_url"] = influx_url
            if influx_database is not None:
                data["output"]["influx_database"] = influx_database
            if influx_username is not None:
                data["output"]["influx_username"] = influx_username
            if influx_password is not None:
                data["output"]["influx_password"] = influx_password
            if influx_insecure_skip_verify is not None:
                data["output"]["influx_insecure_skip_verify"] = "1" if influx_insecure_skip_verify else "0"

            await opnsense_api("/telegraf/output/set", method="POST", data=data)
            return "Telegraf output settings updated. Run reconfigure_telegraf() to apply."
        except Exception as e:
            return f"Error: {e}. Ensure os-telegraf plugin is installed."

    @mcp.tool()
    async def set_telegraf_input(
        cpu: bool = None,
        disk: bool = None,
        diskio: bool = None,
        mem: bool = None,
        net: bool = None,
        pf: bool = None,
        system: bool = None,
        processes: bool = None,
        haproxy: bool = None,
        zfs: bool = None
    ) -> str:
        """Configure Telegraf input plugins.

        Args:
            cpu: Enable CPU metrics
            disk: Enable disk usage metrics
            diskio: Enable disk I/O metrics
            mem: Enable memory metrics
            net: Enable network interface metrics
            pf: Enable PF firewall metrics
            system: Enable system metrics (load, uptime)
            processes: Enable process metrics
            haproxy: Enable HAProxy metrics (requires os-haproxy)
            zfs: Enable ZFS metrics

        Recommended minimum: cpu, mem, disk, net, pf, system"""
        try:
            data = {"input": {}}
            if cpu is not None:
                data["input"]["cpu"] = "1" if cpu else "0"
            if disk is not None:
                data["input"]["disk"] = "1" if disk else "0"
            if diskio is not None:
                data["input"]["diskio"] = "1" if diskio else "0"
            if mem is not None:
                data["input"]["mem"] = "1" if mem else "0"
            if net is not None:
                data["input"]["net"] = "1" if net else "0"
            if pf is not None:
                data["input"]["pf"] = "1" if pf else "0"
            if system is not None:
                data["input"]["system"] = "1" if system else "0"
            if processes is not None:
                data["input"]["processes"] = "1" if processes else "0"
            if haproxy is not None:
                data["input"]["haproxy"] = "1" if haproxy else "0"
            if zfs is not None:
                data["input"]["zfs"] = "1" if zfs else "0"

            await opnsense_api("/telegraf/input/set", method="POST", data=data)
            return "Telegraf input settings updated. Run reconfigure_telegraf() to apply."
        except Exception as e:
            return f"Error: {e}. Ensure os-telegraf plugin is installed."

    @mcp.tool()
    async def reconfigure_telegraf() -> str:
        """Apply pending Telegraf configuration changes.

        Note: Requires os-telegraf plugin to be installed."""
        try:
            result = await opnsense_api("/telegraf/service/reconfigure", method="POST")
            return f"Telegraf reconfigured: {result.get('status', 'unknown')}"
        except Exception as e:
            return f"Error: {e}. Ensure os-telegraf plugin is installed."

    @mcp.tool()
    async def start_telegraf() -> str:
        """Start the Telegraf service.

        Note: Requires os-telegraf plugin to be installed."""
        try:
            result = await opnsense_api("/telegraf/service/start", method="POST")
            return f"Telegraf started: {result.get('result', 'unknown')}"
        except Exception as e:
            return f"Error: {e}. Ensure os-telegraf plugin is installed."

    @mcp.tool()
    async def stop_telegraf() -> str:
        """Stop the Telegraf service.

        Note: Requires os-telegraf plugin to be installed."""
        try:
            result = await opnsense_api("/telegraf/service/stop", method="POST")
            return f"Telegraf stopped: {result.get('result', 'unknown')}"
        except Exception as e:
            return f"Error: {e}. Ensure os-telegraf plugin is installed."

    @mcp.tool()
    async def restart_telegraf() -> str:
        """Restart the Telegraf service.

        Note: Requires os-telegraf plugin to be installed."""
        try:
            result = await opnsense_api("/telegraf/service/restart", method="POST")
            return f"Telegraf restarted: {result.get('result', 'unknown')}"
        except Exception as e:
            return f"Error: {e}. Ensure os-telegraf plugin is installed."
