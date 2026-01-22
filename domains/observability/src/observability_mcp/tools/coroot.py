"""Coroot observability tools."""

import os
import json
import logging
from typing import Dict, Any, Optional, List

import httpx
from fastmcp import FastMCP
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Configuration
COROOT_URL = os.environ.get("COROOT_URL", "http://coroot.monitoring.svc:8080")
COROOT_PROJECT = os.environ.get("COROOT_PROJECT", "default")


class ServiceInput(BaseModel):
    service: str = Field(description="Service name")
    namespace: str = Field(default="ai-platform", description="Kubernetes namespace")


class TimeRangeInput(BaseModel):
    hours: int = Field(default=24, description="Time range in hours")
    severity: Optional[str] = Field(default=None, description="Filter: critical, warning, info")


async def _coroot_api(endpoint: str) -> Dict[str, Any]:
    """Make request to Coroot API."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        url = f"{COROOT_URL}/api{endpoint}"
        response = await client.get(url)
        response.raise_for_status()
        return response.json() if response.text else {}


def _handle_error(e: Exception) -> str:
    """Format error message."""
    if isinstance(e, httpx.HTTPStatusError):
        return f"Error: Coroot API returned status {e.response.status_code}"
    return f"Error: {type(e).__name__}: {str(e)}"


def register_tools(mcp: FastMCP):
    """Register Coroot tools with the MCP server."""

    @mcp.tool(name="coroot_get_service_metrics")
    async def coroot_get_service_metrics(params: ServiceInput) -> str:
        """Get metrics for a specific service (CPU, memory, latency, error rate)."""
        try:
            result = await _coroot_api(f"/project/{COROOT_PROJECT}/app/{params.namespace}:{params.service}")
            return json.dumps(result, indent=2)
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="coroot_get_recent_anomalies")
    async def coroot_get_recent_anomalies(params: TimeRangeInput) -> str:
        """Get recent anomalies detected by Coroot (critical, warning, info)."""
        try:
            result = await _coroot_api(f"/project/{COROOT_PROJECT}/incidents")
            incidents = result if isinstance(result, list) else result.get("incidents", [])

            if params.severity:
                incidents = [i for i in incidents if i.get("severity", "").lower() == params.severity.lower()]

            lines = [f"# Recent Anomalies ({len(incidents)})", ""]
            for inc in incidents[:20]:
                sev = inc.get("severity", "?")
                app = inc.get("application", "?")
                msg = inc.get("message", "No description")
                lines.append(f"- [{sev.upper()}] **{app}**: {msg}")

            return "\n".join(lines) if lines[2:] else "No anomalies detected"
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="coroot_get_service_dependencies")
    async def coroot_get_service_dependencies(params: ServiceInput) -> str:
        """Get upstream and downstream service dependencies."""
        try:
            result = await _coroot_api(f"/project/{COROOT_PROJECT}/app/{params.namespace}:{params.service}/map")
            return json.dumps(result, indent=2)
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="coroot_get_alerts")
    async def coroot_get_alerts(status: str = "firing") -> str:
        """Get current alerts from Coroot (firing, resolved, all)."""
        try:
            result = await _coroot_api(f"/project/{COROOT_PROJECT}/alerts")
            alerts = result if isinstance(result, list) else result.get("alerts", [])

            if status != "all":
                alerts = [a for a in alerts if a.get("status", "").lower() == status.lower()]

            lines = [f"# Coroot Alerts ({len(alerts)})", ""]
            for alert in alerts[:20]:
                name = alert.get("name", "Unknown")
                sev = alert.get("severity", "?")
                lines.append(f"- [{sev.upper()}] {name}")

            return "\n".join(lines) if lines[2:] else f"No {status} alerts"
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="coroot_get_infrastructure_overview")
    async def coroot_get_infrastructure_overview() -> str:
        """Get overview of all services and their health status."""
        try:
            result = await _coroot_api(f"/project/{COROOT_PROJECT}/overview")
            return json.dumps(result, indent=2)
        except Exception as e:
            return _handle_error(e)
