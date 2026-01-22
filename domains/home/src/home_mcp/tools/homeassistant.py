"""Home Assistant smart home control tools."""

import os
import logging
from typing import List, Optional, Any

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
HA_URL = os.environ.get("HA_URL", "http://homeassistant.local:8123")
HA_TOKEN = os.environ.get("HA_TOKEN", "")


async def ha_request(method: str, endpoint: str, data: dict = None) -> Any:
    """Make request to Home Assistant API."""
    if not HA_TOKEN:
        return {"error": "HA_TOKEN not configured"}
    try:
        async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
            response = await client.request(
                method,
                f"{HA_URL}/api/{endpoint}",
                headers={"Authorization": f"Bearer {HA_TOKEN}"},
                json=data
            )
            response.raise_for_status()
            return response.json() if response.content else {}
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"error": str(e)}


async def call_service(domain: str, service: str, data: dict) -> dict:
    """Call a Home Assistant service."""
    result = await ha_request("POST", f"services/{domain}/{service}", data)
    if isinstance(result, dict) and "error" in result:
        return result
    return {"success": True, "service": f"{domain}.{service}"}


async def get_status() -> dict:
    """Get HA status for health checks."""
    try:
        result = await ha_request("GET", "")
        if isinstance(result, dict) and "error" not in result:
            return {"status": "healthy", "message": result.get("message", "OK")}
        return {"status": "unhealthy", "error": result.get("error", "Unknown")}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


def register_tools(mcp: FastMCP):
    """Register Home Assistant tools with the MCP server."""

    @mcp.tool()
    async def list_entities(domain: str = "all", area: Optional[str] = None) -> List[dict]:
        """List entities by domain (light, switch, climate, cover, fan, lock, media_player, scene, script, automation, sensor, binary_sensor) or 'all'."""
        try:
            states = await ha_request("GET", "states")
            if isinstance(states, dict) and "error" in states:
                return [states]

            entities = []
            for state in states:
                eid = state.get("entity_id", "")
                if domain != "all" and not eid.startswith(f"{domain}."):
                    continue
                entities.append({
                    "entity_id": eid,
                    "state": state.get("state"),
                    "friendly_name": state.get("attributes", {}).get("friendly_name"),
                    "domain": eid.split(".")[0] if "." in eid else None
                })
            return entities
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def get_entity_state(entity_id: str) -> dict:
        """Get the current state and attributes of any entity."""
        return await ha_request("GET", f"states/{entity_id}")

    @mcp.tool()
    async def get_home_overview() -> dict:
        """Get an overview of home state: lights on, climate status, open covers, etc."""
        try:
            states = await ha_request("GET", "states")
            if isinstance(states, dict) and "error" in states:
                return states

            overview = {
                "lights_on": [],
                "switches_on": [],
                "climate": [],
                "covers_open": [],
                "locks_unlocked": [],
                "media_playing": []
            }

            for state in states:
                eid = state.get("entity_id", "")
                s = state.get("state", "")
                name = state.get("attributes", {}).get("friendly_name", eid)

                if eid.startswith("light.") and s == "on":
                    overview["lights_on"].append(name)
                elif eid.startswith("switch.") and s == "on":
                    overview["switches_on"].append(name)
                elif eid.startswith("climate."):
                    overview["climate"].append({
                        "name": name,
                        "state": s,
                        "temp": state.get("attributes", {}).get("current_temperature")
                    })
                elif eid.startswith("cover.") and s == "open":
                    overview["covers_open"].append(name)
                elif eid.startswith("lock.") and s == "unlocked":
                    overview["locks_unlocked"].append(name)
                elif eid.startswith("media_player.") and s == "playing":
                    overview["media_playing"].append(name)

            return overview
        except Exception as e:
            return {"error": str(e)}

    # Light controls
    @mcp.tool()
    async def turn_on_light(entity_id: str, brightness: Optional[int] = None,
                           color_temp: Optional[int] = None, rgb_color: Optional[List[int]] = None) -> dict:
        """Turn on a light with optional brightness (0-255), color_temp (mireds), or rgb_color ([r,g,b])."""
        data = {"entity_id": entity_id}
        if brightness is not None:
            data["brightness"] = brightness
        if color_temp is not None:
            data["color_temp"] = color_temp
        if rgb_color is not None:
            data["rgb_color"] = rgb_color
        return await call_service("light", "turn_on", data)

    @mcp.tool()
    async def turn_off_light(entity_id: str) -> dict:
        """Turn off a light."""
        return await call_service("light", "turn_off", {"entity_id": entity_id})

    @mcp.tool()
    async def toggle_light(entity_id: str) -> dict:
        """Toggle a light on/off."""
        return await call_service("light", "toggle", {"entity_id": entity_id})

    # Switch controls
    @mcp.tool()
    async def turn_on_switch(entity_id: str) -> dict:
        """Turn on a switch."""
        return await call_service("switch", "turn_on", {"entity_id": entity_id})

    @mcp.tool()
    async def turn_off_switch(entity_id: str) -> dict:
        """Turn off a switch."""
        return await call_service("switch", "turn_off", {"entity_id": entity_id})

    @mcp.tool()
    async def toggle_switch(entity_id: str) -> dict:
        """Toggle a switch on/off."""
        return await call_service("switch", "toggle", {"entity_id": entity_id})

    # Climate controls
    @mcp.tool()
    async def set_climate_temperature(entity_id: str, temperature: float,
                                      hvac_mode: Optional[str] = None) -> dict:
        """Set climate/thermostat temperature. hvac_mode: heat, cool, auto, off."""
        data = {"entity_id": entity_id, "temperature": temperature}
        if hvac_mode:
            data["hvac_mode"] = hvac_mode
        return await call_service("climate", "set_temperature", data)

    @mcp.tool()
    async def set_climate_hvac_mode(entity_id: str, hvac_mode: str) -> dict:
        """Set HVAC mode: heat, cool, heat_cool, auto, dry, fan_only, off."""
        return await call_service("climate", "set_hvac_mode",
                                  {"entity_id": entity_id, "hvac_mode": hvac_mode})

    @mcp.tool()
    async def turn_off_climate(entity_id: str) -> dict:
        """Turn off climate/HVAC."""
        return await call_service("climate", "turn_off", {"entity_id": entity_id})

    # Cover controls
    @mcp.tool()
    async def open_cover(entity_id: str) -> dict:
        """Open a cover/blind."""
        return await call_service("cover", "open_cover", {"entity_id": entity_id})

    @mcp.tool()
    async def close_cover(entity_id: str) -> dict:
        """Close a cover/blind."""
        return await call_service("cover", "close_cover", {"entity_id": entity_id})

    @mcp.tool()
    async def set_cover_position(entity_id: str, position: int) -> dict:
        """Set cover position (0=closed, 100=open)."""
        return await call_service("cover", "set_cover_position",
                                  {"entity_id": entity_id, "position": position})

    @mcp.tool()
    async def stop_cover(entity_id: str) -> dict:
        """Stop cover movement."""
        return await call_service("cover", "stop_cover", {"entity_id": entity_id})

    # Fan controls
    @mcp.tool()
    async def turn_on_fan(entity_id: str, speed: Optional[str] = None) -> dict:
        """Turn on a fan with optional speed (low, medium, high)."""
        data = {"entity_id": entity_id}
        if speed:
            data["speed"] = speed
        return await call_service("fan", "turn_on", data)

    @mcp.tool()
    async def turn_off_fan(entity_id: str) -> dict:
        """Turn off a fan."""
        return await call_service("fan", "turn_off", {"entity_id": entity_id})

    @mcp.tool()
    async def set_fan_percentage(entity_id: str, percentage: int) -> dict:
        """Set fan speed percentage (0-100)."""
        return await call_service("fan", "set_percentage",
                                  {"entity_id": entity_id, "percentage": percentage})

    # Lock controls
    @mcp.tool()
    async def lock_lock(entity_id: str) -> dict:
        """Lock a lock."""
        return await call_service("lock", "lock", {"entity_id": entity_id})

    @mcp.tool()
    async def unlock_lock(entity_id: str) -> dict:
        """Unlock a lock."""
        return await call_service("lock", "unlock", {"entity_id": entity_id})

    # Media player controls
    @mcp.tool()
    async def media_play(entity_id: str) -> dict:
        """Start/resume media playback."""
        return await call_service("media_player", "media_play", {"entity_id": entity_id})

    @mcp.tool()
    async def media_pause(entity_id: str) -> dict:
        """Pause media playback."""
        return await call_service("media_player", "media_pause", {"entity_id": entity_id})

    @mcp.tool()
    async def media_stop(entity_id: str) -> dict:
        """Stop media playback."""
        return await call_service("media_player", "media_stop", {"entity_id": entity_id})

    @mcp.tool()
    async def media_next_track(entity_id: str) -> dict:
        """Skip to next track."""
        return await call_service("media_player", "media_next_track", {"entity_id": entity_id})

    @mcp.tool()
    async def media_previous_track(entity_id: str) -> dict:
        """Go to previous track."""
        return await call_service("media_player", "media_previous_track", {"entity_id": entity_id})

    @mcp.tool()
    async def set_media_volume(entity_id: str, volume_level: float) -> dict:
        """Set volume level (0.0 to 1.0)."""
        return await call_service("media_player", "volume_set",
                                  {"entity_id": entity_id, "volume_level": volume_level})

    @mcp.tool()
    async def media_mute(entity_id: str, mute: bool = True) -> dict:
        """Mute or unmute media player."""
        return await call_service("media_player", "volume_mute",
                                  {"entity_id": entity_id, "is_volume_muted": mute})

    # Scenes and scripts
    @mcp.tool()
    async def activate_scene(entity_id: str) -> dict:
        """Activate a scene."""
        return await call_service("scene", "turn_on", {"entity_id": entity_id})

    @mcp.tool()
    async def run_script(entity_id: str) -> dict:
        """Run a script."""
        return await call_service("script", "turn_on", {"entity_id": entity_id})

    # Automations
    @mcp.tool()
    async def trigger_automation(entity_id: str) -> dict:
        """Manually trigger an automation."""
        return await call_service("automation", "trigger", {"entity_id": entity_id})

    @mcp.tool()
    async def turn_on_automation(entity_id: str) -> dict:
        """Enable an automation."""
        return await call_service("automation", "turn_on", {"entity_id": entity_id})

    @mcp.tool()
    async def turn_off_automation(entity_id: str) -> dict:
        """Disable an automation."""
        return await call_service("automation", "turn_off", {"entity_id": entity_id})

    # Notifications
    @mcp.tool()
    async def send_notification(message: str, title: Optional[str] = None,
                                target: str = "notify.notify") -> dict:
        """Send a notification via Home Assistant. target: notify service entity (e.g., notify.mobile_app_phone)."""
        data = {"message": message}
        if title:
            data["title"] = title
        # Extract service name from entity
        service = target.replace("notify.", "") if target.startswith("notify.") else target
        return await call_service("notify", service, data)

    # Vacuum
    @mcp.tool()
    async def start_vacuum(entity_id: str) -> dict:
        """Start vacuum cleaning."""
        return await call_service("vacuum", "start", {"entity_id": entity_id})

    @mcp.tool()
    async def stop_vacuum(entity_id: str) -> dict:
        """Stop vacuum."""
        return await call_service("vacuum", "stop", {"entity_id": entity_id})

    @mcp.tool()
    async def return_vacuum_to_base(entity_id: str) -> dict:
        """Return vacuum to charging base."""
        return await call_service("vacuum", "return_to_base", {"entity_id": entity_id})
