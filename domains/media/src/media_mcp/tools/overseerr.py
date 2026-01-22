"""Overseerr request management tools."""

import os
import logging
from typing import List

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
OVERSEERR_URL = os.environ.get("OVERSEERR_URL", "https://overseerr.kernow.io")
OVERSEERR_API_KEY = os.environ.get("OVERSEERR_API_KEY", "")


async def overseerr_request(endpoint: str, method: str = "GET", data: dict = None) -> dict:
    """Make request to Overseerr API."""
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        headers = {"X-Api-Key": OVERSEERR_API_KEY}
        url = f"{OVERSEERR_URL}/api/v1/{endpoint}"
        response = await client.request(method, url, headers=headers, json=data)
        response.raise_for_status()
        return response.json() if response.content else {}


async def get_status() -> dict:
    """Get Overseerr status for health checks."""
    try:
        return await overseerr_request("status")
    except Exception as e:
        return {"error": str(e)}


def register_tools(mcp: FastMCP):
    """Register Overseerr tools with the MCP server."""

    @mcp.tool()
    async def overseerr_list_requests(status: str = "pending") -> List[dict]:
        """List media requests. Status: pending, approved, declined, all."""
        try:
            params = "" if status == "all" else f"filter={status}"
            requests = await overseerr_request(f"request?{params}")
            return [{
                "id": r.get("id"),
                "type": r.get("type"),
                "title": r.get("media", {}).get("title") or r.get("media", {}).get("name"),
                "status": r.get("status"),
                "requestedBy": r.get("requestedBy", {}).get("displayName"),
                "createdAt": r.get("createdAt")
            } for r in requests.get("results", [])]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def overseerr_approve_request(request_id: int) -> dict:
        """Approve a media request."""
        try:
            await overseerr_request(f"request/{request_id}/approve", "POST")
            return {"success": True, "message": f"Request {request_id} approved"}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def overseerr_decline_request(request_id: int) -> dict:
        """Decline a media request."""
        try:
            await overseerr_request(f"request/{request_id}/decline", "POST")
            return {"success": True, "message": f"Request {request_id} declined"}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def overseerr_get_trending() -> dict:
        """Get trending movies and TV shows."""
        try:
            movies = await overseerr_request("discover/movies")
            tv = await overseerr_request("discover/tv")
            return {
                "movies": [{"title": m.get("title"), "year": m.get("releaseDate", "")[:4]}
                          for m in movies.get("results", [])[:5]],
                "tv": [{"title": t.get("name"), "year": t.get("firstAirDate", "")[:4]}
                      for t in tv.get("results", [])[:5]]
            }
        except Exception as e:
            return {"error": str(e)}
