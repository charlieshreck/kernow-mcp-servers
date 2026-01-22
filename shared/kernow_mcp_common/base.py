"""Base MCP server setup with standard configuration."""

import logging
from typing import Optional

from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route, Mount


def create_mcp_server(
    name: str,
    instructions: str,
    version: str = "1.0.0"
) -> FastMCP:
    """Create a FastMCP server with standard Kernow configuration.

    Args:
        name: Server name (e.g., "observability-mcp")
        instructions: Server instructions for the LLM
        version: Server version string

    Returns:
        Configured FastMCP instance with stateless_http=True
    """
    return FastMCP(
        name=name,
        instructions=instructions,
        stateless_http=True,  # Required for Kubernetes pod restarts
    )


def create_starlette_app(
    mcp: FastMCP,
    name: str,
    version: str = "1.0.0",
    health_check_fn: Optional[callable] = None
) -> Starlette:
    """Create a Starlette app with MCP routes and health endpoints.

    Args:
        mcp: FastMCP instance
        name: Service name for health response
        version: Service version for health response
        health_check_fn: Optional async function for deep health checks

    Returns:
        Configured Starlette application
    """

    async def health(request):
        """Basic health check endpoint."""
        result = {"status": "healthy", "service": name, "version": version}

        if health_check_fn:
            try:
                deep_health = await health_check_fn()
                result["checks"] = deep_health
            except Exception as e:
                result["status"] = "degraded"
                result["error"] = str(e)

        return JSONResponse(result)

    async def ready(request):
        """Readiness probe endpoint."""
        return JSONResponse({"ready": True})

    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/ready", ready, methods=["GET"]),
        Mount("/", app=mcp.sse_app()),
    ]

    return Starlette(routes=routes)


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure logging with standard format.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR)

    Returns:
        Configured root logger
    """
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    return logging.getLogger(__name__)
