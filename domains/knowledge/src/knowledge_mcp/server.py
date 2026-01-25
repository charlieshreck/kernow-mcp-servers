#!/usr/bin/env python3
"""Knowledge MCP - Consolidated semantic search, graph queries, wiki, and task management."""

import os
import logging

from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse
import uvicorn

from knowledge_mcp.tools import qdrant, neo4j, vikunja, outline, silverbullet

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create MCP server
mcp = FastMCP(
    name="knowledge-mcp",
    instructions="""MCP server for comprehensive knowledge base operations.
    Collections: runbooks, documentation, entities, decisions, agent_events.
    Use for semantic search, entity lookup, runbook management, and decision tracking.""",
    stateless_http=True
)

# Register all tool modules
qdrant.register_tools(mcp)
neo4j.register_tools(mcp)
vikunja.register_tools(mcp)
outline.register_tools(mcp)
silverbullet.register_tools(mcp)


# =============================================================================
# Health Check Endpoints
# =============================================================================

async def health(request):
    """Basic health check."""
    return JSONResponse({"status": "healthy", "service": "knowledge-mcp"})


async def ready(request):
    """Readiness check with component status."""
    qdrant_status = await qdrant.get_status()
    neo4j_status = await neo4j.get_status()
    vikunja_status = await vikunja.get_status()
    outline_status = await outline.get_status()
    silverbullet_status = await silverbullet.get_status()

    components = {
        "qdrant": qdrant_status.get("status"),
        "neo4j": neo4j_status.get("status"),
        "vikunja": vikunja_status.get("status"),
        "outline": outline_status.get("status"),
        "silverbullet": silverbullet_status.get("status"),
    }

    all_healthy = all(s == "healthy" for s in components.values())

    return JSONResponse({
        "status": "ready" if all_healthy else "degraded",
        "components": components
    }, status_code=200 if all_healthy else 503)


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    port = int(os.environ.get("PORT", "8000"))
    logger.info(f"Starting Knowledge MCP on port {port}")

    # Create combined Starlette app with health routes and MCP
    rest_routes = [
        Route("/health", health, methods=["GET"]),
        Route("/ready", ready, methods=["GET"]),
    ]

    mcp_app = mcp.http_app()
    app = Starlette(
        routes=rest_routes + [Mount("/", app=mcp_app)],
        lifespan=mcp_app.lifespan
    )

    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
