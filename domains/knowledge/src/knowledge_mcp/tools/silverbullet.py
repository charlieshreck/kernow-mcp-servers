"""Silver Bullet markdown PKM tools for knowledge-mcp."""

import os
import re
import logging
from typing import Optional, List, Dict
import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

SILVERBULLET_URL = os.environ.get("SILVERBULLET_URL", "http://silverbullet.ai-platform.svc.cluster.local:3000")
SILVERBULLET_USER = os.environ.get("SILVERBULLET_USER", "")

# Outline configuration for sync
OUTLINE_URL = os.environ.get("OUTLINE_URL", "http://outline.outline.svc.cluster.local")
OUTLINE_API_KEY = os.environ.get("OUTLINE_API_KEY", "")

# Sync configuration
SYNC_FOLDER = "outline"  # Silver Bullet folder for synced collection notes

# Session cache for authenticated cookies
_session_cookie: Optional[str] = None


async def _get_auth_cookie() -> Optional[str]:
    """Get authentication cookie via form-based login.

    Silver Bullet uses form-based auth with cookie sessions.
    We POST to /.auth with username/password and cache the JWT cookie.
    """
    global _session_cookie

    if _session_cookie:
        return _session_cookie

    if not SILVERBULLET_USER or ":" not in SILVERBULLET_USER:
        logger.warning("SILVERBULLET_USER not set or invalid format (expected user:pass)")
        return None

    user, password = SILVERBULLET_USER.split(":", 1)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{SILVERBULLET_URL}/.auth",
                data={"username": user, "password": password}
            )
            resp.raise_for_status()

            # Extract the auth cookie from Set-Cookie header
            for cookie_header in resp.headers.get_list("set-cookie"):
                if cookie_header.startswith("auth_"):
                    # Parse cookie value (before first ;)
                    cookie_value = cookie_header.split(";")[0]
                    _session_cookie = cookie_value
                    logger.info("Silver Bullet auth cookie obtained")
                    return _session_cookie

        logger.warning("No auth cookie received from Silver Bullet")
    except Exception as e:
        logger.error(f"Failed to authenticate to Silver Bullet: {e}")

    return None


async def silverbullet_api(
    endpoint: str,
    method: str = "GET",
    content: Optional[str] = None,
    get_meta: bool = False
) -> dict:
    """Make API call to Silver Bullet."""
    url = f"{SILVERBULLET_URL}/.fs{endpoint}"
    headers = {
        "X-Sync-Mode": "true"  # Required for API access vs browser navigation
    }
    if get_meta:
        headers["X-Get-Meta"] = "true"

    # Get auth cookie
    auth_cookie = await _get_auth_cookie()
    if auth_cookie:
        headers["Cookie"] = auth_cookie

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
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
        # /.ping doesn't require auth
        url = f"{SILVERBULLET_URL}/.ping"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return {"status": "healthy"}
            return {"status": "unhealthy", "code": resp.status_code}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


# =============================================================================
# Outline API helpers for sync
# =============================================================================

async def _outline_api(endpoint: str, data: dict = None) -> dict:
    """Make authenticated API call to Outline."""
    headers = {
        "Authorization": f"Bearer {OUTLINE_API_KEY}",
        "Content-Type": "application/json"
    }
    url = f"{OUTLINE_URL}/api{endpoint}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, headers=headers, json=data or {})
        resp.raise_for_status()
        return resp.json()


async def _get_outline_collections() -> List[Dict]:
    """Get all Outline collections."""
    result = await _outline_api("/collections.list")
    return result.get("data", [])


async def _create_outline_collection(name: str, description: str = "") -> Dict:
    """Create an Outline collection."""
    result = await _outline_api("/collections.create", {
        "name": name,
        "description": description
    })
    return result.get("data", {})


async def _get_silverbullet_sync_pages() -> List[str]:
    """Get all Silver Bullet pages in the sync folder."""
    url = f"{SILVERBULLET_URL}/.fs"
    headers = {
        "X-Sync-Mode": "true"  # Required for API access
    }
    auth_cookie = await _get_auth_cookie()
    if auth_cookie:
        headers["Cookie"] = auth_cookie

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        files = resp.json()

    # Filter to pages in sync folder
    prefix = f"{SYNC_FOLDER}/"
    pages = []
    for f in files:
        name = f.get("name", "")
        if name.startswith(prefix) and name.endswith(".md"):
            # Extract collection name from path
            page_name = name[len(prefix):-3]  # Remove prefix and .md
            if "/" not in page_name:  # Only top-level pages in sync folder
                pages.append(page_name)
    return pages


def _slugify(name: str) -> str:
    """Convert collection name to safe filename."""
    # Replace spaces and special chars with hyphens
    slug = re.sub(r'[^\w\s-]', '', name.lower())
    slug = re.sub(r'[\s_]+', '-', slug)
    return slug.strip('-')


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
        headers = {
            "X-Sync-Mode": "true"  # Required for API access
        }
        auth_cookie = await _get_auth_cookie()
        if auth_cookie:
            headers["Cookie"] = auth_cookie

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            resp = await client.get(url, headers=headers)
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
        headers = {
            "X-Sync-Mode": "true"  # Required for API access
        }
        auth_cookie = await _get_auth_cookie()
        if auth_cookie:
            headers["Cookie"] = auth_cookie

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            resp = await client.get(url, headers=headers)
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

    # =========================================================================
    # Outline <-> Silver Bullet Sync Tools
    # =========================================================================

    @mcp.tool()
    async def sync_outline_to_silverbullet() -> str:
        """Sync Outline collections to Silver Bullet pages.

        Creates a notes page in Silver Bullet for each Outline collection.
        Pages are created in the 'outline/' folder with the collection name.

        Returns:
            Summary of sync actions taken.
        """
        collections = await _get_outline_collections()
        existing_pages = await _get_silverbullet_sync_pages()
        existing_slugs = {_slugify(p) for p in existing_pages}

        created = []
        skipped = []

        for coll in collections:
            name = coll.get("name", "")
            slug = _slugify(name)

            if slug in existing_slugs:
                skipped.append(name)
                continue

            # Create notes page for this collection
            page_path = f"{SYNC_FOLDER}/{name}.md"
            description = coll.get("description", "")
            content = f"""# {name}

> Notes page synced from Outline collection

{description}

---

## Notes

"""
            try:
                await silverbullet_api(f"/{page_path}", method="PUT", content=content)
                created.append(name)
            except Exception as e:
                logger.error(f"Failed to create page for {name}: {e}")

        return f"Synced Outline → Silver Bullet:\n- Created: {len(created)} ({', '.join(created) if created else 'none'})\n- Skipped (exists): {len(skipped)}"

    @mcp.tool()
    async def sync_silverbullet_to_outline() -> str:
        """Sync Silver Bullet pages to Outline collections.

        Creates an Outline collection for each page in the 'outline/' folder
        that doesn't already have a matching collection.

        Returns:
            Summary of sync actions taken.
        """
        collections = await _get_outline_collections()
        existing_slugs = {_slugify(c.get("name", "")) for c in collections}
        existing_names = {c.get("name", "").lower() for c in collections}

        sync_pages = await _get_silverbullet_sync_pages()

        created = []
        skipped = []

        for page_name in sync_pages:
            slug = _slugify(page_name)

            # Check if collection already exists (by slug or exact name)
            if slug in existing_slugs or page_name.lower() in existing_names:
                skipped.append(page_name)
                continue

            # Read page content for description
            try:
                page_path = f"{SYNC_FOLDER}/{page_name}.md"
                result = await silverbullet_api(f"/{page_path}", method="GET")
                content = result.get("content", "")

                # Extract first paragraph as description
                lines = content.split("\n")
                description = ""
                for line in lines:
                    line = line.strip()
                    if line and not line.startswith("#") and not line.startswith(">"):
                        description = line[:200]
                        break

                await _create_outline_collection(page_name, description)
                created.append(page_name)
            except Exception as e:
                logger.error(f"Failed to create collection for {page_name}: {e}")

        return f"Synced Silver Bullet → Outline:\n- Created: {len(created)} ({', '.join(created) if created else 'none'})\n- Skipped (exists): {len(skipped)}"

    @mcp.tool()
    async def sync_collections_bidirectional() -> str:
        """Bidirectional sync between Outline collections and Silver Bullet pages.

        1. Creates Silver Bullet pages for new Outline collections
        2. Creates Outline collections for new Silver Bullet pages in 'outline/' folder

        Returns:
            Combined summary of both sync directions.
        """
        # Sync Outline → Silver Bullet
        collections = await _get_outline_collections()
        sb_pages = await _get_silverbullet_sync_pages()
        sb_slugs = {_slugify(p) for p in sb_pages}
        outline_slugs = {_slugify(c.get("name", "")): c for c in collections}

        results = {"outline_to_sb": [], "sb_to_outline": []}

        # Outline → Silver Bullet
        for coll in collections:
            name = coll.get("name", "")
            slug = _slugify(name)

            if slug not in sb_slugs:
                page_path = f"{SYNC_FOLDER}/{name}.md"
                description = coll.get("description", "")
                content = f"""# {name}

> Notes page synced from Outline collection

{description}

---

## Notes

"""
                try:
                    await silverbullet_api(f"/{page_path}", method="PUT", content=content)
                    results["outline_to_sb"].append(name)
                except Exception as e:
                    logger.error(f"Failed to create SB page for {name}: {e}")

        # Refresh SB pages after creation
        sb_pages = await _get_silverbullet_sync_pages()

        # Silver Bullet → Outline
        for page_name in sb_pages:
            slug = _slugify(page_name)

            if slug not in outline_slugs:
                try:
                    page_path = f"{SYNC_FOLDER}/{page_name}.md"
                    result = await silverbullet_api(f"/{page_path}", method="GET")
                    content = result.get("content", "")

                    # Extract description
                    lines = content.split("\n")
                    description = ""
                    for line in lines:
                        line = line.strip()
                        if line and not line.startswith("#") and not line.startswith(">"):
                            description = line[:200]
                            break

                    await _create_outline_collection(page_name, description)
                    results["sb_to_outline"].append(page_name)
                except Exception as e:
                    logger.error(f"Failed to create collection for {page_name}: {e}")

        o2s = results["outline_to_sb"]
        s2o = results["sb_to_outline"]

        return f"""Bidirectional Sync Complete:

Outline → Silver Bullet:
- Created {len(o2s)} pages: {', '.join(o2s) if o2s else 'none'}

Silver Bullet → Outline:
- Created {len(s2o)} collections: {', '.join(s2o) if s2o else 'none'}"""
