"""VictoriaMetrics query tools."""

import os
import json
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta

import httpx
from fastmcp import FastMCP
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Configuration
VICTORIA_METRICS_URL = os.environ.get("VICTORIA_METRICS_URL", "http://10.30.0.20:30084")


class QueryInput(BaseModel):
    query: str = Field(description="PromQL query string")
    start: str = Field(default="1h", description="Time range start as duration (e.g., '1h', '30m', '24h')")
    step: str = Field(default="1m", description="Query resolution step")


class InstantQueryInput(BaseModel):
    query: str = Field(description="PromQL query string")


class MetricSearchInput(BaseModel):
    search: str = Field(default="", description="Optional search filter")


def _parse_duration(duration: str) -> timedelta:
    """Parse duration string like '1h', '30m', '24h' into timedelta."""
    unit = duration[-1]
    value = int(duration[:-1])
    if unit == 'h':
        return timedelta(hours=value)
    elif unit == 'm':
        return timedelta(minutes=value)
    elif unit == 'd':
        return timedelta(days=value)
    return timedelta(hours=1)


async def _vm_api(endpoint: str, params: dict = None) -> Dict[str, Any]:
    """Make request to VictoriaMetrics API."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        url = f"{VICTORIA_METRICS_URL}/api/v1{endpoint}"
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()


def _handle_error(e: Exception) -> str:
    """Format error message."""
    if isinstance(e, httpx.HTTPStatusError):
        return f"Error: VictoriaMetrics returned status {e.response.status_code}"
    return f"Error: {type(e).__name__}: {str(e)}"


def register_tools(mcp: FastMCP):
    """Register VictoriaMetrics tools with the MCP server."""

    @mcp.tool(name="query_metrics")
    async def query_metrics(params: QueryInput) -> str:
        """Execute PromQL query against VictoriaMetrics.

        Args:
            query: PromQL query string (e.g., "up", "rate(http_requests_total[5m])")
            start: Time range start as duration (e.g., "1h", "30m", "24h")
            step: Query resolution step (e.g., "1m", "5m")
        """
        try:
            end = datetime.now()
            start_time = end - _parse_duration(params.start)

            result = await _vm_api("/query_range", {
                "query": params.query,
                "start": int(start_time.timestamp()),
                "end": int(end.timestamp()),
                "step": params.step
            })

            return json.dumps(result, indent=2)
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="query_metrics_instant")
    async def query_metrics_instant(params: InstantQueryInput) -> str:
        """Execute instant PromQL query against VictoriaMetrics."""
        try:
            result = await _vm_api("/query", {"query": params.query})
            return json.dumps(result, indent=2)
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="get_scrape_targets")
    async def get_scrape_targets() -> str:
        """List all Prometheus scrape targets and their status."""
        try:
            result = await _vm_api("/targets")
            targets = result.get("data", {}).get("activeTargets", [])

            lines = [f"# Scrape Targets ({len(targets)})", ""]
            for t in targets:
                job = t.get("labels", {}).get("job", "unknown")
                health = t.get("health", "unknown")
                icon = "[OK]" if health == "up" else "[!!]"
                lines.append(f"- {icon} **{job}**: {t.get('scrapeUrl', 'N/A')}")

            return "\n".join(lines)
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="get_metric_names")
    async def get_metric_names(params: MetricSearchInput) -> str:
        """Get available metric names, optionally filtered by search term."""
        try:
            result = await _vm_api("/label/__name__/values")
            names = result.get("data", [])

            if params.search:
                names = [n for n in names if params.search.lower() in n.lower()]

            return json.dumps({"metrics": names[:100], "total": len(names)}, indent=2)
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="get_tsdb_stats")
    async def get_tsdb_stats() -> str:
        """Get VictoriaMetrics TSDB statistics (cardinality, storage)."""
        try:
            result = await _vm_api("/status/tsdb")
            return json.dumps(result, indent=2)
        except Exception as e:
            return _handle_error(e)
