"""Gatus endpoint health monitoring tools."""

import os
import json
import logging
from typing import Dict, Any, List

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
GATUS_URL = os.environ.get("GATUS_URL", "http://10.30.0.20:30086")


async def _gatus_api(endpoint: str) -> Dict[str, Any]:
    """Make request to Gatus API."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        url = f"{GATUS_URL}/api/v1{endpoint}"
        response = await client.get(url)
        response.raise_for_status()
        return response.json() if response.text else {}


def _handle_error(e: Exception) -> str:
    """Format error message."""
    if isinstance(e, httpx.HTTPStatusError):
        return f"Error: Gatus returned status {e.response.status_code}"
    return f"Error: {type(e).__name__}: {str(e)}"


def register_tools(mcp: FastMCP):
    """Register Gatus tools with the MCP server."""

    @mcp.tool(name="gatus_get_endpoint_status")
    async def gatus_get_endpoint_status() -> str:
        """Get health status of all Gatus-monitored endpoints."""
        try:
            result = await _gatus_api("/endpoints/statuses")
            endpoints = result if isinstance(result, list) else []

            lines = ["# Endpoint Health Status", ""]

            # Group by health
            healthy = [e for e in endpoints if e.get("results", [{}])[-1].get("success", False)]
            unhealthy = [e for e in endpoints if not e.get("results", [{}])[-1].get("success", True)]

            lines.append(f"**Healthy**: {len(healthy)} | **Unhealthy**: {len(unhealthy)}")
            lines.append("")

            if unhealthy:
                lines.append("## Unhealthy Endpoints")
                for e in unhealthy:
                    name = e.get("name", "Unknown")
                    group = e.get("group", "default")
                    lines.append(f"- [!!] **{group}/{name}**")
                lines.append("")

            lines.append("## All Endpoints")
            for e in endpoints[:30]:
                name = e.get("name", "Unknown")
                group = e.get("group", "default")
                results = e.get("results", [{}])
                success = results[-1].get("success", False) if results else False
                icon = "[OK]" if success else "[!!]"
                lines.append(f"- {icon} **{group}/{name}**")

            return "\n".join(lines)
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="gatus_get_failing_endpoints")
    async def gatus_get_failing_endpoints() -> str:
        """Get only endpoints that are currently failing."""
        try:
            result = await _gatus_api("/endpoints/statuses")
            endpoints = result if isinstance(result, list) else []

            unhealthy = []
            for e in endpoints:
                results = e.get("results", [])
                if results and not results[-1].get("success", True):
                    unhealthy.append(e)

            if not unhealthy:
                return "[OK] All endpoints are healthy!"

            lines = [f"# Failing Endpoints ({len(unhealthy)})", ""]
            for e in unhealthy:
                name = e.get("name", "Unknown")
                group = e.get("group", "default")
                results = e.get("results", [{}])
                last_result = results[-1] if results else {}

                status = last_result.get("status", "?")
                duration = last_result.get("duration", 0)

                lines.append(f"## {group}/{name}")
                lines.append(f"- Status: {status}")
                lines.append(f"- Duration: {duration}ms")
                if last_result.get("errors"):
                    lines.append(f"- Errors: {', '.join(last_result['errors'][:3])}")
                lines.append("")

            return "\n".join(lines)
        except Exception as e:
            return _handle_error(e)
