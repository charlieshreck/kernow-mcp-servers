"""Cloudflare DNS and tunnel management tools."""

import os
import logging
from typing import Optional, List
from enum import Enum

import httpx
from fastmcp import FastMCP
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Configuration
CLOUDFLARE_API_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "")
CLOUDFLARE_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
CF_BASE_URL = "https://api.cloudflare.com/client/v4"


class ResponseFormat(str, Enum):
    markdown = "markdown"
    json = "json"


async def cf_api(endpoint: str, method: str = "GET", data: dict = None) -> dict:
    """Make authenticated API call to Cloudflare."""
    headers = {
        "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
        "Content-Type": "application/json"
    }
    url = f"{CF_BASE_URL}{endpoint}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        if method == "GET":
            resp = await client.get(url, headers=headers)
        elif method == "POST":
            resp = await client.post(url, headers=headers, json=data)
        elif method == "PUT":
            resp = await client.put(url, headers=headers, json=data)
        elif method == "PATCH":
            resp = await client.patch(url, headers=headers, json=data)
        elif method == "DELETE":
            resp = await client.delete(url, headers=headers)
        else:
            raise ValueError(f"Unsupported method: {method}")

        resp.raise_for_status()
        return resp.json()


async def get_status() -> dict:
    """Get Cloudflare status for health checks."""
    try:
        result = await cf_api("/zones?per_page=1")
        return {"status": "healthy", "zones": result.get("result_info", {}).get("total_count", 0)}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


def register_tools(mcp: FastMCP):
    """Register Cloudflare tools with the MCP server."""

    class BaseInput(BaseModel):
        response_format: ResponseFormat = Field(default=ResponseFormat.markdown, description="Output format")

    class ZoneInput(BaseModel):
        zone_id: str = Field(description="Zone ID (32-char hex string)", min_length=32, max_length=32)
        response_format: ResponseFormat = Field(default=ResponseFormat.markdown, description="Output format")

    class TunnelInput(BaseModel):
        tunnel_id: str = Field(description="Tunnel UUID", min_length=36, max_length=36)

    class DNSRecordSearchInput(BaseModel):
        zone_id: str = Field(description="Zone ID", min_length=32, max_length=32)
        name: Optional[str] = Field(default=None, description="Filter by name")
        record_type: Optional[str] = Field(default=None, description="Filter by type")
        response_format: ResponseFormat = Field(default=ResponseFormat.markdown)

    class DNSRecordCreateInput(BaseModel):
        zone_id: str = Field(description="Zone ID", min_length=32, max_length=32)
        record_type: str = Field(description="Record type: A, AAAA, CNAME, TXT, MX, etc.")
        name: str = Field(description="Record name (e.g., 'www' or 'sub.domain.com')")
        content: str = Field(description="Record content (IP, target, etc.)")
        ttl: int = Field(default=1, description="TTL in seconds (1 = auto)")
        proxied: bool = Field(default=False, description="Enable Cloudflare proxy (orange cloud)")
        priority: Optional[int] = Field(default=None, description="Priority for MX records")

    class DNSRecordUpdateInput(BaseModel):
        zone_id: str = Field(description="Zone ID", min_length=32, max_length=32)
        record_id: str = Field(description="Record ID to update")
        record_type: Optional[str] = Field(default=None, description="Record type")
        name: Optional[str] = Field(default=None, description="Record name")
        content: Optional[str] = Field(default=None, description="Record content")
        ttl: Optional[int] = Field(default=None, description="TTL")
        proxied: Optional[bool] = Field(default=None, description="Proxy status")

    class DNSRecordDeleteInput(BaseModel):
        zone_id: str = Field(description="Zone ID", min_length=32, max_length=32)
        record_id: str = Field(description="Record ID to delete")
        confirmation: bool = Field(default=False, description="Must be true to delete")

    class PurgeCacheInput(BaseModel):
        zone_id: str = Field(description="Zone ID", min_length=32, max_length=32)
        purge_everything: bool = Field(default=False, description="Purge all cached content")
        files: Optional[List[str]] = Field(default=None, description="List of URLs to purge")
        tags: Optional[List[str]] = Field(default=None, description="Cache tags to purge")
        confirmation: bool = Field(default=False, description="Must be true to purge")

    class ZoneSettingInput(BaseModel):
        zone_id: str = Field(description="Zone ID", min_length=32, max_length=32)

    # =========================================================================
    # Zones
    # =========================================================================

    @mcp.tool()
    async def cloudflare_list_zones(params: BaseInput) -> str:
        """List all DNS zones in the Cloudflare account with status and nameservers."""
        result = await cf_api("/zones")
        zones = result.get("result", [])

        if params.response_format == ResponseFormat.json:
            return zones

        output = ["# Cloudflare Zones\n"]
        for zone in zones:
            status = "🟢" if zone.get("status") == "active" else "🟡"
            output.append(f"## {status} {zone.get('name')}")
            output.append(f"- ID: `{zone.get('id')}`")
            output.append(f"- Status: {zone.get('status')}")
            output.append(f"- Plan: {zone.get('plan', {}).get('name')}")
            output.append("")

        return "\n".join(output)

    @mcp.tool()
    async def cloudflare_list_dns_records(params: ZoneInput) -> str:
        """List all DNS records for a specific zone. Use cloudflare_list_zones first to get zone_id."""
        result = await cf_api(f"/zones/{params.zone_id}/dns_records")
        records = result.get("result", [])

        if params.response_format == ResponseFormat.json:
            return records

        output = ["# DNS Records\n"]
        for rec in sorted(records, key=lambda x: (x.get("type", ""), x.get("name", ""))):
            proxied = "☁️" if rec.get("proxied") else "⬛"
            output.append(f"- {proxied} **{rec.get('type')}** {rec.get('name')} → {rec.get('content')[:50]}")

        return "\n".join(output)

    @mcp.tool()
    async def cloudflare_list_tunnels(params: BaseInput) -> str:
        """List all Cloudflare Tunnels in the account with status and connections."""
        result = await cf_api(f"/accounts/{CLOUDFLARE_ACCOUNT_ID}/cfd_tunnel")
        tunnels = result.get("result", [])

        if params.response_format == ResponseFormat.json:
            return tunnels

        output = ["# Cloudflare Tunnels\n"]
        for tunnel in tunnels:
            status = "🟢" if tunnel.get("status") == "healthy" else "🔴"
            output.append(f"## {status} {tunnel.get('name')}")
            output.append(f"- ID: `{tunnel.get('id')}`")
            output.append(f"- Status: {tunnel.get('status')}")
            output.append(f"- Connections: {len(tunnel.get('connections', []))}")
            output.append("")

        return "\n".join(output)

    @mcp.tool()
    async def cloudflare_get_tunnel_status(params: TunnelInput) -> dict:
        """Get detailed status of a specific Cloudflare Tunnel including active connections."""
        result = await cf_api(f"/accounts/{CLOUDFLARE_ACCOUNT_ID}/cfd_tunnel/{params.tunnel_id}")
        return result.get("result", {})

    @mcp.tool()
    async def cloudflare_search_dns_records(params: DNSRecordSearchInput) -> str:
        """Search DNS records by name or type."""
        url = f"/zones/{params.zone_id}/dns_records"
        query_params = []
        if params.name:
            query_params.append(f"name={params.name}")
        if params.record_type:
            query_params.append(f"type={params.record_type}")
        if query_params:
            url += "?" + "&".join(query_params)

        result = await cf_api(url)
        records = result.get("result", [])

        if params.response_format == ResponseFormat.json:
            return records

        if not records:
            return "No matching DNS records found."

        output = ["# Matching DNS Records\n"]
        for rec in records:
            output.append(f"- **{rec.get('type')}** {rec.get('name')} → {rec.get('content')}")
            output.append(f"  ID: `{rec.get('id')}`")

        return "\n".join(output)

    @mcp.tool()
    async def cloudflare_add_dns_record(params: DNSRecordCreateInput) -> str:
        """Create a new DNS record."""
        data = {
            "type": params.record_type,
            "name": params.name,
            "content": params.content,
            "ttl": params.ttl,
            "proxied": params.proxied
        }
        if params.priority is not None:
            data["priority"] = params.priority

        result = await cf_api(f"/zones/{params.zone_id}/dns_records", method="POST", data=data)
        rec = result.get("result", {})
        return f"Created DNS record: {rec.get('type')} {rec.get('name')} → {rec.get('content')} (ID: {rec.get('id')})"

    @mcp.tool()
    async def cloudflare_update_dns_record(params: DNSRecordUpdateInput) -> str:
        """Update an existing DNS record."""
        # Get current record first
        current = await cf_api(f"/zones/{params.zone_id}/dns_records/{params.record_id}")
        rec = current.get("result", {})

        data = {
            "type": params.record_type or rec.get("type"),
            "name": params.name or rec.get("name"),
            "content": params.content or rec.get("content"),
            "ttl": params.ttl if params.ttl is not None else rec.get("ttl"),
            "proxied": params.proxied if params.proxied is not None else rec.get("proxied")
        }

        result = await cf_api(f"/zones/{params.zone_id}/dns_records/{params.record_id}", method="PUT", data=data)
        updated = result.get("result", {})
        return f"Updated DNS record: {updated.get('type')} {updated.get('name')} → {updated.get('content')}"

    @mcp.tool()
    async def cloudflare_delete_dns_record(params: DNSRecordDeleteInput) -> str:
        """Delete a DNS record. DESTRUCTIVE - requires confirmation=true."""
        if not params.confirmation:
            return "Error: Set confirmation=true to delete this DNS record."

        await cf_api(f"/zones/{params.zone_id}/dns_records/{params.record_id}", method="DELETE")
        return f"Deleted DNS record {params.record_id}"

    @mcp.tool()
    async def cloudflare_purge_cache(params: PurgeCacheInput) -> str:
        """Purge Cloudflare cache. Requires confirmation=true."""
        if not params.confirmation:
            return "Error: Set confirmation=true to purge cache."

        data = {}
        if params.purge_everything:
            data["purge_everything"] = True
        elif params.files:
            data["files"] = params.files
        elif params.tags:
            data["tags"] = params.tags
        else:
            return "Error: Specify purge_everything, files, or tags."

        await cf_api(f"/zones/{params.zone_id}/purge_cache", method="POST", data=data)
        return "Cache purge initiated."

    @mcp.tool()
    async def cloudflare_get_zone_settings(params: ZoneSettingInput) -> dict:
        """Get zone security and performance settings."""
        result = await cf_api(f"/zones/{params.zone_id}/settings")
        return result.get("result", [])

    @mcp.tool()
    async def cloudflare_get_analytics(params: ZoneSettingInput) -> dict:
        """Get zone analytics summary (requests, bandwidth, threats)."""
        result = await cf_api(f"/zones/{params.zone_id}/analytics/dashboard?since=-10080")
        return result.get("result", {})
