"""Maintainerr Plex media maintenance tools."""

import os
import logging
from typing import List

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
MAINTAINERR_URL = os.environ.get("MAINTAINERR_URL", "https://maintainerr.kernow.io")


async def maintainerr_request(endpoint: str, method: str = "GET", data: dict = None) -> dict:
    """Make request to Maintainerr API."""
    async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
        url = f"{MAINTAINERR_URL}/api/{endpoint.lstrip('/')}"
        response = await client.request(method, url, json=data)
        response.raise_for_status()
        return response.json() if response.content else {}


async def get_status() -> dict:
    """Get Maintainerr status for health checks."""
    try:
        return await maintainerr_request("settings/version")
    except Exception as e:
        return {"error": str(e)}


def register_tools(mcp: FastMCP):
    """Register Maintainerr tools with the MCP server."""

    # === Settings Tools ===

    @mcp.tool()
    async def maintainerr_get_version() -> dict:
        """Get Maintainerr version information."""
        try:
            return await maintainerr_request("settings/version")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def maintainerr_get_settings() -> dict:
        """Get Maintainerr general settings."""
        try:
            return await maintainerr_request("settings")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def maintainerr_update_settings(settings: dict) -> dict:
        """Update Maintainerr general settings.

        Args:
            settings: Settings dict with fields to update
        """
        try:
            result = await maintainerr_request("settings", "PATCH", settings)
            return {"success": True, "message": "Settings updated", "result": result}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def maintainerr_test_setup() -> dict:
        """Test Maintainerr setup and connections to Plex and other services."""
        try:
            return await maintainerr_request("settings/test/setup")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def maintainerr_test_plex() -> dict:
        """Test Maintainerr connection to Plex."""
        try:
            return await maintainerr_request("settings/test/plex")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def maintainerr_get_sonarr_settings() -> List[dict]:
        """Get Maintainerr Sonarr configuration."""
        try:
            return await maintainerr_request("settings/sonarr")
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def maintainerr_get_radarr_settings() -> List[dict]:
        """Get Maintainerr Radarr configuration."""
        try:
            return await maintainerr_request("settings/radarr")
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def maintainerr_get_tautulli_settings() -> dict:
        """Get Maintainerr Tautulli configuration."""
        try:
            return await maintainerr_request("settings/tautulli")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def maintainerr_get_overseerr_settings() -> dict:
        """Get Maintainerr Overseerr configuration."""
        try:
            return await maintainerr_request("settings/overseerr")
        except Exception as e:
            return {"error": str(e)}

    # === Rules Tools ===

    @mcp.tool()
    async def maintainerr_list_rules(active_only: bool = False) -> List[dict]:
        """List all Maintainerr rule groups.

        Args:
            active_only: If True, only return active rule groups
        """
        try:
            params = f"?activeOnly={str(active_only).lower()}" if active_only else ""
            return await maintainerr_request(f"rules{params}")
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def maintainerr_get_rule(rule_id: int) -> dict:
        """Get a specific rule group by ID.

        Args:
            rule_id: The rule group ID
        """
        try:
            return await maintainerr_request(f"rules/{rule_id}")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def maintainerr_create_rule(rule: dict) -> dict:
        """Create a new rule group.

        Args:
            rule: Rule configuration dict with fields like:
                - name: str - Rule group name
                - description: str - Description
                - libraryId: int - Plex library ID
                - isActive: bool - Whether rule is active
                - rules: list - List of rule conditions
        """
        try:
            result = await maintainerr_request("rules", "POST", rule)
            return {"success": True, "message": "Rule created", "result": result}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def maintainerr_update_rule(rule: dict) -> dict:
        """Update an existing rule group.

        Args:
            rule: Rule configuration dict including the rule ID
        """
        try:
            result = await maintainerr_request("rules", "PUT", rule)
            return {"success": True, "message": "Rule updated", "result": result}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def maintainerr_delete_rule(rule_id: int) -> dict:
        """Delete a rule group.

        Args:
            rule_id: The rule group ID to delete
        """
        try:
            await maintainerr_request(f"rules/{rule_id}", "DELETE")
            return {"success": True, "message": f"Rule {rule_id} deleted"}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def maintainerr_execute_all_rules() -> dict:
        """Execute all active rules immediately."""
        try:
            await maintainerr_request("rules/execute", "POST")
            return {"success": True, "message": "All rules execution started"}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def maintainerr_execute_rule(rule_id: int) -> dict:
        """Execute a specific rule immediately.

        Args:
            rule_id: The rule group ID to execute
        """
        try:
            await maintainerr_request(f"rules/{rule_id}/execute", "POST")
            return {"success": True, "message": f"Rule {rule_id} execution started"}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def maintainerr_stop_rules_execution() -> dict:
        """Stop the currently running rules execution."""
        try:
            await maintainerr_request("rules/execute/stop", "POST")
            return {"success": True, "message": "Rules execution stop requested"}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def maintainerr_get_rules_execution_status() -> dict:
        """Get the current status of rules execution."""
        try:
            return await maintainerr_request("rules/execute/status")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def maintainerr_test_rule(rule_id: int, media_id: str) -> dict:
        """Test a rule against a specific media item.

        Args:
            rule_id: The rule group ID
            media_id: The Plex media ID to test against
        """
        try:
            result = await maintainerr_request(
                "rules/test",
                "POST",
                {"rulegroupId": rule_id, "mediaId": media_id}
            )
            return result
        except Exception as e:
            return {"error": str(e)}

    # === Collections Tools ===

    @mcp.tool()
    async def maintainerr_list_collections(library_id: int = None) -> List[dict]:
        """List all Maintainerr collections.

        Args:
            library_id: Optional filter by Plex library ID
        """
        try:
            params = f"?libraryId={library_id}" if library_id else ""
            return await maintainerr_request(f"collections{params}")
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def maintainerr_get_collection(collection_id: int) -> dict:
        """Get a specific collection.

        Args:
            collection_id: The collection ID
        """
        try:
            return await maintainerr_request(f"collections/collection/{collection_id}")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def maintainerr_create_collection(collection: dict, media: List[dict] = None) -> dict:
        """Create a new collection.

        Args:
            collection: Collection configuration dict
            media: Optional list of media items to add initially
        """
        try:
            result = await maintainerr_request(
                "collections",
                "POST",
                {"collection": collection, "media": media or []}
            )
            return {"success": True, "message": "Collection created", "result": result}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def maintainerr_update_collection(collection: dict) -> dict:
        """Update an existing collection.

        Args:
            collection: Collection configuration dict including the collection ID
        """
        try:
            result = await maintainerr_request("collections", "PUT", collection)
            return {"success": True, "message": "Collection updated", "result": result}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def maintainerr_delete_collection(collection_id: int) -> dict:
        """Delete a collection.

        Args:
            collection_id: The collection ID to delete
        """
        try:
            await maintainerr_request(
                "collections/removeCollection",
                "POST",
                {"collectionId": collection_id}
            )
            return {"success": True, "message": f"Collection {collection_id} deleted"}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def maintainerr_add_to_collection(collection_id: int, media: List[dict], manual: bool = True) -> dict:
        """Add media items to a collection.

        Args:
            collection_id: The collection ID
            media: List of media items with plexId
            manual: Whether this is a manual addition
        """
        try:
            await maintainerr_request(
                "collections/add",
                "POST",
                {"collectionId": collection_id, "media": media, "manual": manual}
            )
            return {"success": True, "message": f"Media added to collection {collection_id}"}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def maintainerr_remove_from_collection(collection_id: int, media: List[dict]) -> dict:
        """Remove media items from a collection.

        Args:
            collection_id: The collection ID
            media: List of media items with plexId
        """
        try:
            await maintainerr_request(
                "collections/remove",
                "POST",
                {"collectionId": collection_id, "media": media}
            )
            return {"success": True, "message": f"Media removed from collection {collection_id}"}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def maintainerr_activate_collection(collection_id: int) -> dict:
        """Activate a collection.

        Args:
            collection_id: The collection ID to activate
        """
        try:
            await maintainerr_request(f"collections/activate/{collection_id}")
            return {"success": True, "message": f"Collection {collection_id} activated"}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def maintainerr_deactivate_collection(collection_id: int) -> dict:
        """Deactivate a collection.

        Args:
            collection_id: The collection ID to deactivate
        """
        try:
            await maintainerr_request(f"collections/deactivate/{collection_id}")
            return {"success": True, "message": f"Collection {collection_id} deactivated"}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def maintainerr_trigger_collection_handler() -> dict:
        """Trigger the collection handler to process all collections."""
        try:
            await maintainerr_request("collections/handle", "POST")
            return {"success": True, "message": "Collection handler triggered"}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def maintainerr_update_collection_schedule(schedule: str) -> dict:
        """Update the collection handler schedule.

        Args:
            schedule: Cron expression for the schedule
        """
        try:
            result = await maintainerr_request(
                "collections/schedule/update",
                "PUT",
                {"schedule": schedule}
            )
            return {"success": True, "message": "Schedule updated", "result": result}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def maintainerr_get_collection_media(collection_id: int, page: int = 1, size: int = 25) -> dict:
        """Get media items in a collection with pagination.

        Args:
            collection_id: The collection ID
            page: Page number (1-indexed)
            size: Items per page
        """
        try:
            return await maintainerr_request(f"collections/media/{collection_id}/content/{page}?size={size}")
        except Exception as e:
            return {"error": str(e)}

    # === Exclusion Tools ===

    @mcp.tool()
    async def maintainerr_get_exclusions(rule_group_id: int = None) -> List[dict]:
        """Get rule exclusions.

        Args:
            rule_group_id: Optional filter by rule group ID
        """
        try:
            params = f"?rulegroupId={rule_group_id}" if rule_group_id else ""
            return await maintainerr_request(f"rules/exclusion{params}")
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def maintainerr_add_exclusion(plex_id: int, rule_group_id: int) -> dict:
        """Add an exclusion for a media item from a rule.

        Args:
            plex_id: The Plex media ID
            rule_group_id: The rule group ID
        """
        try:
            result = await maintainerr_request(
                "rules/exclusion",
                "POST",
                {"plexId": plex_id, "ruleGroupId": rule_group_id, "action": "ADD"}
            )
            return {"success": True, "message": f"Exclusion added for plex ID {plex_id}", "result": result}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def maintainerr_remove_exclusion(exclusion_id: int) -> dict:
        """Remove an exclusion by ID.

        Args:
            exclusion_id: The exclusion ID to remove
        """
        try:
            await maintainerr_request(f"rules/exclusion/{exclusion_id}", "DELETE")
            return {"success": True, "message": f"Exclusion {exclusion_id} removed"}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def maintainerr_get_task_status(task_id: str) -> dict:
        """Get status of a Maintainerr background task.

        Args:
            task_id: The task ID
        """
        try:
            return await maintainerr_request(f"tasks/{task_id}/status")
        except Exception as e:
            return {"error": str(e)}
