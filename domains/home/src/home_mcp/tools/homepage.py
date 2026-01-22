"""Homepage dashboard tools."""

import os
import logging
from typing import Dict, Any

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
HOMEPAGE_HOST = os.environ.get("HOMEPAGE_HOST", "http://homepage.default.svc:3000")


async def get_status() -> dict:
    """Get Homepage status for health checks."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{HOMEPAGE_HOST}/api/services")
            if response.status_code == 200:
                return {"status": "healthy"}
            return {"status": "unhealthy", "code": response.status_code}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


def register_tools(mcp: FastMCP):
    """Register Homepage tools with the MCP server."""

    @mcp.tool()
    async def list_services() -> Dict[str, Any]:
        """List all configured services from Homepage."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{HOMEPAGE_HOST}/api/services")
                response.raise_for_status()
                return response.json()
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def list_bookmarks() -> Dict[str, Any]:
        """List all configured bookmarks."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{HOMEPAGE_HOST}/api/bookmarks")
                response.raise_for_status()
                return response.json()
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def get_widgets() -> Dict[str, Any]:
        """Get widget data (resources, weather, etc.)."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{HOMEPAGE_HOST}/api/widgets")
                response.raise_for_status()
                return response.json()
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def check_service_health(service_url: str) -> Dict[str, Any]:
        """Check if a service is reachable."""
        try:
            async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
                response = await client.get(service_url)
                return {
                    "url": service_url,
                    "status": response.status_code,
                    "healthy": response.status_code < 400
                }
        except Exception as e:
            return {"url": service_url, "status": "error", "healthy": False, "error": str(e)}

    @mcp.tool()
    async def get_service_status_summary() -> Dict[str, Any]:
        """Get aggregated status of all services."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{HOMEPAGE_HOST}/api/services")
                response.raise_for_status()
                services = response.json()

            summary = {"total": 0, "healthy": 0, "unhealthy": 0, "unknown": 0, "services": []}

            for group_name, group_services in services.items():
                for service in group_services:
                    summary["total"] += 1
                    service_info = {"name": service.get("name"), "group": group_name}

                    if "href" in service:
                        try:
                            async with httpx.AsyncClient(timeout=5.0, verify=False) as client:
                                resp = await client.get(service["href"])
                                if resp.status_code < 400:
                                    service_info["status"] = "healthy"
                                    summary["healthy"] += 1
                                else:
                                    service_info["status"] = "unhealthy"
                                    summary["unhealthy"] += 1
                        except:
                            service_info["status"] = "unhealthy"
                            summary["unhealthy"] += 1
                    else:
                        service_info["status"] = "unknown"
                        summary["unknown"] += 1

                    summary["services"].append(service_info)

            return summary
        except Exception as e:
            return {"error": str(e)}
