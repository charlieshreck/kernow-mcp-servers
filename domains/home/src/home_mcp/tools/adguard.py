"""AdGuard Home DNS management tools."""

import os
import json
import logging
from typing import Optional, List, Dict, Any

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
ADGUARD_HOST = os.environ.get("ADGUARD_HOST", "http://10.10.0.1:3000")
ADGUARD_USER = os.environ.get("ADGUARD_USER", "admin")
ADGUARD_PASSWORD = os.environ.get("ADGUARD_PASSWORD", "")


async def adguard_api(endpoint: str, method: str = "GET", data: dict = None) -> Dict[str, Any]:
    """Make request to AdGuard Home API."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        url = f"{ADGUARD_HOST}/{endpoint}"
        if method == "GET":
            response = await client.get(url, auth=(ADGUARD_USER, ADGUARD_PASSWORD))
        else:
            response = await client.post(url, auth=(ADGUARD_USER, ADGUARD_PASSWORD), json=data)
        response.raise_for_status()
        return response.json()


async def get_status() -> dict:
    """Get AdGuard status for health checks."""
    try:
        stats = await adguard_api("control/stats")
        return {"status": "healthy", "total_queries": stats.get("num_dns_queries", 0)}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


def register_tools(mcp: FastMCP):
    """Register AdGuard tools with the MCP server."""

    @mcp.tool()
    async def adguard_get_stats() -> dict:
        """Get DNS statistics including total queries, blocked queries, and top clients."""
        try:
            stats = await adguard_api("control/stats")
            return {
                "total_queries": stats.get("num_dns_queries", 0),
                "blocked_queries": stats.get("num_blocked_filtering", 0),
                "blocked_safebrowsing": stats.get("num_replaced_safebrowsing", 0),
                "blocked_parental": stats.get("num_replaced_parental", 0),
                "avg_processing_time": stats.get("avg_processing_time", 0),
                "top_clients": stats.get("top_clients", [])[:10],
                "top_blocked_domains": stats.get("top_blocked_domains", [])[:10],
                "top_queried_domains": stats.get("top_queried_domains", [])[:10]
            }
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def adguard_get_query_log(limit: int = 50, search: Optional[str] = None) -> List[dict]:
        """Get recent DNS query log with domain, client, and block status."""
        try:
            params = f"limit={limit}"
            if search:
                params += f"&search={search}"
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{ADGUARD_HOST}/control/querylog?{params}",
                    auth=(ADGUARD_USER, ADGUARD_PASSWORD)
                )
                response.raise_for_status()
                data = response.json()

            return [{
                "domain": q.get("question", {}).get("name"),
                "client": q.get("client"),
                "type": q.get("question", {}).get("type"),
                "answer": q.get("answer", [{}])[0].get("value") if q.get("answer") else None,
                "blocked": q.get("reason", "") != "",
                "reason": q.get("reason"),
                "time": q.get("time")
            } for q in data.get("data", [])]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def adguard_get_filtering_status() -> dict:
        """Get current filtering configuration including enabled lists and rules count."""
        try:
            status = await adguard_api("control/filtering/status")
            return {
                "enabled": status.get("enabled"),
                "interval": status.get("interval"),
                "filters": [{
                    "name": f.get("name"),
                    "url": f.get("url"),
                    "enabled": f.get("enabled"),
                    "rules_count": f.get("rules_count"),
                    "last_updated": f.get("last_updated")
                } for f in status.get("filters", [])],
                "user_rules_count": len(status.get("user_rules", []))
            }
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def adguard_list_rewrites() -> List[dict]:
        """List all DNS rewrites (custom domain to IP mappings)."""
        try:
            rewrites = await adguard_api("control/rewrite/list")
            return [{
                "domain": r.get("domain"),
                "answer": r.get("answer")
            } for r in rewrites]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def adguard_add_rewrite(domain: str, answer: str) -> dict:
        """Add a DNS rewrite rule (domain to IP mapping)."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{ADGUARD_HOST}/control/rewrite/add",
                    auth=(ADGUARD_USER, ADGUARD_PASSWORD),
                    json={"domain": domain, "answer": answer}
                )
                response.raise_for_status()
            return {"success": True, "message": f"Added rewrite: {domain} -> {answer}"}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def adguard_delete_rewrite(domain: str, answer: str) -> dict:
        """Delete a DNS rewrite rule."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{ADGUARD_HOST}/control/rewrite/delete",
                    auth=(ADGUARD_USER, ADGUARD_PASSWORD),
                    json={"domain": domain, "answer": answer}
                )
                response.raise_for_status()
            return {"success": True, "message": f"Deleted rewrite: {domain}"}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def adguard_get_protection_status() -> dict:
        """Get current protection status (enabled/disabled)."""
        try:
            status = await adguard_api("control/status")
            return {
                "protection_enabled": status.get("protection_enabled"),
                "running": status.get("running"),
                "dns_addresses": status.get("dns_addresses"),
                "version": status.get("version")
            }
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def adguard_set_protection(enabled: bool) -> dict:
        """Enable or disable DNS protection."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{ADGUARD_HOST}/control/protection",
                    auth=(ADGUARD_USER, ADGUARD_PASSWORD),
                    json={"enabled": enabled}
                )
                response.raise_for_status()
            return {"success": True, "protection_enabled": enabled}
        except Exception as e:
            return {"error": str(e)}
