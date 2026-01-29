"""Huntarr missing media discovery tools."""

import os
import logging
from typing import List

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
HUNTARR_URL = os.environ.get("HUNTARR_URL", "https://huntarr.kernow.io")

# Supported apps
HUNTARR_APPS = ["sonarr", "radarr", "lidarr", "readarr", "whisparr"]


async def huntarr_request(endpoint: str, method: str = "GET", data: dict = None) -> dict:
    """Make request to Huntarr API."""
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        url = f"{HUNTARR_URL}/{endpoint.lstrip('/')}"
        response = await client.request(method, url, json=data)
        response.raise_for_status()
        return response.json() if response.content else {}


async def get_status() -> dict:
    """Get Huntarr status for health checks."""
    try:
        return await huntarr_request("api/health")
    except Exception as e:
        return {"error": str(e)}


def register_tools(mcp: FastMCP):
    """Register Huntarr tools with the MCP server."""

    @mcp.tool()
    async def huntarr_get_status() -> dict:
        """Get Huntarr system status including version and uptime."""
        try:
            return await huntarr_request("api/health")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def huntarr_get_settings(app: str = "sonarr") -> dict:
        """Get Huntarr settings for a specific app.

        Args:
            app: One of sonarr, radarr, lidarr, readarr, whisparr
        """
        if app not in HUNTARR_APPS:
            return {"error": f"Invalid app. Must be one of: {HUNTARR_APPS}"}
        try:
            all_settings = await huntarr_request("api/settings")
            return all_settings.get(app, {"error": f"No settings found for {app}"})

    @mcp.tool()
    async def huntarr_update_settings(app: str, settings: dict) -> dict:
        """Update Huntarr settings for a specific app.

        Args:
            app: One of sonarr, radarr, lidarr, readarr, whisparr
            settings: Dictionary of settings to update (e.g., {"missing_search": true, "upgrade_search": true})
        """
        if app not in HUNTARR_APPS:
            return {"error": f"Invalid app. Must be one of: {HUNTARR_APPS}"}
        try:
            result = await huntarr_request(f"api/{app}/settings", "POST", settings)
            return {"success": True, "message": f"Settings updated for {app}", "result": result}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def huntarr_trigger_missing_search(app: str = "sonarr") -> dict:
        """Trigger a missing media search for a specific app.

        Args:
            app: One of sonarr, radarr, lidarr, readarr, whisparr
        """
        if app not in HUNTARR_APPS:
            return {"error": f"Invalid app. Must be one of: {HUNTARR_APPS}"}
        try:
            result = await huntarr_request(f"api/{app}/missing", "POST")
            return {"success": True, "message": f"Missing search triggered for {app}", "result": result}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def huntarr_trigger_upgrade_search(app: str = "sonarr") -> dict:
        """Trigger an upgrade search for a specific app.

        Args:
            app: One of sonarr, radarr, lidarr, readarr, whisparr
        """
        if app not in HUNTARR_APPS:
            return {"error": f"Invalid app. Must be one of: {HUNTARR_APPS}"}
        try:
            result = await huntarr_request(f"api/{app}/upgrade", "POST")
            return {"success": True, "message": f"Upgrade search triggered for {app}", "result": result}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def huntarr_reset_state(app: str = "sonarr", reset_type: str = "all") -> dict:
        """Reset Huntarr state for a specific app.

        Args:
            app: One of sonarr, radarr, lidarr, readarr, whisparr
            reset_type: One of "all", "missing", "upgrade"
        """
        if app not in HUNTARR_APPS:
            return {"error": f"Invalid app. Must be one of: {HUNTARR_APPS}"}
        if reset_type not in ["all", "missing", "upgrade"]:
            return {"error": "Invalid reset_type. Must be one of: all, missing, upgrade"}
        try:
            result = await huntarr_request(f"api/{app}/reset", "POST", {"type": reset_type})
            return {"success": True, "message": f"State reset for {app} ({reset_type})", "result": result}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def huntarr_test_connection(app: str, api_url: str, api_key: str) -> dict:
        """Test connection to an arr instance.

        Args:
            app: One of sonarr, radarr, lidarr, readarr, whisparr
            api_url: The URL of the arr instance (e.g., http://sonarr:8989)
            api_key: The API key for the arr instance
        """
        if app not in HUNTARR_APPS:
            return {"error": f"Invalid app. Must be one of: {HUNTARR_APPS}"}
        try:
            result = await huntarr_request(
                f"{app}/test-connection",
                "POST",
                {"api_url": api_url, "api_key": api_key}
            )
            return result
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def huntarr_get_schedules() -> dict:
        """Get all Huntarr schedules."""
        try:
            return await huntarr_request("api/scheduler/load")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def huntarr_save_schedules(schedules: dict) -> dict:
        """Save Huntarr schedules.

        Args:
            schedules: Dictionary of schedules by app (e.g., {"sonarr": [...], "radarr": [...]})
        """
        try:
            result = await huntarr_request("api/scheduler/save", "POST", schedules)
            return {"success": True, "message": "Schedules saved", "result": result}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def huntarr_get_history() -> dict:
        """Get Huntarr scheduler execution history."""
        try:
            return await huntarr_request("api/scheduler/history")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def huntarr_get_stats(app: str = "sonarr") -> dict:
        """Get Huntarr statistics for a specific app (missing items, upgrades found, etc).

        Args:
            app: One of sonarr, radarr, lidarr, readarr, whisparr
        """
        if app not in HUNTARR_APPS:
            return {"error": f"Invalid app. Must be one of: {HUNTARR_APPS}"}
        try:
            return await huntarr_request(f"api/{app}/stats")
        except Exception as e:
            return {"error": str(e)}
