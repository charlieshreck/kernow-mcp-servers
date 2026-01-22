"""Transmission torrent management tools."""

import os
import logging
import base64
from typing import List

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
TRANSMISSION_URL = os.environ.get("TRANSMISSION_URL", "https://transmission.kernow.io")
TRANSMISSION_USER = os.environ.get("TRANSMISSION_USER", "")
TRANSMISSION_PASS = os.environ.get("TRANSMISSION_PASS", "")


async def transmission_request(method: str, arguments: dict = None) -> dict:
    """Make request to Transmission RPC."""
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        auth = base64.b64encode(f"{TRANSMISSION_USER}:{TRANSMISSION_PASS}".encode()).decode()
        headers = {"Authorization": f"Basic {auth}"}
        url = f"{TRANSMISSION_URL}/transmission/rpc"

        # Get session ID first
        try:
            resp = await client.post(url, headers=headers, json={"method": "session-get"})
        except:
            pass

        if "X-Transmission-Session-Id" in resp.headers:
            headers["X-Transmission-Session-Id"] = resp.headers["X-Transmission-Session-Id"]

        payload = {"method": method}
        if arguments:
            payload["arguments"] = arguments

        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json().get("arguments", {})


async def list_torrents() -> List[dict]:
    """List torrents for health checks."""
    try:
        result = await transmission_request("torrent-get", {
            "fields": ["id", "name", "status", "percentDone", "rateDownload",
                      "rateUpload", "eta", "sizeWhenDone"]
        })
        status_map = {0: "stopped", 1: "queued", 2: "verifying", 3: "queued",
                     4: "downloading", 5: "queued", 6: "seeding"}
        return [{
            "id": t["id"],
            "name": t["name"],
            "status": status_map.get(t["status"], "unknown"),
            "progress": round(t["percentDone"] * 100, 1),
            "downloadSpeed": t.get("rateDownload", 0),
            "uploadSpeed": t.get("rateUpload", 0),
            "eta": t.get("eta", -1),
            "size": t.get("sizeWhenDone", 0)
        } for t in result.get("torrents", [])]
    except Exception as e:
        return [{"error": str(e)}]


def register_tools(mcp: FastMCP):
    """Register Transmission tools with the MCP server."""

    @mcp.tool()
    async def transmission_list_torrents() -> List[dict]:
        """List all torrents."""
        return await list_torrents()

    @mcp.tool()
    async def transmission_add_torrent(torrent_url: str, paused: bool = False) -> dict:
        """Add a torrent by URL or magnet link."""
        try:
            result = await transmission_request("torrent-add", {
                "filename": torrent_url,
                "paused": paused
            })
            added = result.get("torrent-added", result.get("torrent-duplicate", {}))
            return {"success": True, "id": added.get("id"), "name": added.get("name")}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def transmission_pause_torrent(torrent_id: int) -> dict:
        """Pause a torrent."""
        try:
            await transmission_request("torrent-stop", {"ids": [torrent_id]})
            return {"success": True, "message": f"Torrent {torrent_id} paused"}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def transmission_resume_torrent(torrent_id: int) -> dict:
        """Resume a torrent."""
        try:
            await transmission_request("torrent-start", {"ids": [torrent_id]})
            return {"success": True, "message": f"Torrent {torrent_id} resumed"}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def transmission_remove_torrent(torrent_id: int, delete_data: bool = False) -> dict:
        """Remove a torrent. Set delete_data=True to also delete downloaded files."""
        try:
            await transmission_request("torrent-remove", {
                "ids": [torrent_id],
                "delete-local-data": delete_data
            })
            return {"success": True, "message": f"Torrent {torrent_id} removed"}
        except Exception as e:
            return {"error": str(e)}
