"""Recommendarr AI-powered media recommendation tools."""

import os
import logging
from typing import List

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
RECOMMENDARR_URL = os.environ.get("RECOMMENDARR_URL", "https://recomendarr.kernow.io")


async def recommendarr_request(endpoint: str, method: str = "GET", data: dict = None) -> dict:
    """Make request to Recommendarr API."""
    async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
        url = f"{RECOMMENDARR_URL}/{endpoint.lstrip('/')}"
        response = await client.request(method, url, json=data)
        response.raise_for_status()
        return response.json() if response.content else {}


async def get_status() -> dict:
    """Get Recommendarr status for health checks."""
    try:
        return await recommendarr_request("api/health")
    except Exception as e:
        return {"error": str(e)}


def register_tools(mcp: FastMCP):
    """Register Recommendarr tools with the MCP server."""

    @mcp.tool()
    async def recommendarr_get_health() -> dict:
        """Get Recommendarr health status."""
        try:
            return await recommendarr_request("api/health")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def recommendarr_get_movie_recommendations(count: int = 10) -> List[dict]:
        """Get AI-powered movie recommendations based on your library.

        Note: Requires Recommendarr to be configured with Radarr and an AI service.
        """
        try:
            result = await recommendarr_request("api/recommendations/movies", "POST", {
                "count": count
            })
            return result if isinstance(result, list) else [result]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def recommendarr_get_tv_recommendations(count: int = 10) -> List[dict]:
        """Get AI-powered TV show recommendations based on your library.

        Note: Requires Recommendarr to be configured with Sonarr and an AI service.
        """
        try:
            result = await recommendarr_request("api/recommendations/tv", "POST", {
                "count": count
            })
            return result if isinstance(result, list) else [result]
        except Exception as e:
            return [{"error": str(e)}]
