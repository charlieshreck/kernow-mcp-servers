"""Base MCP server setup with standard configuration."""

import logging
import os
from typing import Optional, Callable, Awaitable, Any

from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
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


def create_rest_bridge(
    mcp: FastMCP,
    name: str,
    require_auth: bool = True
) -> Callable[[Request], Awaitable[JSONResponse]]:
    """Create a REST bridge endpoint for A2A agent access.

    This allows A2A agents to call MCP tools via simple POST requests
    instead of the SSE/MCP protocol.

    Args:
        mcp: FastMCP instance with registered tools
        name: Service name for logging
        require_auth: Whether to require Bearer token auth (default True)

    Returns:
        Async endpoint function for /api/call route

    Usage:
        rest_routes = [
            Route("/api/call", create_rest_bridge(mcp, "my-mcp"), methods=["POST"]),
        ]
    """
    logger = logging.getLogger(f"{name}.rest_bridge")

    # Get auth token from environment (set via Infisical)
    auth_token = os.environ.get("A2A_API_TOKEN", "")

    async def api_call(request: Request) -> JSONResponse:
        """REST endpoint to invoke MCP tools via POST.

        Request body:
            {
                "tool": "tool_name",
                "arguments": {"arg1": "value1", ...}
            }

        Response:
            {
                "status": "success" | "error",
                "tool": "tool_name",
                "output": <tool result> | null,
                "error": <error message> | null
            }
        """
        # Auth check
        if require_auth and auth_token:
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return JSONResponse(
                    {"status": "error", "error": "Missing Bearer token"},
                    status_code=401
                )
            provided_token = auth_header[7:]  # Strip "Bearer "
            if provided_token != auth_token:
                return JSONResponse(
                    {"status": "error", "error": "Invalid token"},
                    status_code=403
                )

        try:
            body = await request.json()
        except Exception as e:
            return JSONResponse(
                {"status": "error", "error": f"Invalid JSON: {e}"},
                status_code=400
            )

        tool_name = body.get("tool")
        arguments = body.get("arguments", {})

        if not tool_name:
            return JSONResponse(
                {"status": "error", "error": "Missing 'tool' field"},
                status_code=400
            )

        logger.info(f"REST bridge call: {tool_name}({arguments})")

        try:
            # Get the tool from FastMCP registry
            tool = await mcp.get_tool(tool_name)
            if not tool:
                return JSONResponse({
                    "status": "error",
                    "tool": tool_name,
                    "error": f"Tool not found: {tool_name}"
                }, status_code=404)

            # Call the tool's function with arguments
            import inspect
            if inspect.iscoroutinefunction(tool.fn):
                result = await tool.fn(**arguments)
            else:
                result = tool.fn(**arguments)

            # Format the result
            if isinstance(result, dict):
                output = result
            elif isinstance(result, (list, tuple)):
                output = result
            else:
                output = str(result)

            return JSONResponse({
                "status": "success",
                "tool": tool_name,
                "output": output
            })

        except TypeError as e:
            # Likely a missing or invalid argument
            logger.error(f"Tool call failed: {tool_name} - {e}")
            return JSONResponse({
                "status": "error",
                "tool": tool_name,
                "error": f"Invalid arguments: {e}"
            }, status_code=400)

        except Exception as e:
            logger.error(f"Tool call failed: {tool_name} - {e}")
            return JSONResponse({
                "status": "error",
                "tool": tool_name,
                "error": str(e)
            }, status_code=500)

    return api_call


def create_starlette_app_with_rest(
    mcp: FastMCP,
    name: str,
    version: str = "1.0.0",
    health_check_fn: Optional[Callable] = None,
    enable_rest_bridge: bool = True
) -> Starlette:
    """Create a Starlette app with MCP routes, health endpoints, and REST bridge.

    Args:
        mcp: FastMCP instance
        name: Service name for health response
        version: Service version for health response
        health_check_fn: Optional async function for deep health checks
        enable_rest_bridge: Whether to enable /api/call endpoint (default True)

    Returns:
        Configured Starlette application with REST bridge
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
    ]

    # Add REST bridge if enabled
    if enable_rest_bridge:
        routes.append(
            Route("/api/call", create_rest_bridge(mcp, name), methods=["POST"])
        )

    # Mount MCP SSE app
    mcp_app = mcp.http_app()
    routes.append(Mount("/", app=mcp_app))

    return Starlette(
        routes=routes,
        lifespan=mcp_app.lifespan
    )
