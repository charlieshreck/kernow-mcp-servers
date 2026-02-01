"""TrueNAS storage management tools."""

import os
import json
import logging
from typing import Optional, List
from enum import Enum

import httpx
from fastmcp import FastMCP
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Multi-instance configuration
TRUENAS_INSTANCES = {
    "hdd": {
        "url": os.environ.get("TRUENAS_HDD_URL", "https://10.10.0.51"),
        "api_key": os.environ.get("TRUENAS_HDD_API_KEY", ""),
        "description": "Bulk HDD storage"
    },
    "media": {
        "url": os.environ.get("TRUENAS_MEDIA_URL", "https://10.10.0.52"),
        "api_key": os.environ.get("TRUENAS_MEDIA_API_KEY", ""),
        "description": "Media SSD storage"
    }
}


class ResponseFormat(str, Enum):
    markdown = "markdown"
    json = "json"


async def truenas_api(instance: str, endpoint: str, method: str = "GET", data: dict = None) -> dict:
    """Make authenticated API call to TrueNAS."""
    config = TRUENAS_INSTANCES.get(instance)
    if not config:
        raise ValueError(f"Unknown instance: {instance}")

    headers = {"Authorization": f"Bearer {config['api_key']}"}
    url = f"{config['url']}/api/v2.0{endpoint}"

    async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
        if method == "GET":
            resp = await client.get(url, headers=headers)
        elif method == "POST":
            resp = await client.post(url, headers=headers, json=data)
        elif method == "PUT":
            resp = await client.put(url, headers=headers, json=data)
        elif method == "DELETE":
            resp = await client.delete(url, headers=headers)
        else:
            raise ValueError(f"Unsupported method: {method}")
        resp.raise_for_status()
        return resp.json()


async def get_status() -> dict:
    """Get TrueNAS status for health checks."""
    statuses = {}
    for instance in TRUENAS_INSTANCES:
        try:
            await truenas_api(instance, "/system/info")
            statuses[instance] = "healthy"
        except Exception as e:
            statuses[instance] = f"unhealthy: {str(e)[:50]}"

    all_healthy = all(s == "healthy" for s in statuses.values())
    return {"status": "healthy" if all_healthy else "degraded", "instances": statuses}


def register_tools(mcp: FastMCP):
    """Register TrueNAS tools with the MCP server."""

    class InstanceInput(BaseModel):
        instance: str = Field(default="hdd", description="TrueNAS instance: 'hdd' or 'media'")
        response_format: ResponseFormat = Field(default=ResponseFormat.markdown, description="Output format")

    class DatasetInput(BaseModel):
        instance: str = Field(default="hdd", description="TrueNAS instance: 'hdd' or 'media'")
        pool: Optional[str] = Field(default=None, description="Filter by pool name")
        response_format: ResponseFormat = Field(default=ResponseFormat.markdown, description="Output format")

    class SnapshotInput(BaseModel):
        instance: str = Field(default="hdd", description="TrueNAS instance: 'hdd' or 'media'")
        dataset: Optional[str] = Field(default=None, description="Filter by dataset name")
        response_format: ResponseFormat = Field(default=ResponseFormat.markdown, description="Output format")

    @mcp.tool()
    async def truenas_list_instances() -> List[dict]:
        """List available TrueNAS instances with their hostnames and purposes."""
        return [
            {"name": name, "url": config["url"], "description": config["description"]}
            for name, config in TRUENAS_INSTANCES.items()
        ]

    @mcp.tool()
    async def truenas_list_pools(params: InstanceInput) -> str:
        """List ZFS storage pools with health status and capacity."""
        pools = await truenas_api(params.instance, "/pool")

        if params.response_format == ResponseFormat.json:
            return json.dumps(pools)

        output = [f"# ZFS Pools ({params.instance})\n"]
        for pool in pools:
            health = "🟢" if pool.get("healthy") else "🔴"
            used = pool.get("topology", {}).get("data", [{}])[0].get("stats", {}).get("allocated", 0) / (1024**4)
            total = pool.get("topology", {}).get("data", [{}])[0].get("stats", {}).get("size", 0) / (1024**4)
            output.append(f"## {health} {pool.get('name')}")
            output.append(f"- Status: {pool.get('status')}")
            output.append(f"- Capacity: {used:.2f}TB / {total:.2f}TB")
            output.append("")

        return "\n".join(output)

    @mcp.tool()
    async def truenas_list_datasets(params: DatasetInput) -> str:
        """List ZFS datasets/filesystems with quotas and usage."""
        datasets = await truenas_api(params.instance, "/pool/dataset")

        if params.pool:
            datasets = [d for d in datasets if d.get("pool") == params.pool]

        if params.response_format == ResponseFormat.json:
            return json.dumps(datasets)

        output = [f"# Datasets ({params.instance})\n"]
        for ds in datasets[:30]:  # Limit output
            used = ds.get("used", {}).get("parsed", 0) / (1024**3)
            avail = ds.get("available", {}).get("parsed", 0) / (1024**3)
            output.append(f"- **{ds.get('name')}**: {used:.1f}GB used, {avail:.1f}GB available")

        return "\n".join(output)

    @mcp.tool()
    async def truenas_list_shares(params: InstanceInput) -> str:
        """List all SMB and NFS shares configured on the instance."""
        smb_shares = await truenas_api(params.instance, "/sharing/smb")
        nfs_shares = await truenas_api(params.instance, "/sharing/nfs")

        if params.response_format == ResponseFormat.json:
            return json.dumps({"smb": smb_shares, "nfs": nfs_shares})

        output = [f"# Shares ({params.instance})\n"]

        output.append("## SMB Shares")
        for share in smb_shares:
            enabled = "✅" if share.get("enabled") else "❌"
            output.append(f"- {enabled} {share.get('name')}: {share.get('path')}")

        output.append("\n## NFS Shares")
        for share in nfs_shares:
            enabled = "✅" if share.get("enabled") else "❌"
            output.append(f"- {enabled} {share.get('path')}")

        return "\n".join(output)

    @mcp.tool()
    async def truenas_get_alerts(params: InstanceInput) -> str:
        """Get active alerts from a TrueNAS instance."""
        alerts = await truenas_api(params.instance, "/alert/list")

        if params.response_format == ResponseFormat.json:
            return json.dumps(alerts)

        if not alerts:
            return f"No active alerts on {params.instance}"

        output = [f"# Alerts ({params.instance})\n"]
        for alert in alerts:
            level = "🔴" if alert.get("level") == "CRITICAL" else "🟡" if alert.get("level") == "WARNING" else "ℹ️"
            output.append(f"- {level} [{alert.get('level')}] {alert.get('formatted')}")

        return "\n".join(output)

    @mcp.tool()
    async def truenas_get_all_alerts() -> str:
        """Get alerts from ALL TrueNAS instances at once."""
        all_alerts = []
        for instance in TRUENAS_INSTANCES:
            try:
                alerts = await truenas_api(instance, "/alert/list")
                for alert in alerts:
                    alert["instance"] = instance
                    all_alerts.append(alert)
            except Exception as e:
                all_alerts.append({"instance": instance, "error": str(e)})

        if not all_alerts:
            return "No alerts across all TrueNAS instances"

        output = ["# All TrueNAS Alerts\n"]
        for alert in all_alerts:
            if "error" in alert:
                output.append(f"- ❌ {alert['instance']}: {alert['error']}")
            else:
                level = "🔴" if alert.get("level") == "CRITICAL" else "🟡"
                output.append(f"- {level} [{alert['instance']}] {alert.get('formatted')}")

        return "\n".join(output)

    @mcp.tool()
    async def truenas_list_snapshots(params: SnapshotInput) -> str:
        """List ZFS snapshots, optionally filtered by dataset."""
        snapshots = await truenas_api(params.instance, "/zfs/snapshot")

        if params.dataset:
            snapshots = [s for s in snapshots if s.get("dataset") == params.dataset]

        if params.response_format == ResponseFormat.json:
            return json.dumps(snapshots[:50])

        output = [f"# Snapshots ({params.instance})\n"]
        for snap in snapshots[:30]:
            output.append(f"- {snap.get('name')}")

        if len(snapshots) > 30:
            output.append(f"\n... and {len(snapshots) - 30} more")

        return "\n".join(output)

    @mcp.tool()
    async def truenas_get_disk_usage(params: InstanceInput) -> str:
        """Get disk usage summary across all pools."""
        pools = await truenas_api(params.instance, "/pool")

        if params.response_format == ResponseFormat.json:
            return json.dumps(pools)

        output = [f"# Disk Usage ({params.instance})\n"]
        total_used = 0
        total_size = 0

        for pool in pools:
            topology = pool.get("topology", {})
            for vdev in topology.get("data", []):
                stats = vdev.get("stats", {})
                used = stats.get("allocated", 0)
                size = stats.get("size", 0)
                total_used += used
                total_size += size

        output.append(f"- Total Used: {total_used / (1024**4):.2f}TB")
        output.append(f"- Total Capacity: {total_size / (1024**4):.2f}TB")
        output.append(f"- Utilization: {(total_used / total_size * 100) if total_size > 0 else 0:.1f}%")

        return "\n".join(output)

    class CreateDatasetInput(BaseModel):
        instance: str = Field(default="hdd", description="TrueNAS instance: 'hdd' or 'media'")
        name: str = Field(..., description="Full dataset path (e.g., 'Tekapo/victoria-metrics')")
        comments: Optional[str] = Field(default=None, description="Optional dataset comment")

    @mcp.tool()
    async def truenas_create_dataset(params: CreateDatasetInput) -> str:
        """Create a new ZFS dataset under an existing pool/dataset."""
        data = {
            "name": params.name,
            "type": "FILESYSTEM",
            "compression": "LZ4",
            "atime": "OFF",
            "acltype": "POSIX",
            "aclmode": "DISCARD",
        }
        if params.comments:
            data["comments"] = params.comments

        result = await truenas_api(params.instance, "/pool/dataset", method="POST", data=data)
        return f"Created dataset: {result.get('name', params.name)} at {result.get('mountpoint', 'unknown')}"

    class CreateNFSShareInput(BaseModel):
        instance: str = Field(default="hdd", description="TrueNAS instance: 'hdd' or 'media'")
        path: str = Field(..., description="Full path to share (e.g., '/mnt/Tekapo/victoria-metrics')")
        networks: List[str] = Field(default_factory=list, description="Allowed networks (e.g., ['10.10.0.0/24'])")
        comment: Optional[str] = Field(default=None, description="Optional share comment")
        maproot_user: str = Field(default="root", description="Map root to this user")
        maproot_group: str = Field(default="wheel", description="Map root to this group")

    @mcp.tool()
    async def truenas_create_nfs_share(params: CreateNFSShareInput) -> str:
        """Create an NFS share for a dataset path."""
        data = {
            "path": params.path,
            "networks": params.networks,
            "maproot_user": params.maproot_user,
            "maproot_group": params.maproot_group,
            "enabled": True,
        }
        if params.comment:
            data["comment"] = params.comment

        result = await truenas_api(params.instance, "/sharing/nfs", method="POST", data=data)
        return f"Created NFS share: {result.get('path')} (ID: {result.get('id')})"
