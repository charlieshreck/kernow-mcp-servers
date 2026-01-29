"""Cleanuparr download cleanup automation tools."""

import os
import logging
from typing import List

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
CLEANUPARR_URL = os.environ.get("CLEANUPARR_URL", "https://cleanuparr.kernow.io")

# Job types
JOB_TYPES = ["QueueCleaner", "SeedingCleaner", "OrphanCleaner", "BlacklistSync"]


async def cleanuparr_request(endpoint: str, method: str = "GET", data: dict = None) -> dict:
    """Make request to Cleanuparr API."""
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        url = f"{CLEANUPARR_URL}/api/{endpoint.lstrip('/')}"
        response = await client.request(method, url, json=data)
        response.raise_for_status()
        return response.json() if response.content else {}


async def get_status() -> dict:
    """Get Cleanuparr status for health checks."""
    try:
        return await cleanuparr_request("status")
    except Exception as e:
        return {"error": str(e)}


def register_tools(mcp: FastMCP):
    """Register Cleanuparr tools with the MCP server."""

    # === Status Tools ===

    @mcp.tool()
    async def cleanuparr_get_status() -> dict:
        """Get Cleanuparr system status including version, uptime, and configured instances."""
        try:
            return await cleanuparr_request("status")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def cleanuparr_get_arrs_status() -> dict:
        """Get connection status for all configured *arr instances (Sonarr, Radarr, Lidarr, Readarr)."""
        try:
            return await cleanuparr_request("status/arrs")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def cleanuparr_get_download_client_status() -> dict:
        """Get status of configured download clients (qBittorrent, Transmission, etc)."""
        try:
            return await cleanuparr_request("status/download-client")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def cleanuparr_health() -> dict:
        """Check Cleanuparr health status."""
        try:
            return await cleanuparr_request("health")
        except Exception as e:
            return {"error": str(e)}

    # === Job Management Tools ===

    @mcp.tool()
    async def cleanuparr_list_jobs() -> List[dict]:
        """List all Cleanuparr cleanup jobs and their schedules."""
        try:
            return await cleanuparr_request("jobs")
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def cleanuparr_get_job(job_type: str) -> dict:
        """Get details of a specific cleanup job.

        Args:
            job_type: One of QueueCleaner, SeedingCleaner, OrphanCleaner, BlacklistSync
        """
        if job_type not in JOB_TYPES:
            return {"error": f"Invalid job_type. Must be one of: {JOB_TYPES}"}
        try:
            return await cleanuparr_request(f"jobs/{job_type}")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def cleanuparr_trigger_job(job_type: str) -> dict:
        """Trigger a cleanup job to run immediately.

        Args:
            job_type: One of QueueCleaner, SeedingCleaner, OrphanCleaner, BlacklistSync
        """
        if job_type not in JOB_TYPES:
            return {"error": f"Invalid job_type. Must be one of: {JOB_TYPES}"}
        try:
            result = await cleanuparr_request(f"jobs/{job_type}/trigger", "POST")
            return {"success": True, "message": f"Job {job_type} triggered", "result": result}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def cleanuparr_start_job(job_type: str, cron_schedule: str = None) -> dict:
        """Start a cleanup job with optional cron schedule.

        Args:
            job_type: One of QueueCleaner, SeedingCleaner, OrphanCleaner, BlacklistSync
            cron_schedule: Optional cron expression (e.g., "0 */6 * * *" for every 6 hours)
        """
        if job_type not in JOB_TYPES:
            return {"error": f"Invalid job_type. Must be one of: {JOB_TYPES}"}
        try:
            data = {"Schedule": cron_schedule} if cron_schedule else None
            result = await cleanuparr_request(f"jobs/{job_type}/start", "POST", data)
            return {"success": True, "message": f"Job {job_type} started", "result": result}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def cleanuparr_update_job_schedule(job_type: str, cron_schedule: str) -> dict:
        """Update the schedule for a cleanup job.

        Args:
            job_type: One of QueueCleaner, SeedingCleaner, OrphanCleaner, BlacklistSync
            cron_schedule: Cron expression (e.g., "0 */6 * * *" for every 6 hours)
        """
        if job_type not in JOB_TYPES:
            return {"error": f"Invalid job_type. Must be one of: {JOB_TYPES}"}
        try:
            result = await cleanuparr_request(
                f"jobs/{job_type}/schedule",
                "PUT",
                {"Schedule": cron_schedule}
            )
            return {"success": True, "message": f"Schedule updated for {job_type}", "result": result}
        except Exception as e:
            return {"error": str(e)}

    # === Configuration Tools ===

    @mcp.tool()
    async def cleanuparr_get_general_config() -> dict:
        """Get Cleanuparr general configuration."""
        try:
            return await cleanuparr_request("configuration/general")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def cleanuparr_update_general_config(config: dict) -> dict:
        """Update Cleanuparr general configuration.

        Args:
            config: Configuration dict with fields like:
                - DryRun: bool - Enable dry run mode (no actual deletions)
                - LogLevel: str - Log level (Debug, Info, Warning, Error)
        """
        try:
            result = await cleanuparr_request("configuration/general", "PUT", config)
            return {"success": True, "message": "General config updated", "result": result}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def cleanuparr_get_queue_cleaner_config() -> dict:
        """Get Cleanuparr queue cleaner configuration."""
        try:
            return await cleanuparr_request("configuration/queue_cleaner")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def cleanuparr_update_queue_cleaner_config(config: dict) -> dict:
        """Update Cleanuparr queue cleaner configuration.

        Args:
            config: Configuration dict with fields like:
                - Enabled: bool - Enable the queue cleaner
                - CronExpression: str - Cron schedule (e.g., "*/30 * * * *")
                - FailedImport: bool - Clean failed imports
                - DownloadingMetadataMaxStrikes: int - Max strikes before removal
                - IgnoredDownloads: list - Downloads to ignore
        """
        try:
            result = await cleanuparr_request("configuration/queue_cleaner", "PUT", config)
            return {"success": True, "message": "Queue cleaner config updated", "result": result}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def cleanuparr_get_seeding_cleaner_config() -> dict:
        """Get Cleanuparr seeding cleaner configuration."""
        try:
            return await cleanuparr_request("configuration/seeding_cleaner")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def cleanuparr_update_seeding_cleaner_config(config: dict) -> dict:
        """Update Cleanuparr seeding cleaner configuration.

        Args:
            config: Configuration dict with fields like:
                - Enabled: bool - Enable the seeding cleaner
                - CronExpression: str - Cron schedule
                - SeedingTimeMinutes: int - Minimum seeding time before cleanup
        """
        try:
            result = await cleanuparr_request("configuration/seeding_cleaner", "PUT", config)
            return {"success": True, "message": "Seeding cleaner config updated", "result": result}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def cleanuparr_get_orphan_cleaner_config() -> dict:
        """Get Cleanuparr orphan cleaner configuration."""
        try:
            return await cleanuparr_request("configuration/orphan_cleaner")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def cleanuparr_update_orphan_cleaner_config(config: dict) -> dict:
        """Update Cleanuparr orphan cleaner configuration.

        Args:
            config: Configuration dict with fields like:
                - Enabled: bool - Enable the orphan cleaner
                - CronExpression: str - Cron schedule
                - IgnoreHardlinks: bool - Ignore files with hardlinks
        """
        try:
            result = await cleanuparr_request("configuration/orphan_cleaner", "PUT", config)
            return {"success": True, "message": "Orphan cleaner config updated", "result": result}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def cleanuparr_set_dry_run(enabled: bool) -> dict:
        """Enable or disable dry run mode (no actual deletions).

        Args:
            enabled: True to enable dry run, False to disable
        """
        try:
            result = await cleanuparr_request(
                "configuration/general",
                "PUT",
                {"DryRun": enabled}
            )
            mode = "enabled" if enabled else "disabled"
            return {"success": True, "message": f"Dry run mode {mode}", "result": result}
        except Exception as e:
            return {"error": str(e)}
