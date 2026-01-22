"""Infisical secrets helper for MCP servers."""

import os
import logging
from typing import Optional, Dict, Any

import httpx

logger = logging.getLogger(__name__)

# Infisical configuration from environment
INFISICAL_URL = os.environ.get("INFISICAL_URL", "https://app.infisical.com")
INFISICAL_CLIENT_ID = os.environ.get("INFISICAL_CLIENT_ID", "")
INFISICAL_CLIENT_SECRET = os.environ.get("INFISICAL_CLIENT_SECRET", "")
INFISICAL_WORKSPACE_ID = os.environ.get("INFISICAL_WORKSPACE_ID", "")
INFISICAL_ENVIRONMENT = os.environ.get("INFISICAL_ENVIRONMENT", "prod")

_access_token: Optional[str] = None


async def _get_access_token() -> str:
    """Get or refresh access token using Machine Identity."""
    global _access_token

    if _access_token:
        return _access_token

    if not INFISICAL_CLIENT_ID or not INFISICAL_CLIENT_SECRET:
        raise ValueError("INFISICAL_CLIENT_ID and INFISICAL_CLIENT_SECRET must be set")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{INFISICAL_URL}/api/v1/auth/universal-auth/login",
            json={
                "clientId": INFISICAL_CLIENT_ID,
                "clientSecret": INFISICAL_CLIENT_SECRET
            }
        )
        response.raise_for_status()
        data = response.json()
        _access_token = data["accessToken"]
        return _access_token


async def get_secret(path: str, key: str) -> Optional[str]:
    """Get a secret value from Infisical.

    Args:
        path: Secret path (e.g., "/agentic-platform/keep")
        key: Secret key name

    Returns:
        Secret value or None if not found
    """
    try:
        token = await _get_access_token()

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{INFISICAL_URL}/api/v3/secrets/raw/{key}",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "workspaceId": INFISICAL_WORKSPACE_ID,
                    "environment": INFISICAL_ENVIRONMENT,
                    "secretPath": path
                }
            )

            if response.status_code == 404:
                return None

            response.raise_for_status()
            data = response.json()
            return data.get("secret", {}).get("secretValue")

    except Exception as e:
        logger.error(f"Failed to get secret {path}/{key}: {e}")
        return None


async def list_secrets(path: str = "/") -> Dict[str, Any]:
    """List secrets at a given path (keys only, not values).

    Args:
        path: Secret path to list

    Returns:
        Dict with secret names and metadata
    """
    try:
        token = await _get_access_token()

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{INFISICAL_URL}/api/v3/secrets/raw",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "workspaceId": INFISICAL_WORKSPACE_ID,
                    "environment": INFISICAL_ENVIRONMENT,
                    "secretPath": path
                }
            )
            response.raise_for_status()
            data = response.json()

            # Return only key names, not values
            return {
                "path": path,
                "secrets": [
                    {"key": s["secretKey"], "type": s.get("type", "shared")}
                    for s in data.get("secrets", [])
                ]
            }

    except Exception as e:
        logger.error(f"Failed to list secrets at {path}: {e}")
        return {"path": path, "secrets": [], "error": str(e)}


def clear_token_cache():
    """Clear the cached access token (useful for testing or token refresh)."""
    global _access_token
    _access_token = None
