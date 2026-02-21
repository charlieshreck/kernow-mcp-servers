"""Coroot observability tools."""

import os
import json
import logging
from typing import Dict, Any, Optional

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
COROOT_URL = os.environ.get("COROOT_URL", "http://coroot.monit.kernow.io")
COROOT_PROJECT_NAME = os.environ.get("COROOT_PROJECT", "all-clusters")

# Cache for project name -> ID mapping
_PROJECT_ID_CACHE: Dict[str, str] = {}


async def _get_project_id(project_name: str) -> str:
    """Resolve project name to internal ID. Coroot API uses IDs not names."""
    if project_name in _PROJECT_ID_CACHE:
        return _PROJECT_ID_CACHE[project_name]
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{COROOT_URL}/api/user")
            response.raise_for_status()
            data = response.json()
            for proj in data.get("projects", []):
                _PROJECT_ID_CACHE[proj["name"]] = proj["id"]
            return _PROJECT_ID_CACHE.get(project_name, project_name)
    except Exception as e:
        logger.error(f"Failed to get project ID: {e}")
        return project_name  # Fallback to name


async def _coroot_api(endpoint: str, method: str = "GET", **kwargs) -> Dict[str, Any]:
    """Make request to Coroot API with project ID resolution."""
    project_id = await _get_project_id(COROOT_PROJECT_NAME)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            url = f"{COROOT_URL}/api/project/{project_id}/{endpoint}"
            logger.info(f"Coroot request: {method} {url}")
            response = await client.request(method, url, **kwargs)
            response.raise_for_status()
            # Handle empty responses gracefully
            text = response.text.strip() if response.text else ""
            if not text:
                logger.debug(f"Coroot API returned empty response for {endpoint}")
                return {}
            return json.loads(text)
    except httpx.HTTPStatusError as e:
        logger.error(f"Coroot API error: {e.response.status_code} for {endpoint}")
        raise
    except httpx.TimeoutException:
        logger.error(f"Coroot API timeout for {endpoint}")
        raise
    except json.JSONDecodeError as e:
        logger.error(f"Coroot API invalid JSON for {endpoint}: {e}")
        return {}
    except Exception as e:
        logger.error(f"Coroot API request failed: {type(e).__name__}: {e}")
        raise


def _handle_error(e: Exception) -> str:
    """Format error message."""
    if isinstance(e, httpx.HTTPStatusError):
        return f"Error: Coroot API returned status {e.response.status_code}"
    return f"Error: {type(e).__name__}: {str(e)}"


def register_tools(mcp: FastMCP):
    """Register Coroot tools with the MCP server."""

    @mcp.tool(name="coroot_get_service_metrics")
    async def coroot_get_service_metrics(app_id: str) -> str:
        """Get metrics for a specific service (CPU, memory, latency, error rate).
        Use app_id from coroot_get_infrastructure_overview (format: cluster_id:namespace:Kind:name)."""
        try:
            result = await _coroot_api(f"app/{app_id}")
            return json.dumps(result, indent=2)
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="coroot_get_recent_anomalies")
    async def coroot_get_recent_anomalies(
        hours: int = 24,
        severity: Optional[str] = None,
    ) -> str:
        """Get recent anomalies detected by Coroot (critical, warning, info)."""
        try:
            result = await _coroot_api("incidents")
            incidents = result if isinstance(result, list) else result.get("incidents", [])

            if severity:
                incidents = [i for i in incidents if i.get("severity", "").lower() == severity.lower()]

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
    async def coroot_get_service_dependencies(app_id: str) -> str:
        """Get upstream and downstream service dependencies for a specific app.
        Use app_id from coroot_get_infrastructure_overview (format: cluster_id:namespace:Kind:name)."""
        try:
            result = await _coroot_api(f"app/{app_id}")
            return json.dumps(result, indent=2)
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="coroot_get_alerts")
    async def coroot_get_alerts(status: str = "firing") -> str:
        """Get current alerts from Coroot (firing, resolved, all)."""
        try:
            result = await _coroot_api("alerts")
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
            # Use health view for service status
            result = await _coroot_api("overview/health")

            overview = {"total_services": 0, "healthy": 0, "warning": 0, "critical": 0, "services": {}}

            # Parse the health overview response
            context = result.get("context", {})
            apps = context.get("search", {}).get("applications", [])

            for app in apps:
                app_id = app.get("id", "unknown")
                parts = app_id.split(":")
                name = parts[-1] if parts else app_id
                status = app.get("status", "unknown")

                overview["total_services"] += 1
                if status == "ok":
                    overview["healthy"] += 1
                elif status == "warning":
                    overview["warning"] += 1
                elif status in ("critical", "error"):
                    overview["critical"] += 1

                overview["services"][name] = {"health": status, "id": app_id}

            return json.dumps(overview, indent=2)
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="coroot_get_service_map")
    async def coroot_get_service_map() -> str:
        """Get global service dependency map showing all service connections across all clusters.
        Returns nodes (services) and their upstream/downstream dependencies."""
        try:
            result = await _coroot_api("overview/map")
            return json.dumps(result, indent=2)
        except Exception as e:
            return _handle_error(e)
