#!/usr/bin/env python3
"""Media MCP Server - Consolidated media management."""

import os
import logging
from contextlib import asynccontextmanager

from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse
import uvicorn

from media_mcp.tools import (
    plex, sonarr, radarr, prowlarr, overseerr, tautulli, transmission, sabnzbd,
    huntarr, cleanuparr, maintainerr, notifiarr, recommendarr
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create FastMCP instance
mcp = FastMCP(
    name="media-mcp",
    instructions="""
    MCP server for Plex, Sonarr, Radarr, Prowlarr, Overseerr, Tautulli,
    Transmission, SABnzbd.

    Tool naming convention:
    - plex_* : Plex server operations
    - sonarr_* : TV show management
    - radarr_* : Movie management
    - prowlarr_* : Indexer management
    - overseerr_* : Request management
    - tautulli_* : Plex statistics
    - transmission_* : Torrent downloads
    - sabnzbd_* : Usenet downloads
    - huntarr_* : Missing media discovery
    - cleanuparr_* : Download queue cleanup
    - maintainerr_* : Plex media maintenance
    - notifiarr_* : Notification client
    - recommendarr_* : AI-powered recommendations
    """,
    stateless_http=True
)

# Register all tools
plex.register_tools(mcp)
sonarr.register_tools(mcp)
radarr.register_tools(mcp)
prowlarr.register_tools(mcp)
overseerr.register_tools(mcp)
tautulli.register_tools(mcp)
transmission.register_tools(mcp)
sabnzbd.register_tools(mcp)
huntarr.register_tools(mcp)
cleanuparr.register_tools(mcp)
maintainerr.register_tools(mcp)
notifiarr.register_tools(mcp)
recommendarr.register_tools(mcp)


# Health check components
async def check_components() -> dict:
    """Check health of all media components."""
    components = {}

    # Plex
    try:
        status = await plex.get_server_status()
        components["plex"] = "error" not in status
    except Exception as e:
        logger.warning(f"Plex health check failed: {e}")
        components["plex"] = False

    # Sonarr
    try:
        status = await sonarr.get_system_status()
        components["sonarr"] = "error" not in status
    except Exception as e:
        logger.warning(f"Sonarr health check failed: {e}")
        components["sonarr"] = False

    # Radarr
    try:
        status = await radarr.get_system_status()
        components["radarr"] = "error" not in status
    except Exception as e:
        logger.warning(f"Radarr health check failed: {e}")
        components["radarr"] = False

    # Prowlarr
    try:
        health = await prowlarr.get_health()
        components["prowlarr"] = isinstance(health, list)
    except Exception as e:
        logger.warning(f"Prowlarr health check failed: {e}")
        components["prowlarr"] = False

    # Overseerr
    try:
        status = await overseerr.get_status()
        components["overseerr"] = "error" not in status
    except Exception as e:
        logger.warning(f"Overseerr health check failed: {e}")
        components["overseerr"] = False

    # Tautulli
    try:
        activity = await tautulli.get_activity()
        components["tautulli"] = "error" not in activity
    except Exception as e:
        logger.warning(f"Tautulli health check failed: {e}")
        components["tautulli"] = False

    # Transmission
    try:
        torrents = await transmission.list_torrents()
        components["transmission"] = not (torrents and "error" in torrents[0])
    except Exception as e:
        logger.warning(f"Transmission health check failed: {e}")
        components["transmission"] = False

    # SABnzbd
    try:
        queue = await sabnzbd.get_queue()
        components["sabnzbd"] = "error" not in queue
    except Exception as e:
        logger.warning(f"SABnzbd health check failed: {e}")
        components["sabnzbd"] = False

    # Huntarr
    try:
        status = await huntarr.get_status()
        components["huntarr"] = "error" not in status
    except Exception as e:
        logger.warning(f"Huntarr health check failed: {e}")
        components["huntarr"] = False

    # Cleanuparr
    try:
        status = await cleanuparr.get_status()
        components["cleanuparr"] = "error" not in status
    except Exception as e:
        logger.warning(f"Cleanuparr health check failed: {e}")
        components["cleanuparr"] = False

    # Maintainerr
    try:
        status = await maintainerr.get_status()
        components["maintainerr"] = "error" not in status
    except Exception as e:
        logger.warning(f"Maintainerr health check failed: {e}")
        components["maintainerr"] = False

    # Notifiarr
    try:
        status = await notifiarr.get_status()
        components["notifiarr"] = "error" not in status
    except Exception as e:
        logger.warning(f"Notifiarr health check failed: {e}")
        components["notifiarr"] = False

    # Recommendarr
    try:
        status = await recommendarr.get_status()
        components["recommendarr"] = "error" not in status
    except Exception as e:
        logger.warning(f"Recommendarr health check failed: {e}")
        components["recommendarr"] = False

    return components


async def health_endpoint(request):
    """Health check endpoint."""
    components = await check_components()
    healthy_count = sum(1 for v in components.values() if v)
    total = len(components)

    # Healthy if at least half the components are working
    is_healthy = healthy_count >= total // 2

    return JSONResponse({
        "status": "healthy" if is_healthy else "degraded",
        "service": "media-mcp",
        "version": "1.0.0",
        "components": {k: "healthy" if v else "unhealthy" for k, v in components.items()},
        "healthy_count": healthy_count,
        "total_count": total
    })


async def ready_endpoint(request):
    """Readiness probe - checks if at least one component is available."""
    components = await check_components()
    any_healthy = any(components.values())

    if any_healthy:
        return JSONResponse({"status": "ready"})
    return JSONResponse({"status": "not ready"}, status_code=503)


def main():
    """Run the media MCP server."""
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")

    logger.info(f"Starting media-mcp on {host}:{port}")

    # Import REST bridge for A2A access
    from kernow_mcp_common.base import create_rest_bridge

    # REST routes
    routes = [
        Route("/health", health_endpoint, methods=["GET"]),
        Route("/ready", ready_endpoint, methods=["GET"]),
        Route("/api/call", create_rest_bridge(mcp, "media-mcp"), methods=["POST"]),
    ]

    # Get MCP ASGI app
    mcp_app = mcp.http_app()

    @asynccontextmanager
    async def lifespan(app):
        async with mcp_app.lifespan(app):
            logger.info("media-mcp started")
            yield
        logger.info("media-mcp shutting down")

    # Mount MCP app at root
    app = Starlette(
        routes=routes + [Mount("/", app=mcp_app)],
        lifespan=lifespan
    )

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
