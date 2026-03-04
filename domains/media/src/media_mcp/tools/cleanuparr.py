"""Cleanuparr download cleanup automation tools."""

import os
import logging
from typing import List

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
CLEANUPARR_URL = os.environ.get("CLEANUPARR_URL", "https://cleanuparr.kernow.io")
CLEANUPARR_API_KEY = os.environ.get("CLEANUPARR_API_KEY", "")

# Job types
JOB_TYPES = ["QueueCleaner", "DownloadCleaner", "MalwareBlocker", "BlacklistSynchronizer"]


async def cleanuparr_request(endpoint: str, method: str = "GET", data: dict = None) -> dict:
    """Make request to Cleanuparr API."""
    headers = {}
    if CLEANUPARR_API_KEY:
        headers["X-Api-Key"] = CLEANUPARR_API_KEY
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        url = f"{CLEANUPARR_URL}/api/{endpoint.lstrip('/')}"
        response = await client.request(method, url, json=data, headers=headers)
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
            job_type: One of QueueCleaner, DownloadCleaner, MalwareBlocker, BlacklistSynchronizer
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
            job_type: One of QueueCleaner, DownloadCleaner, MalwareBlocker, BlacklistSynchronizer
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
            job_type: One of QueueCleaner, DownloadCleaner, MalwareBlocker, BlacklistSynchronizer
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
            job_type: One of QueueCleaner, DownloadCleaner, MalwareBlocker, BlacklistSynchronizer
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
    async def cleanuparr_create_stall_rule(
        name: str,
        max_strikes: int = 3,
        privacy_type: str = "Public",
        min_completion_percentage: int = 0,
        max_completion_percentage: int = 100,
        reset_strikes_on_progress: bool = True,
        delete_private_from_client: bool = False,
    ) -> dict:
        """Create a Cleanuparr stall rule to remove downloads that stop progressing.

        Args:
            name: Descriptive name for the rule
            max_strikes: Consecutive stall checks before removal (min 3; each check = cron interval, default 5 min)
            privacy_type: Which torrents to apply: "Public", "Private", or "Both"
            min_completion_percentage: Apply rule when download is above this % complete (0 = all)
            max_completion_percentage: Apply rule when download is below this % complete (100 = all)
            reset_strikes_on_progress: Reset strike count if download resumes
            delete_private_from_client: Also remove stalled private torrents from the download client
        """
        payload = {
            "name": name,
            "enabled": True,
            "maxStrikes": max_strikes,
            "privacyType": privacy_type,
            "minCompletionPercentage": min_completion_percentage,
            "maxCompletionPercentage": max_completion_percentage,
            "resetStrikesOnProgress": reset_strikes_on_progress,
            "deletePrivateTorrentsFromClient": delete_private_from_client,
        }
        try:
            result = await cleanuparr_request("queue-rules/stall", "POST", payload)
            return {"success": True, "message": f"Stall rule '{name}' created", "result": result}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def cleanuparr_create_slow_rule(
        name: str,
        min_speed: str = "10KB/s",
        max_strikes: int = 3,
        privacy_type: str = "Public",
        min_completion_percentage: int = 0,
        max_completion_percentage: int = 100,
        max_time_hours: float = 0,
        reset_strikes_on_progress: bool = True,
        delete_private_from_client: bool = False,
        ignore_above_size: str = None,
    ) -> dict:
        """Create a Cleanuparr slow rule to remove downloads that are consistently too slow.

        Args:
            name: Descriptive name for the rule
            min_speed: Minimum acceptable speed (e.g., "10KB/s", "1MB/s"). Downloads below this get strikes.
            max_strikes: Consecutive slow checks before removal (min 3)
            privacy_type: Which torrents to apply: "Public", "Private", or "Both"
            min_completion_percentage: Apply rule when download is above this % complete
            max_completion_percentage: Apply rule when download is below this % complete
            max_time_hours: Max allowed download time in hours (0 = disabled)
            reset_strikes_on_progress: Reset strike count if speed improves
            delete_private_from_client: Also remove slow private torrents from the download client
            ignore_above_size: Skip files larger than this size (e.g., "50GB")
        """
        payload = {
            "name": name,
            "enabled": True,
            "maxStrikes": max_strikes,
            "privacyType": privacy_type,
            "minCompletionPercentage": min_completion_percentage,
            "maxCompletionPercentage": max_completion_percentage,
            "resetStrikesOnProgress": reset_strikes_on_progress,
            "deletePrivateTorrentsFromClient": delete_private_from_client,
            "minSpeed": min_speed,
            "maxTimeHours": max_time_hours,
        }
        if ignore_above_size:
            payload["ignoreAboveSize"] = ignore_above_size
        try:
            result = await cleanuparr_request("queue-rules/slow", "POST", payload)
            return {"success": True, "message": f"Slow rule '{name}' created", "result": result}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def cleanuparr_get_download_cleaner_config() -> dict:
        """Get Cleanuparr download cleaner configuration (manages seeding rules and cleanup)."""
        try:
            return await cleanuparr_request("configuration/download_cleaner")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def cleanuparr_update_download_cleaner_config(config: dict) -> dict:
        """Update Cleanuparr download cleaner configuration.

        Args:
            config: Configuration dict with fields like:
                - Enabled: bool - Enable the download cleaner
                - CronExpression: str - Cron schedule (Quartz format)
                - UseAdvancedScheduling: bool - Use cron instead of basic interval
                - Categories: list - Seeding rules per category with MaxRatio, MinSeedTime, MaxSeedTime
                - IgnoredDownloads: list - Downloads to ignore
        """
        try:
            result = await cleanuparr_request("configuration/download_cleaner", "PUT", config)
            return {"success": True, "message": "Download cleaner config updated", "result": result}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def cleanuparr_get_malware_blocker_config() -> dict:
        """Get Cleanuparr malware blocker configuration."""
        try:
            return await cleanuparr_request("configuration/malware_blocker")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def cleanuparr_update_malware_blocker_config(config: dict) -> dict:
        """Update Cleanuparr malware blocker configuration.

        Args:
            config: Configuration dict with fields like:
                - Enabled: bool - Enable the malware blocker
                - CronExpression: str - Cron schedule
                - BlocklistUrls: list - URLs of blocklists to use
        """
        try:
            result = await cleanuparr_request("configuration/malware_blocker", "PUT", config)
            return {"success": True, "message": "Malware blocker config updated", "result": result}
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
