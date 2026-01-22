"""Prowlarr indexer management tools."""

import os
import logging
from typing import List

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
PROWLARR_URL = os.environ.get("PROWLARR_URL", "https://prowlarr.kernow.io")
PROWLARR_API_KEY = os.environ.get("PROWLARR_API_KEY", "")


async def prowlarr_request(endpoint: str, method: str = "GET", data: dict = None) -> dict:
    """Make request to Prowlarr API (v1)."""
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        headers = {"X-Api-Key": PROWLARR_API_KEY}
        url = f"{PROWLARR_URL}/api/v1/{endpoint}"
        response = await client.request(method, url, headers=headers, json=data)
        response.raise_for_status()
        return response.json() if response.content else {}


async def get_health() -> List[dict]:
    """Get Prowlarr health for health checks."""
    try:
        return await prowlarr_request("health")
    except Exception as e:
        return [{"error": str(e)}]


def register_tools(mcp: FastMCP):
    """Register Prowlarr tools with the MCP server."""

    @mcp.tool()
    async def prowlarr_list_indexers() -> List[dict]:
        """List all configured indexers and their status."""
        try:
            indexers = await prowlarr_request("indexer")
            return [{
                "id": idx["id"],
                "name": idx["name"],
                "protocol": idx.get("protocol"),
                "privacy": idx.get("privacy"),
                "enabled": idx.get("enable", False),
                "priority": idx.get("priority", 25)
            } for idx in indexers]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def prowlarr_get_health() -> List[dict]:
        """Get Prowlarr health/status checks."""
        try:
            health = await prowlarr_request("health")
            return [{"source": h.get("source"), "type": h.get("type"),
                     "message": h.get("message")} for h in health]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def prowlarr_test_indexer(indexer_id: int) -> dict:
        """Test an indexer connection."""
        try:
            await prowlarr_request(f"indexer/{indexer_id}/test", "POST")
            return {"success": True, "message": f"Indexer {indexer_id} test passed"}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def prowlarr_search(query: str, indexer_ids: List[int] = None) -> List[dict]:
        """Search across indexers."""
        try:
            params = f"query={query}"
            if indexer_ids:
                params += "&" + "&".join([f"indexerIds={i}" for i in indexer_ids])
            results = await prowlarr_request(f"search?{params}")
            return [{
                "title": r.get("title"),
                "indexer": r.get("indexer"),
                "size": r.get("size"),
                "seeders": r.get("seeders"),
                "age": r.get("age")
            } for r in results[:20]]
        except Exception as e:
            return [{"error": str(e)}]
