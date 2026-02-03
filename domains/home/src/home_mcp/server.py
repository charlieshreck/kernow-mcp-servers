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
    stateless_http=True
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
    """Health check endpoint."""
    components = {}

    # Check Home Assistant
    try:
        result = await homeassistant.get_status()
        components["homeassistant"] = result.get("status", "unhealthy")
    except Exception as e:
        components["homeassistant"] = "unhealthy"
        logger.error(f"Home Assistant health check failed: {e}")

    # Check Tasmota
    try:
        result = await tasmota.get_status()
        components["tasmota"] = result.get("status", "unhealthy")
    except Exception as e:
        components["tasmota"] = "unhealthy"
        logger.error(f"Tasmota health check failed: {e}")

    # Check UniFi
    try:
        result = await unifi.get_status()
        components["unifi"] = result.get("status", "unhealthy")
    except Exception as e:
        components["unifi"] = "unhealthy"
        logger.error(f"UniFi health check failed: {e}")

    # Check AdGuard
    try:
        result = await adguard.get_status()
        components["adguard"] = result.get("status", "unhealthy")
    except Exception as e:
        components["adguard"] = "unhealthy"
        logger.error(f"AdGuard health check failed: {e}")

    # Check Homepage
    try:
        result = await homepage.get_status()
        components["homepage"] = result.get("status", "unhealthy")
    except Exception as e:
        components["homepage"] = "unhealthy"
        logger.error(f"Homepage health check failed: {e}")

    # Calculate overall health (healthy if at least half components work)
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
mcp_app = mcp.http_app()

routes = [
    Route("/health", health_check, methods=["GET"]),
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
