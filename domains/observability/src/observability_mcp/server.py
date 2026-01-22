"""Observability MCP Server - Main entry point."""

import os
import logging
import uvicorn

from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route, Mount

# Import tool modules
from .tools import keep, coroot, metrics, alerts, grafana, gatus

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create MCP server with consolidated instructions
mcp = FastMCP(
    name="observability-mcp",
    instructions="""Consolidated observability MCP for the Kernow homelab.

    Provides tools for:
    - **Keep**: Alert aggregation, deduplication, correlation, incidents
    - **Coroot**: Service metrics, anomalies, dependencies
    - **Metrics**: VictoriaMetrics queries (PromQL), scrape targets
    - **Alerts**: AlertManager alerts, silences
    - **Grafana**: Dashboards, annotations, datasources
    - **Gatus**: Endpoint health monitoring

    Tool naming convention:
    - keep_* : Keep alert platform tools
    - coroot_* : Coroot observability tools
    - query_* : VictoriaMetrics metric queries
    - alert_* : AlertManager operations
    - grafana_* : Grafana dashboard/annotation tools
    - gatus_* : Endpoint health tools
    """,
    stateless_http=True
)


def register_tools():
    """Register all tools from submodules."""
    # Keep tools
    keep.register_tools(mcp)
    # Coroot tools
    coroot.register_tools(mcp)
    # VictoriaMetrics tools
    metrics.register_tools(mcp)
    # AlertManager tools
    alerts.register_tools(mcp)
    # Grafana tools
    grafana.register_tools(mcp)
    # Gatus tools
    gatus.register_tools(mcp)


# Register all tools
register_tools()


async def health(request):
    """Health check endpoint."""
    return JSONResponse({
        "status": "healthy",
        "service": "observability-mcp",
        "version": "1.0.0",
        "components": ["keep", "coroot", "metrics", "alerts", "grafana", "gatus"]
    })


async def ready(request):
    """Readiness probe."""
    return JSONResponse({"ready": True})


# Create Starlette app with routes
routes = [
    Route("/health", health, methods=["GET"]),
    Route("/ready", ready, methods=["GET"]),
    Mount("/", app=mcp.sse_app()),
]

app = Starlette(routes=routes)


def main():
    """Run the server."""
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")

    logger.info(f"Starting observability-mcp on {host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
