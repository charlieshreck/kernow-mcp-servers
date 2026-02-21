"""Observability MCP Server - Main entry point."""

import os
import logging
import uvicorn

from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route, Mount

# Import tool modules
from .tools import coroot, metrics, alerts, grafana, gatus, ntopng

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create MCP server with consolidated instructions
mcp = FastMCP(
    name="observability-mcp",
    instructions="""Consolidated observability MCP for the Kernow homelab.

    Provides tools for:
    - **Coroot**: Service metrics, anomalies, dependencies
    - **Metrics**: VictoriaMetrics queries (PromQL), scrape targets
    - **Alerts**: AlertManager alerts, silences
    - **Grafana**: Dashboards, annotations, datasources
    - **Gatus**: Endpoint health monitoring
    - **ntopng**: Network traffic analysis (hosts, flows, L7 protocols, alerts)

    Tool naming convention:
    - coroot_* : Coroot observability tools
    - query_* : VictoriaMetrics metric queries
    - list_alerts / create_silence / etc : AlertManager operations
    - grafana_* : Grafana dashboard/annotation tools
    - gatus_* : Endpoint health tools
    - ntopng_* : Network traffic monitoring tools
    """,
)


def register_tools():
    """Register all tools from submodules."""
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
    # ntopng tools
    ntopng.register_tools(mcp)


# Register all tools
register_tools()


async def health(request):
    """Health check endpoint."""
    return JSONResponse({
        "status": "healthy",
        "service": "observability-mcp",
        "version": "1.0.0",
        "components": ["coroot", "metrics", "alerts", "grafana", "gatus", "ntopng"]
    })


async def ready(request):
    """Readiness probe."""
    return JSONResponse({"ready": True})


# Import REST bridge for A2A access
from kernow_mcp_common.base import create_rest_bridge

# Create Starlette app with routes
# Use http_app() for stateless HTTP MCP transport
mcp_app = mcp.http_app(stateless_http=True)

routes = [
    Route("/health", health, methods=["GET"]),
    Route("/ready", ready, methods=["GET"]),
    Route("/api/call", create_rest_bridge(mcp, "observability-mcp"), methods=["POST"]),
    Mount("/", app=mcp_app),
]

app = Starlette(routes=routes, lifespan=mcp_app.lifespan)


def main():
    """Run the server."""
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")

    logger.info(f"Starting observability-mcp on {host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
