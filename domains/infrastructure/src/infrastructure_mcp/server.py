"""Infrastructure MCP server - Kubernetes, VM, storage, DNS, and secrets management."""

import logging

from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse

from infrastructure_mcp.tools import kubernetes, proxmox, truenas, cloudflare, opnsense, infisical

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create MCP server
mcp = FastMCP(
    name="infrastructure-mcp",
    instructions="""Comprehensive infrastructure management MCP for the Kernow homelab.

Provides tools for:
- **Kubernetes**: Pod, deployment, service, job, configmap, secret management across clusters
- **ArgoCD**: Application sync and status (prod cluster only)
- **Proxmox**: VM and container lifecycle management
- **TrueNAS**: ZFS pools, datasets, shares, snapshots (hdd + media instances)
- **Cloudflare**: DNS zones, records, tunnels, cache management
- **OPNsense**: Firewall, DHCP, gateway status
- **AdGuard Home**: DNS stats, query logs, filtering, rewrites
- **Unbound**: DNS resolver stats, host overrides
- **Caddy**: Reverse proxy management
- **Infisical**: Secrets management (list, get, set)

Tool prefixes:
- kubectl_* : Kubernetes operations
- argocd_* : GitOps operations
- proxmox_* : VM/container management
- truenas_* : Storage management
- cloudflare_* : DNS and tunnels
- get_*/set_*/list_*/add_* : OPNsense/AdGuard/Unbound/Caddy operations
- list_secrets/get_secret/set_secret : Infisical secrets
""",
    stateless_http=True
)

# Register all tools
kubernetes.register_tools(mcp)
proxmox.register_tools(mcp)
truenas.register_tools(mcp)
cloudflare.register_tools(mcp)
opnsense.register_tools(mcp)
infisical.register_tools(mcp)


# Health check endpoints
async def health(request):
    """Basic health check."""
    return JSONResponse({"status": "healthy", "service": "infrastructure-mcp"})


async def ready(request):
    """Readiness check - verifies component connectivity."""
    components = {}

    # Check each component
    try:
        k8s_status = await kubernetes.get_status()
        components["kubernetes"] = k8s_status.get("status", "unknown")
    except Exception as e:
        components["kubernetes"] = f"error: {str(e)[:50]}"

    try:
        proxmox_status = await proxmox.get_status()
        components["proxmox"] = proxmox_status.get("status", "unknown")
    except Exception as e:
        components["proxmox"] = f"error: {str(e)[:50]}"

    try:
        truenas_status = await truenas.get_status()
        components["truenas"] = truenas_status.get("status", "unknown")
    except Exception as e:
        components["truenas"] = f"error: {str(e)[:50]}"

    try:
        cloudflare_status = await cloudflare.get_status()
        components["cloudflare"] = cloudflare_status.get("status", "unknown")
    except Exception as e:
        components["cloudflare"] = f"error: {str(e)[:50]}"

    try:
        opnsense_status = await opnsense.get_status()
        components["opnsense"] = opnsense_status.get("status", "unknown")
    except Exception as e:
        components["opnsense"] = f"error: {str(e)[:50]}"

    try:
        infisical_status = await infisical.get_status()
        components["infisical"] = infisical_status.get("status", "unknown")
    except Exception as e:
        components["infisical"] = f"error: {str(e)[:50]}"

    # Overall status
    all_healthy = all(
        "healthy" in str(s) or "degraded" in str(s)
        for s in components.values()
    )

    return JSONResponse({
        "status": "ready" if all_healthy else "degraded",
        "service": "infrastructure-mcp",
        "components": components
    })


def main():
    """Run the MCP server with health endpoints and REST bridge."""
    import uvicorn
    from kernow_mcp_common.base import create_rest_bridge

    # Create REST routes including A2A bridge
    rest_routes = [
        Route("/health", health, methods=["GET"]),
        Route("/ready", ready, methods=["GET"]),
        Route("/api/call", create_rest_bridge(mcp, "infrastructure-mcp"), methods=["POST"]),
    ]

    # Mount MCP app
    mcp_app = mcp.http_app()
    app = Starlette(
        routes=rest_routes + [Mount("/", app=mcp_app)],
        lifespan=mcp_app.lifespan
    )

    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
