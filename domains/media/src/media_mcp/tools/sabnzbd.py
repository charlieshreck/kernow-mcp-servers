"""SABnzbd usenet download management tools."""

import os
import logging
from typing import List

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
SABNZBD_URL = os.environ.get("SABNZBD_URL", "https://sabnzbd.kernow.io")
SABNZBD_API_KEY = os.environ.get("SABNZBD_API_KEY", "")


async def sabnzbd_request(mode: str, **params) -> dict:
    """Make request to SABnzbd API."""
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        params["apikey"] = SABNZBD_API_KEY
        params["mode"] = mode
        params["output"] = "json"
        url = f"{SABNZBD_URL}/api"
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()


async def get_queue() -> dict:
    """Get SABnzbd queue for health checks."""
    try:
        result = await sabnzbd_request("queue")
        queue = result.get("queue", {})
        return {
            "status": queue.get("status"),
            "speed": queue.get("speed"),
            "timeleft": queue.get("timeleft"),
            "mb_left": queue.get("mbleft"),
            "slots": [{
                "filename": s.get("filename"),
                "status": s.get("status"),
                "percentage": s.get("percentage"),
                "timeleft": s.get("timeleft"),
                "mb_left": s.get("mbleft")
            } for s in queue.get("slots", [])]
        }
    except Exception as e:
        return {"error": str(e)}


def register_tools(mcp: FastMCP):
    """Register SABnzbd tools with the MCP server."""

    @mcp.tool()
    async def sabnzbd_get_queue() -> dict:
        """Get SABnzbd download queue."""
        return await get_queue()

    @mcp.tool()
    async def sabnzbd_get_history(limit: int = 10) -> List[dict]:
        """Get SABnzbd download history."""
        try:
            result = await sabnzbd_request("history", limit=limit)
            return [{
                "name": h.get("name"),
                "status": h.get("status"),
                "size": h.get("size"),
                "completed": h.get("completed"),
                "category": h.get("category")
            } for h in result.get("history", {}).get("slots", [])]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def sabnzbd_pause_queue() -> dict:
        """Pause SABnzbd queue."""
        try:
            await sabnzbd_request("pause")
            return {"success": True, "message": "SABnzbd queue paused"}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def sabnzbd_resume_queue() -> dict:
        """Resume SABnzbd queue."""
        try:
            await sabnzbd_request("resume")
            return {"success": True, "message": "SABnzbd queue resumed"}
        except Exception as e:
            return {"error": str(e)}
