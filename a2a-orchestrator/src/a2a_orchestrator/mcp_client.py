"""MCP REST Client - Call MCP tools via the /api/call REST bridge."""

import os
import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# MCP endpoints (ClusterIP within ai-platform namespace)
MCP_ENDPOINTS = {
    "infrastructure": os.environ.get("INFRASTRUCTURE_MCP_URL", "http://infrastructure-mcp:8000"),
    "observability": os.environ.get("OBSERVABILITY_MCP_URL", "http://observability-mcp:8000"),
    "knowledge": os.environ.get("KNOWLEDGE_MCP_URL", "http://knowledge-mcp:8000"),
    "home": os.environ.get("HOME_MCP_URL", "http://home-mcp:8000"),
}

# Auth token for MCP access
A2A_API_TOKEN = os.environ.get("A2A_API_TOKEN", "")


async def call_mcp_tool(
    mcp: str,
    tool: str,
    arguments: dict[str, Any] = None,
    timeout: float = 10.0
) -> dict:
    """Call an MCP tool via the REST bridge.

    Args:
        mcp: MCP name (infrastructure, observability, knowledge, home)
        tool: Tool name to call
        arguments: Tool arguments
        timeout: Request timeout in seconds

    Returns:
        Tool result as dict with 'status' and 'output' or 'error'
    """
    if mcp not in MCP_ENDPOINTS:
        return {"status": "error", "error": f"Unknown MCP: {mcp}"}

    base_url = MCP_ENDPOINTS[mcp]
    url = f"{base_url}/api/call"

    headers = {}
    if A2A_API_TOKEN:
        headers["Authorization"] = f"Bearer {A2A_API_TOKEN}"

    payload = {
        "tool": tool,
        "arguments": arguments or {}
    }

    logger.debug(f"Calling {mcp}/{tool} with {arguments}")

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=payload, headers=headers)

            if response.status_code == 401:
                return {"status": "error", "error": "Unauthorized - check A2A_API_TOKEN"}
            if response.status_code == 403:
                return {"status": "error", "error": "Forbidden - invalid token"}

            response.raise_for_status()
            return response.json()

    except httpx.TimeoutException:
        logger.warning(f"MCP call timed out: {mcp}/{tool}")
        return {"status": "error", "error": "timeout"}
    except httpx.HTTPError as e:
        logger.error(f"MCP call failed: {mcp}/{tool} - {e}")
        return {"status": "error", "error": str(e)}
    except Exception as e:
        logger.error(f"MCP call error: {mcp}/{tool} - {e}")
        return {"status": "error", "error": str(e)}


# Convenience wrappers for common tools

async def kubectl_get_pods(namespace: str = "default", name: Optional[str] = None) -> dict:
    """Get pods from infrastructure-mcp."""
    args = {"namespace": namespace}
    if name:
        args["name"] = name
    return await call_mcp_tool("infrastructure", "kubectl_get_pods", args)


async def kubectl_get_events(namespace: str = "default", field_selector: Optional[str] = None) -> dict:
    """Get events from infrastructure-mcp."""
    args = {"namespace": namespace}
    if field_selector:
        args["field_selector"] = field_selector
    return await call_mcp_tool("infrastructure", "kubectl_get_events", args)


async def kubectl_logs(namespace: str, pod: str, container: Optional[str] = None, tail: int = 50) -> dict:
    """Get pod logs from infrastructure-mcp."""
    args = {"namespace": namespace, "pod": pod, "tail": tail}
    if container:
        args["container"] = container
    return await call_mcp_tool("infrastructure", "kubectl_logs", args)


async def get_secret(path: str, key: str) -> dict:
    """Get secret from infrastructure-mcp (Infisical)."""
    return await call_mcp_tool("infrastructure", "get_secret", {"path": path, "key": key})


async def list_secrets(path: str) -> dict:
    """List secrets from infrastructure-mcp (Infisical)."""
    return await call_mcp_tool("infrastructure", "list_secrets", {"path": path})


async def query_metrics(query: str, time: Optional[str] = None) -> dict:
    """Query VictoriaMetrics from observability-mcp."""
    args = {"query": query}
    if time:
        args["time"] = time
    return await call_mcp_tool("observability", "query_metrics_instant", args)


async def get_alerts() -> dict:
    """Get active alerts from observability-mcp."""
    return await call_mcp_tool("observability", "list_alerts")


async def coroot_get_anomalies() -> dict:
    """Get recent anomalies from observability-mcp (Coroot)."""
    return await call_mcp_tool("observability", "coroot_get_recent_anomalies")


async def adguard_get_rewrites() -> dict:
    """Get DNS rewrites from home-mcp (AdGuard)."""
    return await call_mcp_tool("home", "adguard_list_rewrites")


async def search_runbooks(query: str) -> dict:
    """Search runbooks from knowledge-mcp."""
    return await call_mcp_tool("knowledge", "search_runbooks", {"query": query})


async def search_entities(query: str) -> dict:
    """Search entities from knowledge-mcp."""
    return await call_mcp_tool("knowledge", "search_entities", {"query": query})
