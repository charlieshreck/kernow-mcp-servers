"""AlertManager tools."""

import os
import json
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

import httpx
from fastmcp import FastMCP
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Configuration
ALERTMANAGER_URL = os.environ.get("ALERTMANAGER_URL", "http://alertmanager.monit.kernow.io")


class AlertsFilterInput(BaseModel):
    active: bool = Field(default=True, description="Include active alerts")
    silenced: bool = Field(default=False, description="Include silenced alerts")
    inhibited: bool = Field(default=False, description="Include inhibited alerts")


class SilenceInput(BaseModel):
    alertname: str = Field(description="Alert name to silence")
    duration_hours: int = Field(default=2, description="How long to silence")
    comment: str = Field(default="Created via observability-mcp", description="Reason for silence")
    matcher_type: str = Field(default="=", description="Match type: '=', '=~', '!='")


class SilenceIdInput(BaseModel):
    silence_id: str = Field(description="The silence UUID to delete")


async def _am_api(endpoint: str, method: str = "GET", data: dict = None) -> Dict[str, Any]:
    """Make request to AlertManager API."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        url = f"{ALERTMANAGER_URL}/api/v2{endpoint}"

        if method == "GET":
            response = await client.get(url)
        elif method == "POST":
            response = await client.post(url, json=data)
        elif method == "DELETE":
            response = await client.delete(url)
        else:
            raise ValueError(f"Unsupported method: {method}")

        response.raise_for_status()
        return response.json() if response.text else {"status": "success"}


def _handle_error(e: Exception) -> str:
    """Format error message."""
    if isinstance(e, httpx.HTTPStatusError):
        return f"Error: AlertManager returned status {e.response.status_code}"
    return f"Error: {type(e).__name__}: {str(e)}"


def register_tools(mcp: FastMCP):
    """Register AlertManager tools with the MCP server."""

    @mcp.tool(name="list_alerts")
    async def list_alerts(params: AlertsFilterInput) -> str:
        """List current AlertManager alerts."""
        try:
            filters = []
            if params.active:
                filters.append("active=true")
            if params.silenced:
                filters.append("silenced=true")
            if params.inhibited:
                filters.append("inhibited=true")

            query = "&".join(filters) if filters else ""
            result = await _am_api(f"/alerts?{query}")
            alerts = result if isinstance(result, list) else []

            lines = [f"# AlertManager Alerts ({len(alerts)})", ""]
            for alert in alerts[:20]:
                labels = alert.get("labels", {})
                name = labels.get("alertname", "Unknown")
                severity = labels.get("severity", "?")
                status = alert.get("status", {}).get("state", "?")
                lines.append(f"- [{severity.upper()}] **{name}** ({status})")

            return "\n".join(lines) if lines[2:] else "No alerts matching filter"
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="create_silence")
    async def create_silence(params: SilenceInput) -> str:
        """Create a silence for matching alerts."""
        try:
            now = datetime.utcnow()
            ends = now + timedelta(hours=params.duration_hours)

            data = {
                "matchers": [{
                    "name": "alertname",
                    "value": params.alertname,
                    "isRegex": params.matcher_type == "=~",
                    "isEqual": params.matcher_type != "!="
                }],
                "startsAt": now.isoformat() + "Z",
                "endsAt": ends.isoformat() + "Z",
                "createdBy": "observability-mcp",
                "comment": params.comment
            }

            result = await _am_api("/silences", method="POST", data=data)
            silence_id = result.get("silenceID", "unknown")
            return f"[OK] Created silence {silence_id} for '{params.alertname}' until {ends.isoformat()}"
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="delete_silence")
    async def delete_silence(params: SilenceIdInput) -> str:
        """Delete/expire a silence by ID."""
        try:
            await _am_api(f"/silence/{params.silence_id}", method="DELETE")
            return f"[OK] Deleted silence {params.silence_id}"
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="list_silences")
    async def list_silences() -> str:
        """List all active and pending silences."""
        try:
            result = await _am_api("/silences")
            silences = result if isinstance(result, list) else []

            # Filter to active/pending only
            active = [s for s in silences if s.get("status", {}).get("state") in ["active", "pending"]]

            lines = [f"# Active Silences ({len(active)})", ""]
            for s in active:
                matchers = s.get("matchers", [])
                matcher_str = ", ".join(f"{m.get('name')}={m.get('value')}" for m in matchers)
                ends = s.get("endsAt", "?")
                lines.append(f"- **{s.get('id', '?')[:8]}...**: {matcher_str} (until {ends})")

            return "\n".join(lines) if lines[2:] else "No active silences"
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="get_alertmanager_status")
    async def get_alertmanager_status() -> str:
        """Get AlertManager cluster and configuration status."""
        try:
            result = await _am_api("/status")
            return json.dumps(result, indent=2)
        except Exception as e:
            return _handle_error(e)
