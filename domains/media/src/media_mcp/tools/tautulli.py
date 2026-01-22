"""Tautulli Plex statistics tools."""

import os
import logging
from typing import List

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
TAUTULLI_URL = os.environ.get("TAUTULLI_URL", "https://tautulli.kernow.io")
TAUTULLI_API_KEY = os.environ.get("TAUTULLI_API_KEY", "")


async def tautulli_request(cmd: str, **params) -> dict:
    """Make request to Tautulli API."""
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        params["apikey"] = TAUTULLI_API_KEY
        params["cmd"] = cmd
        url = f"{TAUTULLI_URL}/api/v2"
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json().get("response", {}).get("data", {})


async def get_activity() -> dict:
    """Get Plex activity for health checks."""
    try:
        activity = await tautulli_request("get_activity")
        sessions = []
        for s in activity.get("sessions", []):
            sessions.append({
                "user": s.get("friendly_name"),
                "title": s.get("full_title"),
                "state": s.get("state"),
                "progress": s.get("progress_percent"),
                "quality": s.get("quality_profile"),
                "player": s.get("player")
            })
        return {
            "stream_count": activity.get("stream_count", 0),
            "sessions": sessions
        }
    except Exception as e:
        return {"error": str(e)}


def register_tools(mcp: FastMCP):
    """Register Tautulli tools with the MCP server."""

    @mcp.tool()
    async def tautulli_get_activity() -> dict:
        """Get current Plex streaming activity."""
        return await get_activity()

    @mcp.tool()
    async def tautulli_get_history(length: int = 10) -> List[dict]:
        """Get recent watch history."""
        try:
            history = await tautulli_request("get_history", length=length)
            return [{
                "user": h.get("friendly_name"),
                "title": h.get("full_title"),
                "watched_at": h.get("date"),
                "duration": h.get("duration"),
                "percent_complete": h.get("percent_complete")
            } for h in history.get("data", [])]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def tautulli_get_most_watched(time_range: int = 30) -> dict:
        """Get most watched content in the last N days."""
        try:
            movies = await tautulli_request("get_home_stats", stat_id="top_movies", time_range=time_range)
            shows = await tautulli_request("get_home_stats", stat_id="top_tv", time_range=time_range)
            return {
                "movies": [{"title": m.get("title"), "plays": m.get("total_plays")}
                          for m in movies.get("rows", [])[:5]],
                "shows": [{"title": s.get("title"), "plays": s.get("total_plays")}
                         for s in shows.get("rows", [])[:5]]
            }
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def tautulli_get_library_stats() -> dict:
        """Get library statistics."""
        try:
            stats = await tautulli_request("get_libraries")
            return [{
                "name": lib.get("section_name"),
                "type": lib.get("section_type"),
                "count": lib.get("count"),
                "parent_count": lib.get("parent_count"),
                "child_count": lib.get("child_count")
            } for lib in stats]
        except Exception as e:
            return {"error": str(e)}
