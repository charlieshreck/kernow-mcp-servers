"""Home MCP Server - Consolidated smart home, network, and IoT management.

Combines: Home Assistant, Tasmota, UniFi, AdGuard, Homepage
"""

import os
import logging

from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse
import uvicorn

from home_mcp.tools import homeassistant, tasmota, unifi, adguard, homepage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create MCP server
mcp = FastMCP(
    name="home-mcp",
    instructions="""Consolidated Home MCP for smart home, network, and IoT management.

    Components:
    - Home Assistant: Smart home control (lights, switches, climate, covers, fans, locks, media, scenes, automations)
    - Tasmota: Direct control of 26+ Tasmota smart devices (lights, switches, plugs)
    - UniFi: Network management (clients, devices, WLANs, events, RF optimization)
    - AdGuard: DNS filtering and management (stats, query logs, rewrites, protection)
    - Homepage: Dashboard service discovery and health checks

    Use list_entities() for Home Assistant devices.
    Use tasmota_list_devices() for Tasmota devices.
    Use unifi_list_clients() for network clients.""",
)


def register_tools():
    """Register all tools from submodules."""
    homeassistant.register_tools(mcp)
    tasmota.register_tools(mcp)
    unifi.register_tools(mcp)
    adguard.register_tools(mcp)
    homepage.register_tools(mcp)


# Register all tools
register_tools()


async def health_check(request):
    """Liveness check — lightweight, no external dependencies."""
    return JSONResponse({
        "status": "healthy",
        "service": "home-mcp",
        "version": "1.0.0",
    })


async def deep_health_check(request):
    """Deep health check with component status (for debugging, not probes)."""
    import asyncio

    async def check_component(name, get_status_fn):
        try:
            result = await asyncio.wait_for(get_status_fn(), timeout=5.0)
            return name, result.get("status", "unhealthy")
        except Exception as e:
            logger.error(f"{name} health check failed: {e}")
            return name, "unhealthy"

    results = await asyncio.gather(
        check_component("homeassistant", homeassistant.get_status),
        check_component("tasmota", tasmota.get_status),
        check_component("unifi", unifi.get_status),
        check_component("adguard", adguard.get_status),
        check_component("homepage", homepage.get_status),
    )
    components = dict(results)

    healthy_count = sum(1 for v in components.values() if v == "healthy")
    total_count = len(components)
    overall_status = "healthy" if healthy_count >= total_count // 2 else "unhealthy"

    return JSONResponse({
        "status": overall_status,
        "service": "home-mcp",
        "version": "1.0.0",
        "components": components,
        "healthy_count": healthy_count,
        "total_count": total_count
    })


async def ready_check(request):
    """Readiness check endpoint."""
    return JSONResponse({"ready": True})


# Import REST bridge for A2A access
from kernow_mcp_common.base import create_rest_bridge

# Create Starlette app with health endpoints and MCP
# Use http_app() for stateless HTTP MCP transport
mcp_app = mcp.http_app(stateless_http=True)

routes = [
    Route("/health", health_check, methods=["GET"]),
    Route("/health/deep", deep_health_check, methods=["GET"]),
    Route("/ready", ready_check, methods=["GET"]),
    Route("/api/call", create_rest_bridge(mcp, "home-mcp"), methods=["POST"]),
    Mount("/", app=mcp_app),
]

app = Starlette(routes=routes, lifespan=mcp_app.lifespan)


def main():
    """Run the server."""
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")
    logger.info(f"Starting Home MCP server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
