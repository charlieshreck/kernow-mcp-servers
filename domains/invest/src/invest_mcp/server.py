#!/usr/bin/env python3
"""Invest MCP Server - Investment advisory data and analysis tools."""

import os
import logging
from contextlib import asynccontextmanager

from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse
import uvicorn

from invest_mcp.tools import investmentology

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastMCP instance
mcp = FastMCP(
    name="invest-mcp",
    instructions="""
    MCP server for Investmentology - AI-powered investment advisory platform.

    Tool naming convention:
    - invest_get_portfolio* : Portfolio positions, alerts, balance, briefing, timeline
    - invest_get_watchlist : Stocks being monitored for entry
    - invest_get_stock_* : Per-stock analysis and signals
    - invest_get_recommendations : Buy/sell/hold recommendations
    - invest_get_decisions : Decision log with rationale
    - invest_get_quant_gate_* : Quantitative screening results
    - invest_get_system_health : System health status
    """,
)

# Register all tools
investmentology.register_tools(mcp)


async def health_endpoint(request):
    """Liveness check - lightweight, no external dependencies."""
    return JSONResponse({
        "status": "healthy",
        "service": "invest-mcp",
        "version": "1.0.0",
    })


async def deep_health_endpoint(request):
    """Deep health check with Investmentology API connectivity."""
    import asyncio

    components = {}
    try:
        result = await asyncio.wait_for(investmentology.get_health(), timeout=10.0)
        components["investmentology"] = "error" not in result
    except Exception as e:
        logger.warning(f"Investmentology health check failed: {e}")
        components["investmentology"] = False

    healthy_count = sum(1 for v in components.values() if v)
    total = len(components)

    return JSONResponse({
        "status": "healthy" if healthy_count == total else "degraded",
        "service": "invest-mcp",
        "version": "1.0.0",
        "components": {k: "healthy" if v else "unhealthy" for k, v in components.items()},
        "healthy_count": healthy_count,
        "total_count": total
    })


async def ready_endpoint(request):
    """Readiness probe - lightweight."""
    return JSONResponse({"status": "ready"})


def main():
    """Run the invest MCP server."""
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")

    logger.info(f"Starting invest-mcp on {host}:{port}")

    # Import REST bridge for A2A access
    from kernow_mcp_common.base import create_rest_bridge

    # REST routes
    routes = [
        Route("/health", health_endpoint, methods=["GET"]),
        Route("/health/deep", deep_health_endpoint, methods=["GET"]),
        Route("/ready", ready_endpoint, methods=["GET"]),
        Route("/api/call", create_rest_bridge(mcp, "invest-mcp"), methods=["POST"]),
    ]

    # Get MCP ASGI app
    mcp_app = mcp.http_app(stateless_http=True)

    @asynccontextmanager
    async def lifespan(app):
        async with mcp_app.lifespan(app):
            logger.info("invest-mcp started")
            yield
        logger.info("invest-mcp shutting down")

    # Mount MCP app at root
    app = Starlette(
        routes=routes + [Mount("/", app=mcp_app)],
        lifespan=lifespan
    )

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
