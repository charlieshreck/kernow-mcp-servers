"""Proxmox VE hypervisor management tools."""

import os
import json
import logging
from typing import Optional, List
from enum import Enum

import httpx
from fastmcp import FastMCP
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Configuration
PROXMOX_URL = os.environ.get("PROXMOX_URL", "https://10.10.0.6:8006")
PROXMOX_TOKEN_ID = os.environ.get("PROXMOX_TOKEN_ID", "")
PROXMOX_TOKEN_SECRET = os.environ.get("PROXMOX_TOKEN_SECRET", "")


class ResponseFormat(str, Enum):
    markdown = "markdown"
    json = "json"


async def proxmox_api(endpoint: str, method: str = "GET", data: dict = None) -> dict:
    """Make authenticated API call to Proxmox."""
    headers = {"Authorization": f"PVEAPIToken={PROXMOX_TOKEN_ID}={PROXMOX_TOKEN_SECRET}"}
    url = f"{PROXMOX_URL}/api2/json{endpoint}"

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
    """Get Proxmox status for health checks."""
    try:
        result = await proxmox_api("/nodes")
        return {"status": "healthy", "nodes": len(result.get("data", []))}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


def register_tools(mcp: FastMCP):
    """Register Proxmox tools with the MCP server."""

    # =========================================================================
    # Input Models
    # =========================================================================

    class ListNodesInput(BaseModel):
        response_format: ResponseFormat = Field(default=ResponseFormat.markdown, description="Output format")

    class ListVMsInput(BaseModel):
        node: Optional[str] = Field(default=None, description="Filter by node name (e.g., 'pve1'). If omitted, lists VMs from all nodes.")
        response_format: ResponseFormat = Field(default=ResponseFormat.markdown, description="Output format")

    class VMOperationInput(BaseModel):
        node: str = Field(description="Node name where the VM is located (e.g., 'pve1')", min_length=1)
        vmid: int = Field(description="VM ID number (e.g., 100)", ge=100, le=999999999)

    class StorageStatusInput(BaseModel):
        node: str = Field(description="Node name", min_length=1)
        storage: str = Field(description="Storage name")
        response_format: ResponseFormat = Field(default=ResponseFormat.markdown)

    class SnapshotCreateInput(BaseModel):
        node: str = Field(description="Node name", min_length=1)
        vmid: int = Field(description="VM/Container ID", ge=100)
        snapname: str = Field(description="Snapshot name")
        description: str = Field(default="", description="Snapshot description")
        vmstate: bool = Field(default=False, description="Include VM RAM (only for VMs)")

    class TaskInput(BaseModel):
        node: str = Field(description="Node name", min_length=1)
        upid: str = Field(description="Task UPID")

    # =========================================================================
    # Nodes
    # =========================================================================

    @mcp.tool()
    async def proxmox_list_nodes(params: ListNodesInput) -> str:
        """List all Proxmox cluster nodes with status and resource usage.

        Returns node names, online status, CPU/memory usage, and uptime.
        Use this to discover available nodes before listing VMs."""
        result = await proxmox_api("/nodes")
        nodes = result.get("data", [])

        if params.response_format == ResponseFormat.json:
            return json.dumps(nodes)

        output = ["# Proxmox Nodes\n"]
        for node in nodes:
            status = "🟢" if node.get("status") == "online" else "🔴"
            output.append(f"## {status} {node.get('node')}")
            output.append(f"- Status: {node.get('status')}")
            output.append(f"- CPU: {node.get('cpu', 0) * 100:.1f}%")
            output.append(f"- Memory: {node.get('mem', 0) / (1024**3):.1f}GB / {node.get('maxmem', 0) / (1024**3):.1f}GB")
            output.append(f"- Uptime: {node.get('uptime', 0) // 86400}d {(node.get('uptime', 0) % 86400) // 3600}h")
            output.append("")

        return "\n".join(output)

    # =========================================================================
    # VMs
    # =========================================================================

    @mcp.tool()
    async def proxmox_list_vms(params: ListVMsInput) -> str:
        """List all QEMU virtual machines across the cluster.

        Returns VM ID, name, status, CPU, memory, and node location.
        Optionally filter by node name to see VMs on a specific host."""
        if params.node:
            result = await proxmox_api(f"/nodes/{params.node}/qemu")
            vms = [{"node": params.node, **vm} for vm in result.get("data", [])]
        else:
            nodes_result = await proxmox_api("/nodes")
            vms = []
            for node in nodes_result.get("data", []):
                if node.get("status") == "online":
                    node_vms = await proxmox_api(f"/nodes/{node['node']}/qemu")
                    for vm in node_vms.get("data", []):
                        vms.append({"node": node["node"], **vm})

        if params.response_format == ResponseFormat.json:
            return json.dumps(vms)

        output = ["# Virtual Machines\n"]
        for vm in sorted(vms, key=lambda x: x.get("vmid", 0)):
            status_icon = "🟢" if vm.get("status") == "running" else "⭕"
            output.append(f"## {status_icon} {vm.get('name', 'unnamed')} (VMID: {vm.get('vmid')})")
            output.append(f"- Node: {vm.get('node')}")
            output.append(f"- Status: {vm.get('status')}")
            output.append(f"- CPU: {vm.get('cpus', 0)} cores")
            output.append(f"- Memory: {vm.get('maxmem', 0) / (1024**3):.1f}GB")
            output.append("")

        return "\n".join(output)

    @mcp.tool()
    async def proxmox_get_vm_status(params: VMOperationInput) -> dict:
        """Get detailed status of a specific VM including CPU, memory, disk, and network stats."""
        result = await proxmox_api(f"/nodes/{params.node}/qemu/{params.vmid}/status/current")
        return result.get("data", {})

    @mcp.tool()
    async def proxmox_start_vm(params: VMOperationInput) -> str:
        """Start a stopped VM. Returns the task ID for tracking the operation."""
        result = await proxmox_api(f"/nodes/{params.node}/qemu/{params.vmid}/status/start", method="POST")
        return f"Started VM {params.vmid}. Task: {result.get('data')}"

    @mcp.tool()
    async def proxmox_stop_vm(params: VMOperationInput) -> str:
        """Stop a running VM (hard stop). For graceful shutdown, use proxmox_shutdown_vm."""
        result = await proxmox_api(f"/nodes/{params.node}/qemu/{params.vmid}/status/stop", method="POST")
        return f"Stopping VM {params.vmid}. Task: {result.get('data')}"

    @mcp.tool()
    async def proxmox_shutdown_vm(params: VMOperationInput) -> str:
        """Gracefully shutdown a VM via ACPI. Preferred over hard stop."""
        result = await proxmox_api(f"/nodes/{params.node}/qemu/{params.vmid}/status/shutdown", method="POST")
        return f"Shutting down VM {params.vmid}. Task: {result.get('data')}"

    @mcp.tool()
    async def proxmox_reboot_vm(params: VMOperationInput) -> str:
        """Gracefully reboot a VM via ACPI."""
        result = await proxmox_api(f"/nodes/{params.node}/qemu/{params.vmid}/status/reboot", method="POST")
        return f"Rebooting VM {params.vmid}. Task: {result.get('data')}"

    # =========================================================================
    # Containers
    # =========================================================================

    @mcp.tool()
    async def proxmox_list_containers(params: ListVMsInput) -> str:
        """List all LXC containers across the cluster."""
        if params.node:
            result = await proxmox_api(f"/nodes/{params.node}/lxc")
            containers = [{"node": params.node, **ct} for ct in result.get("data", [])]
        else:
            nodes_result = await proxmox_api("/nodes")
            containers = []
            for node in nodes_result.get("data", []):
                if node.get("status") == "online":
                    node_cts = await proxmox_api(f"/nodes/{node['node']}/lxc")
                    for ct in node_cts.get("data", []):
                        containers.append({"node": node["node"], **ct})

        if params.response_format == ResponseFormat.json:
            return json.dumps(containers)

        output = ["# LXC Containers\n"]
        for ct in sorted(containers, key=lambda x: x.get("vmid", 0)):
            status_icon = "🟢" if ct.get("status") == "running" else "⭕"
            output.append(f"## {status_icon} {ct.get('name', 'unnamed')} (VMID: {ct.get('vmid')})")
            output.append(f"- Node: {ct.get('node')}")
            output.append(f"- Status: {ct.get('status')}")
            output.append(f"- Memory: {ct.get('maxmem', 0) / (1024**3):.1f}GB")
            output.append("")

        return "\n".join(output)

    @mcp.tool()
    async def proxmox_get_container_status(params: VMOperationInput) -> dict:
        """Get detailed status of a specific LXC container."""
        result = await proxmox_api(f"/nodes/{params.node}/lxc/{params.vmid}/status/current")
        return result.get("data", {})

    @mcp.tool()
    async def proxmox_start_container(params: VMOperationInput) -> str:
        """Start a stopped LXC container."""
        result = await proxmox_api(f"/nodes/{params.node}/lxc/{params.vmid}/status/start", method="POST")
        return f"Started container {params.vmid}. Task: {result.get('data')}"

    @mcp.tool()
    async def proxmox_stop_container(params: VMOperationInput) -> str:
        """Stop a running LXC container (hard stop)."""
        result = await proxmox_api(f"/nodes/{params.node}/lxc/{params.vmid}/status/stop", method="POST")
        return f"Stopping container {params.vmid}. Task: {result.get('data')}"

    @mcp.tool()
    async def proxmox_shutdown_container(params: VMOperationInput) -> str:
        """Gracefully shutdown an LXC container."""
        result = await proxmox_api(f"/nodes/{params.node}/lxc/{params.vmid}/status/shutdown", method="POST")
        return f"Shutting down container {params.vmid}. Task: {result.get('data')}"

    @mcp.tool()
    async def proxmox_reboot_container(params: VMOperationInput) -> str:
        """Reboot an LXC container."""
        result = await proxmox_api(f"/nodes/{params.node}/lxc/{params.vmid}/status/reboot", method="POST")
        return f"Rebooting container {params.vmid}. Task: {result.get('data')}"

    # =========================================================================
    # Storage
    # =========================================================================

    @mcp.tool()
    async def proxmox_list_storage(params: ListNodesInput) -> str:
        """List all storage pools across the cluster."""
        result = await proxmox_api("/storage")
        storage = result.get("data", [])

        if params.response_format == ResponseFormat.json:
            return json.dumps(storage)

        output = ["# Storage Pools\n"]
        for s in storage:
            output.append(f"## {s.get('storage')}")
            output.append(f"- Type: {s.get('type')}")
            output.append(f"- Content: {s.get('content')}")
            output.append(f"- Shared: {s.get('shared', 0)}")
            output.append("")

        return "\n".join(output)

    @mcp.tool()
    async def proxmox_get_storage_status(params: StorageStatusInput) -> str:
        """Get storage pool status with usage on a specific node."""
        result = await proxmox_api(f"/nodes/{params.node}/storage/{params.storage}/status")
        data = result.get("data", {})

        if params.response_format == ResponseFormat.json:
            return json.dumps(data)

        used = data.get("used", 0) / (1024**3)
        total = data.get("total", 0) / (1024**3)
        pct = (used / total * 100) if total > 0 else 0

        return f"""# Storage: {params.storage}
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
            result = await proxmox_api(f"/nodes/{params.node}/qemu/{params.vmid}/snapshot")
            return result.get("data", [])
        except Exception:
            pass

        # Try container
        try:
            result = await proxmox_api(f"/nodes/{params.node}/lxc/{params.vmid}/snapshot")
            return result.get("data", [])
        except Exception:
            return [{"error": f"No VM or container found with ID {params.vmid}"}]

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
            result = await proxmox_api(f"/nodes/{params.node}/qemu/{params.vmid}/snapshot", method="POST", data=data)
            return f"Creating snapshot '{params.snapname}'. Task: {result.get('data')}"
        except Exception:
            pass

        # Try container
        try:
            result = await proxmox_api(f"/nodes/{params.node}/lxc/{params.vmid}/snapshot", method="POST", data=data)
            return f"Creating snapshot '{params.snapname}'. Task: {result.get('data')}"
        except Exception as e:
            return f"Error creating snapshot: {e}"

    # =========================================================================
    # Tasks
    # =========================================================================

    @mcp.tool()
    async def proxmox_get_task_status(params: TaskInput) -> dict:
        """Get status of a running or completed task."""
        result = await proxmox_api(f"/nodes/{params.node}/tasks/{params.upid}/status")
        return result.get("data", {})

    @mcp.tool()
    async def proxmox_list_tasks(params: ListNodesInput) -> str:
        """List recent tasks on all nodes."""
        nodes_result = await proxmox_api("/nodes")
        all_tasks = []

        for node in nodes_result.get("data", []):
            if node.get("status") == "online":
                tasks = await proxmox_api(f"/nodes/{node['node']}/tasks")
                for task in tasks.get("data", [])[:10]:
                    task["node"] = node["node"]
                    all_tasks.append(task)

        if params.response_format == ResponseFormat.json:
            return json.dumps(all_tasks)

        output = ["# Recent Tasks\n"]
        for task in sorted(all_tasks, key=lambda x: x.get("starttime", 0), reverse=True)[:20]:
            status = "✅" if task.get("status") == "OK" else "❌" if task.get("status") else "⏳"
            output.append(f"- {status} {task.get('type')} on {task.get('node')} ({task.get('status', 'running')})")

        return "\n".join(output)

    # =========================================================================
    # Cluster
    # =========================================================================

    @mcp.tool()
    async def proxmox_get_cluster_status(params: ListNodesInput) -> str:
        """Get overall cluster status including quorum and node health."""
        result = await proxmox_api("/cluster/status")
        status = result.get("data", [])

        if params.response_format == ResponseFormat.json:
            return json.dumps(status)

        output = ["# Cluster Status\n"]
        for item in status:
            if item.get("type") == "cluster":
                output.append(f"## Cluster: {item.get('name')}")
                output.append(f"- Quorum: {item.get('quorate')}")
                output.append(f"- Nodes: {item.get('nodes')}")
            elif item.get("type") == "node":
                status_icon = "🟢" if item.get("online") else "🔴"
                output.append(f"- {status_icon} {item.get('name')}: {'online' if item.get('online') else 'offline'}")

        return "\n".join(output)
