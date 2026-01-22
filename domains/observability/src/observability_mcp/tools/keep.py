"""Keep alert aggregation tools."""

import os
import json
import logging
from typing import Dict, Any, Optional, List
from enum import Enum

import httpx
from fastmcp import FastMCP
from pydantic import BaseModel, Field, ConfigDict

logger = logging.getLogger(__name__)

# Configuration
KEEP_URL = os.environ.get("KEEP_URL", "http://keep.keep.svc.cluster.local:8080")
KEEP_API_KEY = os.environ.get("KEEP_API_KEY", "")


class ResponseFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"


class BaseInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")


class AlertsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="Output format")
    limit: int = Field(default=20, ge=1, le=100, description="Max alerts to return")
    severity: Optional[str] = Field(default=None, description="Filter by severity: critical, warning, info")
    status: Optional[str] = Field(default="firing", description="Filter by status: firing, resolved, acknowledged")
    summary_only: bool = Field(default=False, description="Return counts and summary only")


class AlertIdInput(BaseModel):
    alert_id: str = Field(description="Alert ID (fingerprint)")


class IncidentsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)
    limit: int = Field(default=20, ge=1, le=100)
    status: Optional[str] = Field(default="open", description="Filter: open, acknowledged, resolved")


class IncidentIdInput(BaseModel):
    incident_id: str = Field(description="Incident ID")


def _get_headers() -> Dict[str, str]:
    """Get headers for Keep API requests."""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if KEEP_API_KEY:
        headers["X-API-KEY"] = KEEP_API_KEY
    return headers


async def _keep_api(endpoint: str, method: str = "GET", data: dict = None) -> Dict[str, Any]:
    """Make authenticated request to Keep API."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        url = f"{KEEP_URL}{endpoint}"
        headers = _get_headers()

        if method == "GET":
            response = await client.get(url, headers=headers)
        elif method == "POST":
            response = await client.post(url, headers=headers, json=data)
        elif method == "DELETE":
            response = await client.delete(url, headers=headers)
        else:
            raise ValueError(f"Unsupported method: {method}")

        response.raise_for_status()
        return response.json() if response.text else {"status": "success"}


def _handle_error(e: Exception) -> str:
    """Format error message."""
    if isinstance(e, httpx.HTTPStatusError):
        return f"Error: Keep API returned status {e.response.status_code}"
    return f"Error: {type(e).__name__}: {str(e)}"


def register_tools(mcp: FastMCP):
    """Register Keep tools with the MCP server."""

    @mcp.tool(name="keep_list_alerts")
    async def keep_list_alerts(params: AlertsInput) -> str:
        """List alerts with optional filters. By default shows only firing alerts (limit 20)."""
        try:
            result = await _keep_api("/alerts")
            alerts = result if isinstance(result, list) else result.get("alerts", [])

            # Filter by status
            if params.status:
                alerts = [a for a in alerts if a.get("status", "").lower() == params.status.lower()]

            # Filter by severity
            if params.severity:
                alerts = [a for a in alerts if a.get("severity", "").lower() == params.severity.lower()]

            # Limit results
            alerts = alerts[:params.limit]

            if params.summary_only:
                return json.dumps({
                    "total": len(alerts),
                    "by_severity": {sev: len([a for a in alerts if a.get("severity") == sev])
                                    for sev in ["critical", "warning", "info"]}
                }, indent=2)

            if params.response_format == ResponseFormat.JSON:
                return json.dumps({"alerts": alerts, "count": len(alerts)}, indent=2)

            lines = [f"# Alerts ({len(alerts)})", ""]
            for a in alerts:
                name = a.get("name", a.get("alertname", "Unknown"))
                severity = a.get("severity", "?")
                status = a.get("status", "?")
                lines.append(f"- [{severity.upper()}] **{name}** ({status})")

            return "\n".join(lines)
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="keep_get_alert")
    async def keep_get_alert(params: AlertIdInput) -> str:
        """Get details of a specific alert."""
        try:
            result = await _keep_api(f"/alerts/{params.alert_id}")
            return json.dumps(result, indent=2)
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="keep_acknowledge_alert")
    async def keep_acknowledge_alert(params: AlertIdInput) -> str:
        """Acknowledge an alert."""
        try:
            await _keep_api(f"/alerts/{params.alert_id}/acknowledge", method="POST")
            return f"[OK] Acknowledged alert {params.alert_id}"
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="keep_resolve_alert")
    async def keep_resolve_alert(params: AlertIdInput) -> str:
        """Resolve an alert."""
        try:
            await _keep_api(f"/alerts/{params.alert_id}/resolve", method="POST")
            return f"[OK] Resolved alert {params.alert_id}"
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="keep_list_incidents")
    async def keep_list_incidents(params: IncidentsInput) -> str:
        """List incidents (correlated groups of alerts)."""
        try:
            result = await _keep_api("/incidents")
            incidents = result if isinstance(result, list) else result.get("incidents", [])

            if params.status:
                incidents = [i for i in incidents if i.get("status", "").lower() == params.status.lower()]

            incidents = incidents[:params.limit]

            if params.response_format == ResponseFormat.JSON:
                return json.dumps({"incidents": incidents, "count": len(incidents)}, indent=2)

            lines = [f"# Incidents ({len(incidents)})", ""]
            for inc in incidents:
                name = inc.get("name", inc.get("id", "Unknown"))
                status = inc.get("status", "?")
                alert_count = len(inc.get("alerts", []))
                lines.append(f"- [{status.upper()}] **{name}** ({alert_count} alerts)")

            return "\n".join(lines)
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="keep_get_incident")
    async def keep_get_incident(params: IncidentIdInput) -> str:
        """Get details of a specific incident including correlated alerts."""
        try:
            result = await _keep_api(f"/incidents/{params.incident_id}")
            return json.dumps(result, indent=2)
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="keep_acknowledge_incident")
    async def keep_acknowledge_incident(params: IncidentIdInput) -> str:
        """Acknowledge an incident."""
        try:
            await _keep_api(f"/incidents/{params.incident_id}/acknowledge", method="POST")
            return f"[OK] Acknowledged incident {params.incident_id}"
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="keep_resolve_incident")
    async def keep_resolve_incident(params: IncidentIdInput) -> str:
        """Resolve an incident."""
        try:
            await _keep_api(f"/incidents/{params.incident_id}/resolve", method="POST")
            return f"[OK] Resolved incident {params.incident_id}"
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="keep_health")
    async def keep_health(params: BaseInput) -> str:
        """Check Keep server health and connectivity."""
        try:
            # Try to hit the API
            await _keep_api("/providers")
            return "[OK] Keep is healthy and accessible"
        except Exception as e:
            return _handle_error(e)
