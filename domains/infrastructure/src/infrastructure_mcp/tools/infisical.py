"""Infisical secrets management tools."""

import os
import logging
from typing import Optional, List, Dict, Any

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
# Support both INFISICAL_* prefixed vars and unprefixed vars (for K8s secretRef compatibility)
INFISICAL_URL = os.environ.get("INFISICAL_URL", "https://app.infisical.com")
INFISICAL_CLIENT_ID = os.environ.get("INFISICAL_CLIENT_ID") or os.environ.get("CLIENT_ID", "")
INFISICAL_CLIENT_SECRET = os.environ.get("INFISICAL_CLIENT_SECRET") or os.environ.get("CLIENT_SECRET", "")
INFISICAL_WORKSPACE_ID = os.environ.get("INFISICAL_WORKSPACE_ID") or os.environ.get("WORKSPACE_ID", "")
INFISICAL_ENVIRONMENT = os.environ.get("INFISICAL_ENVIRONMENT", "prod")

# Token cache
_access_token: Optional[str] = None


async def get_access_token() -> str:
    """Get or refresh access token using Machine Identity."""
    global _access_token
    if _access_token:
        return _access_token

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{INFISICAL_URL}/api/v1/auth/universal-auth/login",
            json={"clientId": INFISICAL_CLIENT_ID, "clientSecret": INFISICAL_CLIENT_SECRET}
        )
        response.raise_for_status()
        _access_token = response.json()["accessToken"]
        return _access_token


async def infisical_api(endpoint: str, method: str = "GET", data: dict = None) -> Any:
    """Make authenticated API call to Infisical."""
    token = await get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        url = f"{INFISICAL_URL}/api{endpoint}"
        if method == "GET":
            response = await client.get(url, headers=headers)
        elif method == "POST":
            response = await client.post(url, headers=headers, json=data)
        elif method == "DELETE":
            response = await client.delete(url, headers=headers)
        else:
            response = await client.patch(url, headers=headers, json=data)

        response.raise_for_status()
        return response.json()


async def get_status() -> dict:
    """Get Infisical status for health checks."""
    try:
        # Try to get access token as health check
        await get_access_token()
        return {"status": "healthy"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


def register_tools(mcp: FastMCP):
    """Register Infisical tools with the MCP server."""

    @mcp.tool()
    async def list_secrets(path: str = "/") -> List[Dict[str, Any]]:
        """List secrets at a given path (keys only, not values for security)."""
        try:
            result = await infisical_api(
                f"/v3/secrets/raw?workspaceId={INFISICAL_WORKSPACE_ID}&environment={INFISICAL_ENVIRONMENT}&secretPath={path}"
            )
            secrets = result.get("secrets", [])
            return [{"key": s["secretKey"], "type": s.get("type", "shared")} for s in secrets]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def get_secret(path: str, key: str) -> Dict[str, Any]:
        """Get a specific secret value."""
        try:
            result = await infisical_api(
                f"/v3/secrets/raw/{key}?workspaceId={INFISICAL_WORKSPACE_ID}&environment={INFISICAL_ENVIRONMENT}&secretPath={path}"
            )
            secret = result.get("secret", {})
            return {"key": secret.get("secretKey"), "value": secret.get("secretValue")}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def set_secret(path: str, key: str, value: str) -> Dict[str, Any]:
        """Create or update a secret."""
        try:
            result = await infisical_api(
                f"/v3/secrets/raw/{key}",
                method="POST",
                data={
                    "workspaceId": INFISICAL_WORKSPACE_ID,
                    "environment": INFISICAL_ENVIRONMENT,
                    "secretPath": path,
                    "secretValue": value,
                    "type": "shared"
                }
            )
            return {"success": True, "key": key, "path": path}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def list_folders(path: str = "/") -> List[Dict[str, Any]]:
        """List folders at a given path."""
        try:
            result = await infisical_api(
                f"/v1/folders?workspaceId={INFISICAL_WORKSPACE_ID}&environment={INFISICAL_ENVIRONMENT}&path={path}"
            )
            folders = result.get("folders", [])
            return [{"name": f["name"], "id": f["id"]} for f in folders]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def create_folder(path: str, name: str) -> Dict[str, Any]:
        """Create a new folder."""
        try:
            result = await infisical_api(
                "/v1/folders",
                method="POST",
                data={
                    "workspaceId": INFISICAL_WORKSPACE_ID,
                    "environment": INFISICAL_ENVIRONMENT,
                    "path": path,
                    "name": name
                }
            )
            return {"success": True, "folder": result.get("folder", {})}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def delete_secret(path: str, key: str) -> Dict[str, Any]:
        """Delete a secret."""
        try:
            await infisical_api(
                f"/v3/secrets/raw/{key}?workspaceId={INFISICAL_WORKSPACE_ID}&environment={INFISICAL_ENVIRONMENT}&secretPath={path}",
                method="DELETE"
            )
            return {"success": True, "deleted": key, "path": path}
        except Exception as e:
            return {"error": str(e)}
