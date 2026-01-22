"""Tasmota smart device control tools."""

import os
import json
import logging
import asyncio
from pathlib import Path
from typing import List, Optional, Dict, Any

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
DEVICES_FILE = os.environ.get("TASMOTA_DEVICES_FILE", "/data/tasmota-devices.json")
INITIAL_DEVICES = os.environ.get("TASMOTA_DEVICES", "")
COMMAND_TIMEOUT = float(os.environ.get("TASMOTA_TIMEOUT", "10.0"))


def load_devices() -> Dict[str, dict]:
    """Load devices from persistent storage."""
    devices = {}
    if Path(DEVICES_FILE).exists():
        try:
            with open(DEVICES_FILE, "r") as f:
                devices = json.load(f)
            logger.info(f"Loaded {len(devices)} Tasmota devices from {DEVICES_FILE}")
        except Exception as e:
            logger.error(f"Failed to load devices: {e}")
    if INITIAL_DEVICES:
        for ip in INITIAL_DEVICES.split(","):
            ip = ip.strip()
            if ip and ip not in devices:
                devices[ip] = {"ip": ip}
    return devices


def save_devices(devices: Dict[str, dict]) -> None:
    """Save devices to persistent storage."""
    try:
        Path(DEVICES_FILE).parent.mkdir(parents=True, exist_ok=True)
        with open(DEVICES_FILE, "w") as f:
            json.dump(devices, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save devices: {e}")


# Global device registry
DEVICES = load_devices()


async def tasmota_cmd(ip: str, command: str, timeout: float = COMMAND_TIMEOUT) -> dict:
    """Execute a Tasmota command via HTTP API."""
    url = f"http://{ip}/cm?cmnd={command}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
            response.raise_for_status()
            return {"success": True, "ip": ip, "command": command, "response": response.json()}
    except httpx.TimeoutException:
        return {"error": f"Timeout connecting to {ip}"}
    except Exception as e:
        return {"error": str(e)}


async def get_status() -> dict:
    """Get Tasmota status for health checks."""
    if not DEVICES:
        return {"status": "healthy", "message": "No devices registered"}

    # Check first device
    first_ip = list(DEVICES.keys())[0]
    result = await tasmota_cmd(first_ip, "Status")
    if "error" in result:
        return {"status": "unhealthy", "error": result["error"]}
    return {"status": "healthy", "devices": len(DEVICES)}


def register_tools(mcp: FastMCP):
    """Register Tasmota tools with the MCP server."""

    @mcp.tool()
    async def tasmota_list_devices() -> List[dict]:
        """List all registered Tasmota devices."""
        return [{"ip": ip, **info} for ip, info in DEVICES.items()]

    @mcp.tool()
    async def tasmota_add_device(ip: str, name: Optional[str] = None) -> dict:
        """Add a Tasmota device by IP address."""
        if ip in DEVICES:
            return {"message": f"Device {ip} already registered"}
        DEVICES[ip] = {"ip": ip, "name": name}
        save_devices(DEVICES)
        return {"success": True, "message": f"Added device {ip}"}

    @mcp.tool()
    async def tasmota_remove_device(ip: str) -> dict:
        """Remove a Tasmota device from the registry."""
        if ip not in DEVICES:
            return {"error": f"Device {ip} not found"}
        del DEVICES[ip]
        save_devices(DEVICES)
        return {"success": True, "message": f"Removed device {ip}"}

    @mcp.tool()
    async def tasmota_discover(network: str = "192.168.1", start: int = 1,
                               end: int = 254, timeout: float = 2) -> List[dict]:
        """Scan network for Tasmota devices."""
        found = []

        async def check_ip(ip: str):
            result = await tasmota_cmd(ip, "Status%200", timeout)
            if "error" not in result:
                found.append({
                    "ip": ip,
                    "status": result.get("response", {})
                })

        tasks = [check_ip(f"{network}.{i}") for i in range(start, end + 1)]
        await asyncio.gather(*tasks)
        return found

    @mcp.tool()
    async def tasmota_power(ip: str, action: str = "toggle", relay: int = 1) -> dict:
        """Control device power state. action: on, off, toggle, blink. relay: 1-based for multi-relay."""
        cmd_map = {"on": "ON", "off": "OFF", "toggle": "TOGGLE", "blink": "BLINK"}
        cmd = cmd_map.get(action.lower(), "TOGGLE")
        power_cmd = f"Power{relay}" if relay > 1 else "Power"
        return await tasmota_cmd(ip, f"{power_cmd}%20{cmd}")

    @mcp.tool()
    async def tasmota_power_all(action: str = "toggle") -> List[dict]:
        """Control power on all registered devices."""
        results = []
        for ip in DEVICES:
            result = await tasmota_power(ip, action)
            results.append(result)
        return results

    @mcp.tool()
    async def tasmota_status(ip: str) -> dict:
        """Get comprehensive device status."""
        result = await tasmota_cmd(ip, "Status%200")
        if "error" in result:
            return result
        return result.get("response", {})

    @mcp.tool()
    async def tasmota_status_all() -> List[dict]:
        """Get status of all registered devices."""
        results = []
        for ip, info in DEVICES.items():
            result = await tasmota_cmd(ip, "Status%200")
            results.append({
                "ip": ip,
                "name": info.get("name"),
                "status": result.get("response") if "error" not in result else result
            })
        return results

    @mcp.tool()
    async def tasmota_wifi_config(ip: str, ssid: Optional[str] = None,
                                   password: Optional[str] = None,
                                   ssid2: Optional[str] = None,
                                   password2: Optional[str] = None) -> dict:
        """Configure WiFi settings. If no args, show current config."""
        if not ssid:
            return await tasmota_cmd(ip, "WifiConfig")

        commands = []
        if ssid:
            commands.append(f"SSID1%20{ssid}")
        if password:
            commands.append(f"Password1%20{password}")
        if ssid2:
            commands.append(f"SSID2%20{ssid2}")
        if password2:
            commands.append(f"Password2%20{password2}")

        backlog = ";".join(commands)
        return await tasmota_cmd(ip, f"Backlog%20{backlog}")

    @mcp.tool()
    async def tasmota_mqtt_config(ip: str, host: Optional[str] = None,
                                   port: Optional[int] = None,
                                   user: Optional[str] = None,
                                   password: Optional[str] = None,
                                   topic: Optional[str] = None) -> dict:
        """Configure MQTT settings. If no args, show current config."""
        if not host and not topic:
            result = await tasmota_cmd(ip, "Status%206")
            return result.get("response", result)

        commands = []
        if host:
            commands.append(f"MqttHost%20{host}")
        if port:
            commands.append(f"MqttPort%20{port}")
        if user:
            commands.append(f"MqttUser%20{user}")
        if password:
            commands.append(f"MqttPassword%20{password}")
        if topic:
            commands.append(f"Topic%20{topic}")

        backlog = ";".join(commands)
        return await tasmota_cmd(ip, f"Backlog%20{backlog}")

    @mcp.tool()
    async def tasmota_set_name(ip: str, name: str) -> dict:
        """Set device friendly name."""
        result = await tasmota_cmd(ip, f"FriendlyName%20{name}")
        if "error" not in result and ip in DEVICES:
            DEVICES[ip]["name"] = name
            save_devices(DEVICES)
        return result

    @mcp.tool()
    async def tasmota_command(ip: str, command: str) -> dict:
        """Execute any Tasmota command directly. command: e.g., 'Status 0', 'Backlog Power ON; Delay 100; Power OFF'"""
        # URL encode spaces
        cmd = command.replace(" ", "%20")
        return await tasmota_cmd(ip, cmd)

    @mcp.tool()
    async def tasmota_upgrade(ip: str, url: Optional[str] = None) -> dict:
        """Trigger firmware upgrade. url: OTA firmware URL (optional, uses default if not specified)."""
        if url:
            return await tasmota_cmd(ip, f"OtaUrl%20{url};Upgrade%201")
        return await tasmota_cmd(ip, "Upgrade%201")

    @mcp.tool()
    async def tasmota_restart(ip: str) -> dict:
        """Restart the device."""
        return await tasmota_cmd(ip, "Restart%201")

    @mcp.tool()
    async def tasmota_get_sensors(ip: str) -> dict:
        """Get sensor readings (temperature, humidity, energy, etc.)."""
        result = await tasmota_cmd(ip, "Status%2010")
        if "error" in result:
            return result
        return result.get("response", {}).get("StatusSNS", {})

    @mcp.tool()
    async def tasmota_get_energy(ip: str) -> dict:
        """Get energy monitoring data (voltage, current, power, energy)."""
        result = await tasmota_cmd(ip, "Status%208")
        if "error" in result:
            return result
        return result.get("response", {}).get("StatusSNS", {}).get("ENERGY", {})
