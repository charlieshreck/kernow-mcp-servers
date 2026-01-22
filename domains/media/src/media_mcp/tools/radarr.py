"""Radarr movie management tools."""

import os
import logging
from typing import List

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
RADARR_URL = os.environ.get("RADARR_URL", "https://radarr.kernow.io")
RADARR_API_KEY = os.environ.get("RADARR_API_KEY", "")


async def arr_request(endpoint: str, method: str = "GET", data: dict = None) -> dict:
    """Make request to Radarr API (v3)."""
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        headers = {"X-Api-Key": RADARR_API_KEY}
        url = f"{RADARR_URL}/api/v3/{endpoint}"
        response = await client.request(method, url, headers=headers, json=data)
        response.raise_for_status()
        return response.json() if response.content else {}


async def get_system_status() -> dict:
    """Get Radarr system status for health checks."""
    try:
        return await arr_request("system/status")
    except Exception as e:
        return {"error": str(e)}


def register_tools(mcp: FastMCP):
    """Register Radarr tools with the MCP server."""

    @mcp.tool()
    async def radarr_list_movies(monitored_only: bool = True) -> List[dict]:
        """List all movies in Radarr."""
        try:
            movies = await arr_request("movie")
            result = []
            for m in movies:
                if monitored_only and not m.get("monitored"):
                    continue
                result.append({
                    "id": m["id"],
                    "title": m["title"],
                    "year": m.get("year"),
                    "status": "downloaded" if m.get("hasFile") else "missing",
                    "monitored": m.get("monitored"),
                    "quality": m.get("movieFile", {}).get("quality", {}).get("quality", {}).get("name") if m.get("hasFile") else None
                })
            return result
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def radarr_search_movie(query: str) -> List[dict]:
        """Search for a movie to add."""
        try:
            results = await arr_request(f"movie/lookup?term={query}")
            return [{"tmdbId": r.get("tmdbId"), "title": r["title"], "year": r.get("year"),
                     "overview": r.get("overview", "")[:200]} for r in results[:10]]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def radarr_add_movie(tmdb_id: int, quality_profile_id: int = 1,
                                root_folder_path: str = "/movies") -> dict:
        """Add a movie to Radarr by TMDB ID."""
        try:
            lookup = await arr_request(f"movie/lookup/tmdb?tmdbId={tmdb_id}")
            if not lookup:
                return {"error": "Movie not found"}

            movie = lookup if isinstance(lookup, dict) else lookup[0]
            movie["qualityProfileId"] = quality_profile_id
            movie["rootFolderPath"] = root_folder_path
            movie["monitored"] = True
            movie["addOptions"] = {"searchForMovie": True}

            result = await arr_request("movie", "POST", movie)
            return {"success": True, "id": result.get("id"), "title": result.get("title")}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def radarr_get_queue() -> List[dict]:
        """Get Radarr download queue."""
        try:
            queue = await arr_request("queue?pageSize=50")
            return [{
                "title": item.get("title"),
                "movie": item.get("movie", {}).get("title"),
                "status": item.get("status"),
                "sizeleft": item.get("sizeleft", 0),
                "timeleft": item.get("timeleft", "unknown"),
                "quality": item.get("quality", {}).get("quality", {}).get("name")
            } for item in queue.get("records", [])]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def radarr_trigger_search(movie_id: int) -> dict:
        """Trigger a search for a movie."""
        try:
            await arr_request("command", "POST", {"name": "MoviesSearch", "movieIds": [movie_id]})
            return {"success": True, "message": f"Search triggered for movie {movie_id}"}
        except Exception as e:
            return {"error": str(e)}
