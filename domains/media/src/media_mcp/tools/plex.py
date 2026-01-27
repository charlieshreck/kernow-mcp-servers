"""Plex Media Server tools."""

import os
import logging
import subprocess
import tempfile
from typing import Optional, Any

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
PLEX_URL = os.environ.get("PLEX_URL", "http://10.10.0.50:32400")
PLEX_TOKEN = os.environ.get("PLEX_TOKEN", "")
PLEX_HOST = os.environ.get("PLEX_HOST", "10.10.0.50")
PLEX_SSH_KEY_PATH = os.environ.get("PLEX_SSH_KEY_PATH", "")

# Prepare SSH key at module load (fix Infisical trailing newline stripping)
_ssh_key_file = None
if PLEX_SSH_KEY_PATH and os.path.isfile(PLEX_SSH_KEY_PATH):
    try:
        with open(PLEX_SSH_KEY_PATH, "r") as f:
            key_data = f.read()
        if not key_data.endswith("\n"):
            key_data += "\n"
        fd, _ssh_key_file = tempfile.mkstemp(prefix="plex_ssh_")
        with os.fdopen(fd, "w") as f:
            f.write(key_data)
        os.chmod(_ssh_key_file, 0o600)
        logger.info("SSH key prepared for Plex GPU monitoring")
    except Exception as e:
        logger.warning(f"Failed to prepare SSH key: {e}")
        _ssh_key_file = None


def _ssh_base_cmd() -> list:
    """Build base SSH command with optional key."""
    cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5"]
    if _ssh_key_file:
        cmd.extend(["-i", _ssh_key_file])
    return cmd


async def plex_request(endpoint: str, method: str = "GET") -> Any:
    """Make request to Plex API."""
    if not PLEX_TOKEN:
        return {"error": "PLEX_TOKEN not configured"}
    try:
        headers = {
            "X-Plex-Token": PLEX_TOKEN,
            "Accept": "application/json"
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            if method == "GET":
                response = await client.get(f"{PLEX_URL}{endpoint}", headers=headers)
            else:
                response = await client.request(method, f"{PLEX_URL}{endpoint}", headers=headers)
            response.raise_for_status()
            return response.json() if response.content else {}
    except httpx.HTTPStatusError as e:
        logger.error(f"Plex request failed: {e}")
        return {"error": f"HTTP {e.response.status_code}"}
    except Exception as e:
        logger.error(f"Plex request error: {e}")
        return {"error": str(e)}


# Standalone functions for health checks
async def get_server_status() -> dict:
    """Get Plex server identity, version, and claim status."""
    try:
        data = await plex_request("/identity")
        if "error" in data:
            return data
        mc = data.get("MediaContainer", data)
        return {
            "version": mc.get("version", "Unknown"),
            "machine_id": mc.get("machineIdentifier", "Unknown")[:16] + "...",
            "claimed": mc.get("claimed", False),
            "platform": mc.get("platform", "Unknown")
        }
    except Exception as e:
        return {"error": str(e)}


def register_tools(mcp: FastMCP):
    """Register Plex tools with the MCP server."""

    @mcp.tool()
    async def plex_get_server_status() -> dict:
        """Get Plex server identity, version, and claim status."""
        return await get_server_status()

    @mcp.tool()
    async def plex_list_libraries() -> list:
        """List all Plex libraries with item counts."""
        try:
            data = await plex_request("/library/sections")
            if "error" in data:
                return [data]
            dirs = data.get("MediaContainer", {}).get("Directory", [])
            return [{
                "key": d.get("key"),
                "title": d.get("title"),
                "type": d.get("type"),
                "agent": d.get("agent"),
                "scanner": d.get("scanner")
            } for d in dirs]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def plex_get_library_details(library_key: str) -> dict:
        """Get detailed info for a specific library including item counts."""
        try:
            data = await plex_request(f"/library/sections/{library_key}/all")
            if "error" in data:
                return data
            mc = data.get("MediaContainer", {})
            return {
                "title": mc.get("title1", "Unknown"),
                "total_items": mc.get("size", 0),
                "view_group": mc.get("viewGroup"),
                "library_section_id": library_key
            }
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def plex_get_active_sessions() -> list:
        """Get currently active playback sessions."""
        try:
            data = await plex_request("/status/sessions")
            if "error" in data:
                return [data]
            mc = data.get("MediaContainer", {})
            size = mc.get("size", 0)
            if size == 0:
                return []
            sessions = mc.get("Metadata", [])
            return [{
                "title": s.get("title", "Unknown"),
                "user": s.get("User", {}).get("title", "Unknown"),
                "player": s.get("Player", {}).get("product", "Unknown"),
                "state": s.get("Player", {}).get("state", "Unknown"),
                "progress_percent": round(s.get("viewOffset", 0) / max(s.get("duration", 1), 1) * 100, 1),
                "transcode": "Session" in s.get("TranscodeSession", {}) if isinstance(s.get("TranscodeSession"), dict) else False
            } for s in sessions]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def plex_get_transcode_sessions() -> list:
        """Get active transcode sessions with codec info."""
        try:
            data = await plex_request("/transcode/sessions")
            if "error" in data:
                return [data]
            mc = data.get("MediaContainer", {})
            sessions = mc.get("TranscodeSession", [])
            if not sessions:
                return []
            return [{
                "video_codec_in": s.get("videoCodec"),
                "video_codec_out": s.get("transcodeVideoCodec"),
                "hw_requested": s.get("transcodeHwRequested", False),
                "hw_full_pipeline": s.get("transcodeHwFullPipeline", False),
                "progress": round(s.get("progress", 0), 1),
                "speed": round(s.get("speed", 0), 2),
                "throttled": s.get("throttled", False)
            } for s in sessions]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def plex_get_recently_added(limit: int = 10) -> list:
        """Get recently added content."""
        try:
            data = await plex_request(f"/library/recentlyAdded")
            if "error" in data:
                return [data]
            items = data.get("MediaContainer", {}).get("Metadata", [])[:limit]
            return [{
                "title": i.get("title"),
                "year": i.get("year", "N/A"),
                "type": i.get("type"),
                "added_at": i.get("addedAt")
            } for i in items]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def plex_search_library(query: str) -> list:
        """Search across all Plex libraries."""
        try:
            import urllib.parse
            encoded_query = urllib.parse.quote(query)
            data = await plex_request(f"/search?query={encoded_query}")
            if "error" in data:
                return [data]
            items = data.get("MediaContainer", {}).get("Metadata", [])[:15]
            return [{
                "title": i.get("title"),
                "year": i.get("year", "N/A"),
                "type": i.get("type"),
                "library": i.get("librarySectionTitle")
            } for i in items]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def plex_get_on_deck() -> list:
        """Get on-deck items (continue watching)."""
        try:
            data = await plex_request("/library/onDeck")
            if "error" in data:
                return [data]
            items = data.get("MediaContainer", {}).get("Metadata", [])[:10]
            return [{
                "title": i.get("title"),
                "grandparent_title": i.get("grandparentTitle"),
                "progress_percent": round(i.get("viewOffset", 0) / max(i.get("duration", 1), 1) * 100, 1),
                "type": i.get("type")
            } for i in items]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def plex_get_gpu_status() -> dict:
        """Get GPU status from Plex VM via nvidia-smi."""
        try:
            ssh_cmd = _ssh_base_cmd() + [
                f"root@{PLEX_HOST}", "nvidia-smi",
                "--query-gpu=name,driver_version,memory.used,memory.total,temperature.gpu,utilization.gpu,utilization.encoder,utilization.decoder",
                "--format=csv,noheader,nounits"]
            result = subprocess.run(
                ssh_cmd, capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                parts = [p.strip() for p in result.stdout.strip().split(",")]
                if len(parts) >= 8:
                    return {
                        "gpu_name": parts[0],
                        "driver_version": parts[1],
                        "memory_used_mb": int(parts[2]),
                        "memory_total_mb": int(parts[3]),
                        "temperature_c": int(parts[4]),
                        "gpu_utilization_percent": int(parts[5]),
                        "encoder_utilization_percent": int(parts[6]),
                        "decoder_utilization_percent": int(parts[7])
                    }
            return {"error": f"nvidia-smi failed: {result.stderr}"}
        except subprocess.TimeoutExpired:
            return {"error": "SSH timeout connecting to Plex VM"}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def plex_get_gpu_processes() -> list:
        """Get GPU processes (what's using the GPU)."""
        try:
            ssh_cmd = _ssh_base_cmd() + [
                f"root@{PLEX_HOST}", "nvidia-smi",
                "--query-compute-apps=pid,name,used_memory",
                "--format=csv,noheader,nounits"]
            result = subprocess.run(
                ssh_cmd, capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split("\n")
                processes = []
                for line in lines:
                    if line.strip():
                        parts = [p.strip() for p in line.split(",")]
                        if len(parts) >= 3:
                            processes.append({
                                "pid": parts[0],
                                "name": parts[1],
                                "memory_mb": int(parts[2])
                            })
                return processes if processes else [{"info": "No GPU processes running"}]
            return [{"error": f"nvidia-smi failed: {result.stderr}"}]
        except subprocess.TimeoutExpired:
            return [{"error": "SSH timeout"}]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def plex_refresh_library(library_key: Optional[str] = None) -> dict:
        """Trigger library scan. Provide library_key or scans all."""
        try:
            if library_key:
                await plex_request(f"/library/sections/{library_key}/refresh", method="GET")
                return {"success": True, "message": f"Triggered refresh for library {library_key}"}
            else:
                data = await plex_request("/library/sections")
                if "error" in data:
                    return data
                dirs = data.get("MediaContainer", {}).get("Directory", [])
                for d in dirs:
                    await plex_request(f"/library/sections/{d.get('key')}/refresh", method="GET")
                return {"success": True, "message": f"Triggered refresh for all {len(dirs)} libraries"}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def plex_empty_trash(library_key: Optional[str] = None) -> dict:
        """Empty trash for a library or all libraries."""
        try:
            if library_key:
                await plex_request(f"/library/sections/{library_key}/emptyTrash", method="PUT")
                return {"success": True, "message": f"Emptied trash for library {library_key}"}
            else:
                data = await plex_request("/library/sections")
                if "error" in data:
                    return data
                dirs = data.get("MediaContainer", {}).get("Directory", [])
                for d in dirs:
                    await plex_request(f"/library/sections/{d.get('key')}/emptyTrash", method="PUT")
                return {"success": True, "message": f"Emptied trash for all {len(dirs)} libraries"}
        except Exception as e:
            return {"error": str(e)}
