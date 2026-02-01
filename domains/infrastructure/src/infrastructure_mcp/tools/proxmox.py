"""Proxmox VE hypervisor management tools with multi-host support."""

import os
import json
import logging
from typing import Optional, List, Literal
from enum import Enum

import httpx
from fastmcp import FastMCP
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ResponseFormat(str, Enum):
    markdown = "markdown"
    json = "json"


# Multi-host configuration
# Each host has URL, TOKEN_ID, and TOKEN_SECRET from environment
PROXMOX_HOSTS = {
    "ruapehu": {
        "url": os.environ.get("PROXMOX_RUAPEHU_URL", "https://10.10.0.10:8006"),
        "token_id": os.environ.get("PROXMOX_RUAPEHU_TOKEN_ID", ""),
        "token_secret": os.environ.get("PROXMOX_RUAPEHU_TOKEN_SECRET", ""),
        "description": "Production cluster (10.10.0.0/24)",
    },
    "pihanga": {
        "url": os.environ.get("PROXMOX_PIHANGA_URL", "https://10.10.0.20:8006"),
        "token_id": os.environ.get("PROXMOX_PIHANGA_TOKEN_ID", ""),
        "token_secret": os.environ.get("PROXMOX_PIHANGA_TOKEN_SECRET", ""),
        "description": "Monitoring + Backup cluster (Ryzen 5 7640HS, 28GB)",
    },
    "hikurangi": {
        "url": os.environ.get("PROXMOX_HIKURANGI_URL", "https://10.30.0.10:8006"),
        "token_id": os.environ.get("PROXMOX_HIKURANGI_TOKEN_ID", ""),
        "token_secret": os.environ.get("PROXMOX_HIKURANGI_TOKEN_SECRET", ""),
        "description": "IaC + Test VMs (N150, 12GB) - formerly Carrick",
    },
}

# Legacy single-host fallback (backwards compatibility)
if os.environ.get("PROXMOX_TOKEN_ID") and not PROXMOX_HOSTS["ruapehu"]["token_id"]:
    PROXMOX_HOSTS["ruapehu"]["url"] = os.environ.get("PROXMOX_URL", "https://10.10.0.10:8006")
    PROXMOX_HOSTS["ruapehu"]["token_id"] = os.environ.get("PROXMOX_TOKEN_ID", "")
    PROXMOX_HOSTS["ruapehu"]["token_secret"] = os.environ.get("PROXMOX_TOKEN_SECRET", "")

DEFAULT_HOST = "ruapehu"

ProxmoxHost = Literal["ruapehu", "pihanga", "hikurangi", "all"]


def get_host_config(host: str) -> dict:
    """Get configuration for a specific host."""
    if host not in PROXMOX_HOSTS:
        raise ValueError(f"Unknown Proxmox host: {host}. Available: {list(PROXMOX_HOSTS.keys())}")
    return PROXMOX_HOSTS[host]


async def proxmox_api(endpoint: str, method: str = "GET", data: dict = None, host: str = DEFAULT_HOST) -> dict:
    """Make authenticated API call to a specific Proxmox host."""
    config = get_host_config(host)
    headers = {"Authorization": f"PVEAPIToken={config['token_id']}={config['token_secret']}"}
    url = f"{config['url']}/api2/json{endpoint}"

    async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
        if method == "GET":
            resp = await client.get(url, headers=headers)
        elif method == "POST":
            resp = await client.post(url, headers=headers, data=data)
        elif method == "DELETE":
            resp = await client.delete(url, headers=headers)
        else:
            raise ValueError(f"Unsupported method: {method}")

        resp.raise_for_status()
        return resp.json()


async def get_status() -> dict:
    """Get Proxmox status for health checks (all hosts)."""
    results = {}
    for host_name, config in PROXMOX_HOSTS.items():
        if not config["token_id"]:
            results[host_name] = {"status": "unconfigured"}
            continue
        try:
            result = await proxmox_api("/nodes", host=host_name)
            results[host_name] = {"status": "healthy", "nodes": len(result.get("data", []))}
        except Exception as e:
            results[host_name] = {"status": "unhealthy", "error": str(e)}

    overall = "healthy" if any(r.get("status") == "healthy" for r in results.values()) else "unhealthy"
    return {"status": overall, "hosts": results}


def register_tools(mcp: FastMCP):
    """Register Proxmox tools with the MCP server."""

    # =========================================================================
    # Input Models
    # =========================================================================

    class ListNodesInput(BaseModel):
        host: Optional[ProxmoxHost] = Field(
            default=None,
            description="Proxmox host: 'ruapehu' (prod), 'carrick' (monit), or 'all'. Defaults to all configured hosts."
        )
        response_format: ResponseFormat = Field(default=ResponseFormat.markdown, description="Output format")

    class ListVMsInput(BaseModel):
        host: Optional[ProxmoxHost] = Field(
            default=None,
            description="Proxmox host: 'ruapehu' (prod), 'carrick' (monit), or 'all'. Defaults to all configured hosts."
        )
        node: Optional[str] = Field(default=None, description="Filter by node name (e.g., 'pve1'). If omitted, lists VMs from all nodes.")
        response_format: ResponseFormat = Field(default=ResponseFormat.markdown, description="Output format")

    class VMOperationInput(BaseModel):
        host: ProxmoxHost = Field(
            default="ruapehu",
            description="Proxmox host: 'ruapehu' (prod) or 'carrick' (monit)"
        )
        node: str = Field(description="Node name where the VM is located (e.g., 'Ruapehu' or 'Carrick')", min_length=1)
        vmid: int = Field(description="VM ID number (e.g., 100)", ge=100, le=999999999)

    class StorageStatusInput(BaseModel):
        host: ProxmoxHost = Field(default="ruapehu", description="Proxmox host")
        node: str = Field(description="Node name", min_length=1)
        storage: str = Field(description="Storage name")
        response_format: ResponseFormat = Field(default=ResponseFormat.markdown)

    class SnapshotCreateInput(BaseModel):
        host: ProxmoxHost = Field(default="ruapehu", description="Proxmox host")
        node: str = Field(description="Node name", min_length=1)
        vmid: int = Field(description="VM/Container ID", ge=100)
        snapname: str = Field(description="Snapshot name")
        description: str = Field(default="", description="Snapshot description")
        vmstate: bool = Field(default=False, description="Include VM RAM (only for VMs)")

    class TaskInput(BaseModel):
        host: ProxmoxHost = Field(default="ruapehu", description="Proxmox host")
        node: str = Field(description="Node name", min_length=1)
        upid: str = Field(description="Task UPID")

    # =========================================================================
    # Helper functions
    # =========================================================================

    def get_configured_hosts() -> List[str]:
        """Get list of hosts that have credentials configured."""
        return [name for name, config in PROXMOX_HOSTS.items() if config["token_id"]]

    async def query_all_hosts(endpoint: str, host_filter: Optional[str] = None):
        """Query endpoint on all configured hosts or a specific one."""
        if host_filter and host_filter != "all":
            hosts = [host_filter]
        else:
            hosts = get_configured_hosts()

        results = {}
        for host_name in hosts:
            try:
                result = await proxmox_api(endpoint, host=host_name)
                results[host_name] = result.get("data", [])
            except Exception as e:
                logger.warning(f"Failed to query {host_name}: {e}")
                results[host_name] = []
        return results

    # =========================================================================
    # Nodes
    # =========================================================================

    @mcp.tool()
    async def proxmox_list_nodes(params: ListNodesInput) -> str:
        """List all Proxmox cluster nodes with status and resource usage.

        Returns node names, online status, CPU/memory usage, and uptime.
        Use this to discover available nodes before listing VMs.

        Supports querying multiple Proxmox hosts (ruapehu=prod, carrick=monit)."""

        host_results = await query_all_hosts("/nodes", params.host)

        if params.response_format == ResponseFormat.json:
            return json.dumps(host_results)

        output = ["# Proxmox Nodes\n"]

        for host_name, nodes in host_results.items():
            host_desc = PROXMOX_HOSTS.get(host_name, {}).get("description", "")
            output.append(f"## Host: {host_name.capitalize()}")
            if host_desc:
                output.append(f"*{host_desc}*\n")

            if not nodes:
                output.append("- No nodes found or connection failed\n")
                continue

            for node in nodes:
                status = "🟢" if node.get("status") == "online" else "🔴"
                mem_used = node.get('mem', 0) / (1024**3)
                mem_total = node.get('maxmem', 0) / (1024**3)
                output.append(f"### {status} {node.get('node')}")
                output.append(f"- Status: {node.get('status')}")
                output.append(f"- CPU: {node.get('cpu', 0) * 100:.1f}%")
                output.append(f"- Memory: {mem_used:.1f}GB / {mem_total:.1f}GB ({mem_used/mem_total*100:.0f}%)")
                output.append(f"- Uptime: {node.get('uptime', 0) // 86400}d {(node.get('uptime', 0) % 86400) // 3600}h")
                output.append("")

        return "\n".join(output)

    # =========================================================================
    # VMs
    # =========================================================================

    @mcp.tool()
    async def proxmox_list_vms(params: ListVMsInput) -> str:
        """List all QEMU virtual machines across Proxmox hosts.

        Returns VM ID, name, status, CPU, memory, and node location.
        Optionally filter by host (ruapehu/carrick) or node name."""

        # Determine which hosts to query
        if params.host and params.host != "all":
            hosts = [params.host]
        else:
            hosts = get_configured_hosts()

        all_vms = []

        for host_name in hosts:
            try:
                if params.node:
                    result = await proxmox_api(f"/nodes/{params.node}/qemu", host=host_name)
                    vms = [{"host": host_name, "node": params.node, **vm} for vm in result.get("data", [])]
                else:
                    nodes_result = await proxmox_api("/nodes", host=host_name)
                    vms = []
                    for node in nodes_result.get("data", []):
                        if node.get("status") == "online":
                            node_vms = await proxmox_api(f"/nodes/{node['node']}/qemu", host=host_name)
                            for vm in node_vms.get("data", []):
                                vms.append({"host": host_name, "node": node["node"], **vm})
                all_vms.extend(vms)
            except Exception as e:
                logger.warning(f"Failed to list VMs on {host_name}: {e}")

        if params.response_format == ResponseFormat.json:
            return json.dumps(all_vms)

        output = ["# Virtual Machines\n"]

        # Group by host for clarity
        by_host = {}
        for vm in all_vms:
            host = vm.get("host", "unknown")
            if host not in by_host:
                by_host[host] = []
            by_host[host].append(vm)

        for host_name, vms in by_host.items():
            host_desc = PROXMOX_HOSTS.get(host_name, {}).get("description", "")
            output.append(f"## {host_name.capitalize()}")
            if host_desc:
                output.append(f"*{host_desc}*\n")

            for vm in sorted(vms, key=lambda x: x.get("vmid", 0)):
                status_icon = "🟢" if vm.get("status") == "running" else "⭕"
                output.append(f"### {status_icon} {vm.get('name', 'unnamed')} (VMID: {vm.get('vmid')})")
                output.append(f"- Node: {vm.get('node')}")
                output.append(f"- Status: {vm.get('status')}")
                output.append(f"- CPU: {vm.get('cpus', 0)} cores")
                output.append(f"- Memory: {vm.get('maxmem', 0) / (1024**3):.1f}GB")
                output.append("")

        if not all_vms:
            output.append("No VMs found.\n")

        return "\n".join(output)

    @mcp.tool()
    async def proxmox_get_vm_status(params: VMOperationInput) -> dict:
        """Get detailed status of a specific VM including CPU, memory, disk, and network stats."""
        result = await proxmox_api(f"/nodes/{params.node}/qemu/{params.vmid}/status/current", host=params.host)
        return result.get("data", {})

    @mcp.tool()
    async def proxmox_start_vm(params: VMOperationInput) -> str:
        """Start a stopped VM. Returns the task ID for tracking the operation."""
        result = await proxmox_api(f"/nodes/{params.node}/qemu/{params.vmid}/status/start", method="POST", host=params.host)
        return f"Started VM {params.vmid} on {params.host}. Task: {result.get('data')}"

    @mcp.tool()
    async def proxmox_stop_vm(params: VMOperationInput) -> str:
        """Stop a running VM (hard stop). For graceful shutdown, use proxmox_shutdown_vm."""
        result = await proxmox_api(f"/nodes/{params.node}/qemu/{params.vmid}/status/stop", method="POST", host=params.host)
        return f"Stopping VM {params.vmid} on {params.host}. Task: {result.get('data')}"

    @mcp.tool()
    async def proxmox_shutdown_vm(params: VMOperationInput) -> str:
        """Gracefully shutdown a VM via ACPI. Preferred over hard stop."""
        result = await proxmox_api(f"/nodes/{params.node}/qemu/{params.vmid}/status/shutdown", method="POST", host=params.host)
        return f"Shutting down VM {params.vmid} on {params.host}. Task: {result.get('data')}"

    @mcp.tool()
    async def proxmox_reboot_vm(params: VMOperationInput) -> str:
        """Gracefully reboot a VM via ACPI."""
        result = await proxmox_api(f"/nodes/{params.node}/qemu/{params.vmid}/status/reboot", method="POST", host=params.host)
        return f"Rebooting VM {params.vmid} on {params.host}. Task: {result.get('data')}"

    # =========================================================================
    # Containers
    # =========================================================================

    @mcp.tool()
    async def proxmox_list_containers(params: ListVMsInput) -> str:
        """List all LXC containers across Proxmox hosts."""

        if params.host and params.host != "all":
            hosts = [params.host]
        else:
            hosts = get_configured_hosts()

        all_containers = []

        for host_name in hosts:
            try:
                if params.node:
                    result = await proxmox_api(f"/nodes/{params.node}/lxc", host=host_name)
                    containers = [{"host": host_name, "node": params.node, **ct} for ct in result.get("data", [])]
                else:
                    nodes_result = await proxmox_api("/nodes", host=host_name)
                    containers = []
                    for node in nodes_result.get("data", []):
                        if node.get("status") == "online":
                            node_cts = await proxmox_api(f"/nodes/{node['node']}/lxc", host=host_name)
                            for ct in node_cts.get("data", []):
                                containers.append({"host": host_name, "node": node["node"], **ct})
                all_containers.extend(containers)
            except Exception as e:
                logger.warning(f"Failed to list containers on {host_name}: {e}")

        if params.response_format == ResponseFormat.json:
            return json.dumps(all_containers)

        output = ["# LXC Containers\n"]

        by_host = {}
        for ct in all_containers:
            host = ct.get("host", "unknown")
            if host not in by_host:
                by_host[host] = []
            by_host[host].append(ct)

        for host_name, containers in by_host.items():
            host_desc = PROXMOX_HOSTS.get(host_name, {}).get("description", "")
            output.append(f"## {host_name.capitalize()}")
            if host_desc:
                output.append(f"*{host_desc}*\n")

            for ct in sorted(containers, key=lambda x: x.get("vmid", 0)):
                status_icon = "🟢" if ct.get("status") == "running" else "⭕"
                output.append(f"### {status_icon} {ct.get('name', 'unnamed')} (VMID: {ct.get('vmid')})")
                output.append(f"- Node: {ct.get('node')}")
                output.append(f"- Status: {ct.get('status')}")
                output.append(f"- Memory: {ct.get('maxmem', 0) / (1024**3):.1f}GB")
                output.append("")

        if not all_containers:
            output.append("No containers found.\n")

        return "\n".join(output)

    @mcp.tool()
    async def proxmox_get_container_status(params: VMOperationInput) -> dict:
        """Get detailed status of a specific LXC container."""
        result = await proxmox_api(f"/nodes/{params.node}/lxc/{params.vmid}/status/current", host=params.host)
        return result.get("data", {})

    @mcp.tool()
    async def proxmox_start_container(params: VMOperationInput) -> str:
        """Start a stopped LXC container."""
        result = await proxmox_api(f"/nodes/{params.node}/lxc/{params.vmid}/status/start", method="POST", host=params.host)
        return f"Started container {params.vmid} on {params.host}. Task: {result.get('data')}"

    @mcp.tool()
    async def proxmox_stop_container(params: VMOperationInput) -> str:
        """Stop a running LXC container (hard stop)."""
        result = await proxmox_api(f"/nodes/{params.node}/lxc/{params.vmid}/status/stop", method="POST", host=params.host)
        return f"Stopping container {params.vmid} on {params.host}. Task: {result.get('data')}"

    @mcp.tool()
    async def proxmox_shutdown_container(params: VMOperationInput) -> str:
        """Gracefully shutdown an LXC container."""
        result = await proxmox_api(f"/nodes/{params.node}/lxc/{params.vmid}/status/shutdown", method="POST", host=params.host)
        return f"Shutting down container {params.vmid} on {params.host}. Task: {result.get('data')}"

    @mcp.tool()
    async def proxmox_reboot_container(params: VMOperationInput) -> str:
        """Reboot an LXC container."""
        result = await proxmox_api(f"/nodes/{params.node}/lxc/{params.vmid}/status/reboot", method="POST", host=params.host)
        return f"Rebooting container {params.vmid} on {params.host}. Task: {result.get('data')}"

    # =========================================================================
    # Storage
    # =========================================================================

    @mcp.tool()
    async def proxmox_list_storage(params: ListNodesInput) -> str:
        """List all storage pools across Proxmox hosts."""

        host_results = await query_all_hosts("/storage", params.host)

        if params.response_format == ResponseFormat.json:
            return json.dumps(host_results)

        output = ["# Storage Pools\n"]

        for host_name, storage_list in host_results.items():
            host_desc = PROXMOX_HOSTS.get(host_name, {}).get("description", "")
            output.append(f"## {host_name.capitalize()}")
            if host_desc:
                output.append(f"*{host_desc}*\n")

            for s in storage_list:
                output.append(f"### {s.get('storage')}")
                output.append(f"- Type: {s.get('type')}")
                output.append(f"- Content: {s.get('content')}")
                output.append(f"- Shared: {s.get('shared', 0)}")
                output.append("")

        return "\n".join(output)

    @mcp.tool()
    async def proxmox_get_storage_status(params: StorageStatusInput) -> str:
        """Get storage pool status with usage on a specific node."""
        result = await proxmox_api(f"/nodes/{params.node}/storage/{params.storage}/status", host=params.host)
        data = result.get("data", {})

        if params.response_format == ResponseFormat.json:
            return json.dumps(data)

        used = data.get("used", 0) / (1024**3)
        total = data.get("total", 0) / (1024**3)
        pct = (used / total * 100) if total > 0 else 0

        return f"""# Storage: {params.storage} ({params.host})
- Used: {used:.1f}GB / {total:.1f}GB ({pct:.1f}%)
- Available: {(total - used):.1f}GB
- Active: {data.get('active', False)}"""

    # =========================================================================
    # Snapshots
    # =========================================================================

    @mcp.tool()
    async def proxmox_list_snapshots(params: VMOperationInput) -> List[dict]:
        """List all snapshots for a VM or container."""
        # Try VM first
        try:
            result = await proxmox_api(f"/nodes/{params.node}/qemu/{params.vmid}/snapshot", host=params.host)
            return result.get("data", [])
        except Exception:
            pass

        # Try container
        try:
            result = await proxmox_api(f"/nodes/{params.node}/lxc/{params.vmid}/snapshot", host=params.host)
            return result.get("data", [])
        except Exception:
            return [{"error": f"No VM or container found with ID {params.vmid} on {params.host}"}]

    @mcp.tool()
    async def proxmox_create_snapshot(params: SnapshotCreateInput) -> str:
        """Create a snapshot of a VM or container."""
        data = {
            "snapname": params.snapname,
            "description": params.description,
        }
        if params.vmstate:
            data["vmstate"] = 1

        # Try VM first
        try:
            result = await proxmox_api(f"/nodes/{params.node}/qemu/{params.vmid}/snapshot", method="POST", data=data, host=params.host)
            return f"Creating snapshot '{params.snapname}' on {params.host}. Task: {result.get('data')}"
        except Exception:
            pass

        # Try container
        try:
            result = await proxmox_api(f"/nodes/{params.node}/lxc/{params.vmid}/snapshot", method="POST", data=data, host=params.host)
            return f"Creating snapshot '{params.snapname}' on {params.host}. Task: {result.get('data')}"
        except Exception as e:
            return f"Error creating snapshot on {params.host}: {e}"

    # =========================================================================
    # Tasks
    # =========================================================================

    @mcp.tool()
    async def proxmox_get_task_status(params: TaskInput) -> dict:
        """Get status of a running or completed task."""
        result = await proxmox_api(f"/nodes/{params.node}/tasks/{params.upid}/status", host=params.host)
        return result.get("data", {})

    @mcp.tool()
    async def proxmox_list_tasks(params: ListNodesInput) -> str:
        """List recent tasks on all nodes across Proxmox hosts."""

        if params.host and params.host != "all":
            hosts = [params.host]
        else:
            hosts = get_configured_hosts()

        all_tasks = []

        for host_name in hosts:
            try:
                nodes_result = await proxmox_api("/nodes", host=host_name)
                for node in nodes_result.get("data", []):
                    if node.get("status") == "online":
                        tasks = await proxmox_api(f"/nodes/{node['node']}/tasks", host=host_name)
                        for task in tasks.get("data", [])[:10]:
                            task["host"] = host_name
                            task["node"] = node["node"]
                            all_tasks.append(task)
            except Exception as e:
                logger.warning(f"Failed to list tasks on {host_name}: {e}")

        if params.response_format == ResponseFormat.json:
            return json.dumps(all_tasks)

        output = ["# Recent Tasks\n"]
        for task in sorted(all_tasks, key=lambda x: x.get("starttime", 0), reverse=True)[:20]:
            status = "✅" if task.get("status") == "OK" else "❌" if task.get("status") else "⏳"
            output.append(f"- {status} [{task.get('host')}] {task.get('type')} on {task.get('node')} ({task.get('status', 'running')})")

        return "\n".join(output)

    # =========================================================================
    # Cluster
    # =========================================================================

    @mcp.tool()
    async def proxmox_get_cluster_status(params: ListNodesInput) -> str:
        """Get overall cluster status including quorum and node health across all Proxmox hosts."""

        if params.host and params.host != "all":
            hosts = [params.host]
        else:
            hosts = get_configured_hosts()

        output = ["# Cluster Status\n"]

        for host_name in hosts:
            host_desc = PROXMOX_HOSTS.get(host_name, {}).get("description", "")
            output.append(f"## {host_name.capitalize()}")
            if host_desc:
                output.append(f"*{host_desc}*\n")

            try:
                result = await proxmox_api("/cluster/status", host=host_name)
                status = result.get("data", [])

                if params.response_format == ResponseFormat.json:
                    return json.dumps({host_name: status for host_name in hosts})

                for item in status:
                    if item.get("type") == "cluster":
                        output.append(f"### Cluster: {item.get('name')}")
                        output.append(f"- Quorum: {item.get('quorate')}")
                        output.append(f"- Nodes: {item.get('nodes')}")
                    elif item.get("type") == "node":
                        status_icon = "🟢" if item.get("online") else "🔴"
                        output.append(f"- {status_icon} {item.get('name')}: {'online' if item.get('online') else 'offline'}")
                output.append("")
            except Exception as e:
                output.append(f"- Error: {e}\n")

        return "\n".join(output)
