"""Grafana dashboard and annotation tools."""

import os
import json
import logging
from typing import Dict, Any, Optional, List

import httpx
from fastmcp import FastMCP
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Configuration
GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://10.30.0.20:30081")
GRAFANA_USER = os.environ.get("GRAFANA_USER", "admin")
GRAFANA_PASSWORD = os.environ.get("GRAFANA_PASSWORD", "")


class SearchInput(BaseModel):
    search: str = Field(default="", description="Optional search term")


class DashboardInput(BaseModel):
    uid_or_title: str = Field(description="Dashboard UID or title to search for")


class AnnotationInput(BaseModel):
    text: str = Field(description="Annotation text/description")
    tags: List[str] = Field(default=[], description="List of tags")
    dashboard_uid: Optional[str] = Field(default=None, description="Optional dashboard UID")
    panel_id: Optional[int] = Field(default=None, description="Optional panel ID")


async def _grafana_api(endpoint: str, method: str = "GET", data: dict = None) -> Dict[str, Any]:
    """Make authenticated request to Grafana API."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        url = f"{GRAFANA_URL}/api{endpoint}"
        auth = (GRAFANA_USER, GRAFANA_PASSWORD) if GRAFANA_PASSWORD else None

        if method == "GET":
            response = await client.get(url, auth=auth)
        elif method == "POST":
            response = await client.post(url, auth=auth, json=data)
        else:
            raise ValueError(f"Unsupported method: {method}")

        response.raise_for_status()
        return response.json() if response.text else {"status": "success"}


def _handle_error(e: Exception) -> str:
    """Format error message."""
    if isinstance(e, httpx.HTTPStatusError):
        return f"Error: Grafana returned status {e.response.status_code}"
    return f"Error: {type(e).__name__}: {str(e)}"


def register_tools(mcp: FastMCP):
    """Register Grafana tools with the MCP server."""

    @mcp.tool(name="grafana_list_dashboards")
    async def grafana_list_dashboards(params: SearchInput) -> str:
        """List available Grafana dashboards."""
        try:
            query = f"?query={params.search}" if params.search else ""
            result = await _grafana_api(f"/search{query}")
            dashboards = result if isinstance(result, list) else []

            lines = [f"# Grafana Dashboards ({len(dashboards)})", ""]
            for d in dashboards[:30]:
                uid = d.get("uid", "?")
                title = d.get("title", "Untitled")
                folder = d.get("folderTitle", "General")
                lines.append(f"- **{title}** (uid: {uid}, folder: {folder})")

            return "\n".join(lines)
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="grafana_get_dashboard_url")
    async def grafana_get_dashboard_url(params: DashboardInput) -> str:
        """Get direct URL to a dashboard by UID or title."""
        try:
            # Search for dashboard
            result = await _grafana_api(f"/search?query={params.uid_or_title}")
            dashboards = result if isinstance(result, list) else []

            if not dashboards:
                return f"No dashboard found matching '{params.uid_or_title}'"

            d = dashboards[0]
            uid = d.get("uid", "?")
            url = f"{GRAFANA_URL}/d/{uid}"

            return f"Dashboard: **{d.get('title')}**\nURL: {url}"
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="grafana_create_annotation")
    async def grafana_create_annotation(params: AnnotationInput) -> str:
        """Create annotation in Grafana (marks events on graphs)."""
        try:
            data = {
                "text": params.text,
                "tags": params.tags
            }
            if params.dashboard_uid:
                data["dashboardUID"] = params.dashboard_uid
            if params.panel_id:
                data["panelId"] = params.panel_id

            result = await _grafana_api("/annotations", method="POST", data=data)
            ann_id = result.get("id", "unknown")
            return f"[OK] Created annotation {ann_id}: {params.text}"
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="grafana_list_datasources")
    async def grafana_list_datasources() -> str:
        """List configured Grafana datasources."""
        try:
            result = await _grafana_api("/datasources")
            datasources = result if isinstance(result, list) else []

            lines = [f"# Grafana Datasources ({len(datasources)})", ""]
            for ds in datasources:
                name = ds.get("name", "?")
                dtype = ds.get("type", "?")
                is_default = "[*]" if ds.get("isDefault") else "[ ]"
                lines.append(f"- {is_default} **{name}** ({dtype})")

            return "\n".join(lines)
        except Exception as e:
            return _handle_error(e)
