"""Sonarr TV show management tools."""

import os
import logging
from typing import List

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
SONARR_URL = os.environ.get("SONARR_URL", "https://sonarr.kernow.io")
SONARR_API_KEY = os.environ.get("SONARR_API_KEY", "")


async def arr_request(endpoint: str, method: str = "GET", data: dict = None) -> dict:
    """Make request to Sonarr API (v3)."""
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        headers = {"X-Api-Key": SONARR_API_KEY}
        url = f"{SONARR_URL}/api/v3/{endpoint}"
        response = await client.request(method, url, headers=headers, json=data)
        response.raise_for_status()
        return response.json() if response.content else {}


async def get_system_status() -> dict:
    """Get Sonarr system status for health checks."""
    try:
        return await arr_request("system/status")
    except Exception as e:
        return {"error": str(e)}


def register_tools(mcp: FastMCP):
    """Register Sonarr tools with the MCP server."""

    @mcp.tool()
    async def sonarr_list_series(monitored_only: bool = True) -> List[dict]:
        """List all TV series in Sonarr."""
        try:
            series = await arr_request("series")
            result = []
            for s in series:
                if monitored_only and not s.get("monitored"):
                    continue
                result.append({
                    "id": s["id"],
                    "title": s["title"],
                    "year": s.get("year"),
                    "status": s.get("status"),
                    "monitored": s.get("monitored"),
                    "episodeCount": s.get("statistics", {}).get("episodeCount", 0),
                    "episodeFileCount": s.get("statistics", {}).get("episodeFileCount", 0),
                    "percentComplete": s.get("statistics", {}).get("percentOfEpisodes", 0)
                })
            return result
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def sonarr_search_series(query: str) -> List[dict]:
        """Search for a TV series to add."""
        try:
            results = await arr_request(f"series/lookup?term={query}")
            return [{"tvdbId": r.get("tvdbId"), "title": r["title"], "year": r.get("year"),
                     "overview": r.get("overview", "")[:200]} for r in results[:10]]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def sonarr_add_series(tvdb_id: int, quality_profile_id: int = 1,
                                 root_folder_path: str = "/tv") -> dict:
        """Add a TV series to Sonarr by TVDB ID."""
        try:
            lookup = await arr_request(f"series/lookup?term=tvdb:{tvdb_id}")
            if not lookup:
                return {"error": "Series not found"}

            series = lookup[0]
            series["qualityProfileId"] = quality_profile_id
            series["rootFolderPath"] = root_folder_path
            series["monitored"] = True
            series["addOptions"] = {"searchForMissingEpisodes": True}

            result = await arr_request("series", "POST", series)
            return {"success": True, "id": result.get("id"), "title": result.get("title")}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def sonarr_get_queue() -> List[dict]:
        """Get Sonarr download queue."""
        try:
            queue = await arr_request("queue?pageSize=50")
            return [{
                "title": item.get("title"),
                "series": item.get("series", {}).get("title"),
                "status": item.get("status"),
                "sizeleft": item.get("sizeleft", 0),
                "timeleft": item.get("timeleft", "unknown"),
                "quality": item.get("quality", {}).get("quality", {}).get("name")
            } for item in queue.get("records", [])]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def sonarr_trigger_search(series_id: int) -> dict:
        """Trigger a search for missing episodes of a series."""
        try:
            await arr_request("command", "POST", {"name": "SeriesSearch", "seriesId": series_id})
            return {"success": True, "message": f"Search triggered for series {series_id}"}
        except Exception as e:
            return {"error": str(e)}
