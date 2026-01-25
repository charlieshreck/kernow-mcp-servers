"""Silver Bullet markdown PKM tools for knowledge-mcp."""

import os
import logging
from typing import Optional
import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

SILVERBULLET_URL = os.environ.get("SILVERBULLET_URL", "http://silverbullet.ai-platform.svc.cluster.local:3000")
SILVERBULLET_USER = os.environ.get("SILVERBULLET_USER", "")


def _get_auth() -> Optional[tuple]:
    """Get basic auth tuple if credentials configured."""
    if SILVERBULLET_USER and ":" in SILVERBULLET_USER:
        user, password = SILVERBULLET_USER.split(":", 1)
        return (user, password)
    return None


async def silverbullet_api(
    endpoint: str,
    method: str = "GET",
    content: Optional[str] = None,
    get_meta: bool = False
) -> dict:
    """Make API call to Silver Bullet."""
    url = f"{SILVERBULLET_URL}/.fs{endpoint}"
    headers = {}
    if get_meta:
        headers["X-Get-Meta"] = "true"

    async with httpx.AsyncClient(timeout=30.0, auth=_get_auth()) as client:
        if method == "GET":
            resp = await client.get(url, headers=headers)
        elif method == "PUT":
            resp = await client.put(url, content=content, headers=headers)
        elif method == "DELETE":
            resp = await client.delete(url, headers=headers)
        else:
            raise ValueError(f"Unsupported method: {method}")

        resp.raise_for_status()

        if method == "GET" and not get_meta:
            return {"content": resp.text, "status": resp.status_code}
        return {"status": resp.status_code}


async def get_status() -> dict:
    """Get Silver Bullet status for health checks."""
    try:
        url = f"{SILVERBULLET_URL}/.ping"
        async with httpx.AsyncClient(timeout=10.0, auth=_get_auth()) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return {"status": "healthy"}
            return {"status": "unhealthy", "code": resp.status_code}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


def register_tools(mcp: FastMCP):
    """Register Silver Bullet tools with the MCP server."""

    @mcp.tool()
    async def silverbullet_list_pages(prefix: str = "") -> str:
        """List all pages/files in Silver Bullet.

        Use this to discover available notes and their metadata.

        Args:
            prefix: Optional path prefix to filter results (e.g., "journal/" or "projects/")

        Returns:
            JSON list of files with name, size, and modification time.
        """
        url = f"{SILVERBULLET_URL}/.fs"
        async with httpx.AsyncClient(timeout=30.0, auth=_get_auth()) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            files = resp.json()

        if prefix:
            files = [f for f in files if f.get("name", "").startswith(prefix)]

        result = []
        for f in files:
            result.append({
                "name": f.get("name"),
                "size": f.get("size"),
                "modified": f.get("lastModified")
            })

        return str(result)

    @mcp.tool()
    async def silverbullet_read_page(page_name: str) -> str:
        """Read a markdown page from Silver Bullet.

        Args:
            page_name: Page path (e.g., "index", "journal/2024-01-25", "projects/homelab")
                      Do not include .md extension.

        Returns:
            The markdown content of the page.
        """
        # Ensure .md extension
        if not page_name.endswith(".md"):
            page_name = f"{page_name}.md"

        result = await silverbullet_api(f"/{page_name}", method="GET")
        return result.get("content", "")

    @mcp.tool()
    async def silverbullet_write_page(page_name: str, content: str) -> str:
        """Create or update a markdown page in Silver Bullet.

        Args:
            page_name: Page path (e.g., "notes/meeting", "projects/new-idea")
                      Do not include .md extension.
            content: The markdown content to write.

        Returns:
            Confirmation message.
        """
        if not page_name.endswith(".md"):
            page_name = f"{page_name}.md"

        await silverbullet_api(f"/{page_name}", method="PUT", content=content)
        return f"Page '{page_name}' saved successfully."

    @mcp.tool()
    async def silverbullet_delete_page(page_name: str) -> str:
        """Delete a page from Silver Bullet.

        Args:
            page_name: Page path to delete.

        Returns:
            Confirmation message.
        """
        if not page_name.endswith(".md"):
            page_name = f"{page_name}.md"

        await silverbullet_api(f"/{page_name}", method="DELETE")
        return f"Page '{page_name}' deleted."

    @mcp.tool()
    async def silverbullet_search(query: str) -> str:
        """Search for content across all Silver Bullet pages.

        Searches page names and content for the query string.

        Args:
            query: Text to search for (case-insensitive).

        Returns:
            List of matching pages with excerpts.
        """
        # Get all files first
        url = f"{SILVERBULLET_URL}/.fs"
        async with httpx.AsyncClient(timeout=30.0, auth=_get_auth()) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            files = resp.json()

        # Filter to .md files and search
        matches = []
        query_lower = query.lower()

        for f in files:
            name = f.get("name", "")
            if not name.endswith(".md"):
                continue

            # Check filename match
            if query_lower in name.lower():
                matches.append({"page": name, "match": "filename"})
                continue

            # Check content match (read file)
            try:
                result = await silverbullet_api(f"/{name}", method="GET")
                content = result.get("content", "")
                if query_lower in content.lower():
                    # Find excerpt around match
                    idx = content.lower().find(query_lower)
                    start = max(0, idx - 50)
                    end = min(len(content), idx + len(query) + 50)
                    excerpt = content[start:end].replace("\n", " ")
                    matches.append({"page": name, "match": "content", "excerpt": f"...{excerpt}..."})
            except Exception:
                pass

        if not matches:
            return f"No pages found matching '{query}'"

        return str(matches)
