"""Notifiarr notification client tools."""

import os
import logging

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
NOTIFIARR_URL = os.environ.get("NOTIFIARR_URL", "https://notifiarr.kernow.io")


async def notifiarr_request(endpoint: str, method: str = "GET", data: dict = None) -> dict:
    """Make request to Notifiarr API."""
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        url = f"{NOTIFIARR_URL}/{endpoint.lstrip('/')}"
        response = await client.request(method, url, json=data)
        response.raise_for_status()
        return response.json() if response.content else {}


async def get_status() -> dict:
    """Get Notifiarr status for health checks."""
    try:
        return await notifiarr_request("api/version")
    except Exception as e:
        return {"error": str(e)}


def register_tools(mcp: FastMCP):
    """Register Notifiarr tools with the MCP server."""

    @mcp.tool()
    async def notifiarr_get_version() -> dict:
        """Get Notifiarr client version information."""
        try:
            return await notifiarr_request("api/version")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def notifiarr_get_status() -> dict:
        """Get Notifiarr client status and health."""
        try:
            return await notifiarr_request("api/status")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def notifiarr_trigger_snapshot() -> dict:
        """Trigger a system snapshot notification."""
        try:
            result = await notifiarr_request("api/trigger/snapshot", "POST")
            return {"success": True, "message": "Snapshot triggered", "result": result}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def notifiarr_trigger_dashboard() -> dict:
        """Trigger a dashboard notification update."""
        try:
            result = await notifiarr_request("api/trigger/dashboard", "POST")
            return {"success": True, "message": "Dashboard update triggered", "result": result}
        except Exception as e:
            return {"error": str(e)}
