"""External MCP Server - Main entry point."""

import os
import logging
import uvicorn

from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route, Mount

# Import tool modules
from .tools import websearch, github, reddit, wikipedia, browser

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create MCP server with consolidated instructions
mcp = FastMCP(
    name="external-mcp",
    instructions="""Consolidated external APIs MCP for the Kernow homelab.

    Provides tools for:
    - **Web Search**: SearXNG-powered web, news, and image search
    - **GitHub**: Repository operations, issues, PRs, code search, workflows
    - **Reddit**: Browse subreddits, search posts, read discussions
    - **Wikipedia**: Article search, summaries, knowledge retrieval
    - **Browser**: Playwright-based browser automation and screenshots

    Tool naming convention:
    - websearch_* : Web search tools (SearXNG)
    - github_* : GitHub API tools
    - reddit_* : Reddit browsing tools
    - wikipedia_* : Wikipedia knowledge tools
    - browser_* : Browser automation tools
    """,
)


def register_tools():
    """Register all tools from submodules."""
    # Web Search tools
    websearch.register_tools(mcp)
    # GitHub tools
    github.register_tools(mcp)
    # Reddit tools
    reddit.register_tools(mcp)
    # Wikipedia tools
    wikipedia.register_tools(mcp)
    # Browser tools
    browser.register_tools(mcp)


# Register all tools
register_tools()


async def health(request):
    """Health check endpoint."""
    return JSONResponse({
        "status": "healthy",
        "service": "external-mcp",
        "version": "1.0.0",
        "components": ["websearch", "github", "reddit", "wikipedia", "browser"]
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
    Route("/api/call", create_rest_bridge(mcp, "external-mcp"), methods=["POST"]),
    Mount("/", app=mcp_app),
]

app = Starlette(routes=routes, lifespan=mcp_app.lifespan)


def main():
    """Run the server."""
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")

    logger.info(f"Starting external-mcp on {host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
