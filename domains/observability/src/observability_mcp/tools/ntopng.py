"""ntopng network traffic monitoring tools."""

import os
import logging
from typing import Optional, Dict, Any

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

NTOPNG_URL = os.environ.get("NTOPNG_URL", "https://ntopng.kernow.io")
NTOPNG_API_TOKEN = os.environ.get("NTOPNG_API_TOKEN", "")

# Interface ID mapping for OPNsense
IFACE_MAP = {
    "pppoe0": 0,  # WAN
    "igc3": 1,    # LAN/Production
    "igc2": 2,    # AI
    "igc0": 3,    # Monit
}
IFACE_NAMES = {
    0: "WAN (pppoe0)",
    1: "LAN (igc3)",
    2: "AI (igc2)",
    3: "Monit (igc0)",
}


async def _api(endpoint: str, params: dict = None) -> Dict[str, Any]:
    """Make authenticated request to ntopng REST API v2."""
    url = f"{NTOPNG_URL}/lua/rest/v2/{endpoint}"
    headers = {"Authorization": f"Token {NTOPNG_API_TOKEN}"}
    async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
        response = await client.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()
        if data.get("rc") != 0:
            raise Exception(f"ntopng error: {data.get('rc_str_hr', data.get('rc_str'))}")
        return data.get("rsp", data)


def _fmt_bytes(b: int) -> str:
    """Format bytes to human-readable."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(b) < 1024:
            return f"{b:.1f}{unit}"
        b /= 1024
    return f"{b:.1f}PB"


def _fmt_bps(bps: float) -> str:
    """Format bits per second."""
    bps *= 8  # bytes to bits
    for unit in ("bps", "Kbps", "Mbps", "Gbps"):
        if abs(bps) < 1024:
            return f"{bps:.1f}{unit}"
        bps /= 1024
    return f"{bps:.1f}Tbps"


def register_tools(mcp: FastMCP):
    """Register ntopng tools with the MCP server."""

    @mcp.tool(name="ntopng_get_interfaces")
    async def ntopng_get_interfaces() -> str:
        """Get all ntopng-monitored network interfaces with traffic stats."""
        try:
            ifaces = await _api("get/ntopng/interfaces.lua")
            lines = ["# ntopng Interfaces", ""]
            for iface in ifaces:
                ifid = iface["ifid"]
                name = IFACE_NAMES.get(ifid, iface["ifname"])
                data = await _api("get/interface/data.lua", {"ifid": ifid})
                lines.append(f"## {name} (ifid={ifid})")
                lines.append(f"- Throughput: {_fmt_bps(data.get('throughput_bps', 0))}")
                lines.append(f"- Hosts: {data.get('num_hosts', 0)} (local: {data.get('num_local_hosts', 0)})")
                lines.append(f"- Flows: {data.get('num_flows', 0)}")
                lines.append(f"- Traffic: {_fmt_bytes(data.get('bytes', 0))}")
                lines.append(f"- Packets: {data.get('packets', 0):,}")
                tcp = data.get("tcpPacketStats", {})
                if tcp.get("retransmissions"):
                    lines.append(f"- TCP retrans: {tcp['retransmissions']:,} | lost: {tcp.get('lost', 0):,}")
                lines.append(f"- Alerts: {data.get('alerted_flows', 0)} flow alerts, {data.get('engaged_alerts', 0)} engaged")
                lines.append("")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(name="ntopng_get_interface_data")
    async def ntopng_get_interface_data(interface: str = "pppoe0") -> str:
        """Get detailed stats for a specific interface. Use: pppoe0 (WAN), igc3 (LAN), igc2 (AI), igc0 (Monit)."""
        try:
            ifid = IFACE_MAP.get(interface, 0)
            data = await _api("get/interface/data.lua", {"ifid": ifid})
            name = IFACE_NAMES.get(ifid, interface)
            lines = [f"# {name} — Detailed Stats", ""]
            lines.append(f"- **Uptime**: {data.get('uptime', 'N/A')}")
            lines.append(f"- **Speed**: {data.get('speed', 0)} Mbps")
            lines.append(f"- **Throughput**: {_fmt_bps(data.get('throughput_bps', 0))} ({data.get('throughput_pps', 0):.0f} pps)")
            tp = data.get("throughput", {})
            if tp:
                ul = tp.get("upload", {})
                dl = tp.get("download", {})
                lines.append(f"  - Upload: {_fmt_bps(ul.get('bps', 0))} ({ul.get('pps', 0):.0f} pps)")
                lines.append(f"  - Download: {_fmt_bps(dl.get('bps', 0))} ({dl.get('pps', 0):.0f} pps)")
            lines.append(f"- **Total bytes**: {_fmt_bytes(data.get('bytes', 0))}")
            lines.append(f"  - Upload: {_fmt_bytes(data.get('bytes_upload', 0))}")
            lines.append(f"  - Download: {_fmt_bytes(data.get('bytes_download', 0))}")
            lines.append(f"- **Packets**: {data.get('packets', 0):,}")
            lines.append(f"- **Hosts**: {data.get('num_hosts', 0)} total, {data.get('num_local_hosts', 0)} local")
            lines.append(f"- **Flows**: {data.get('num_flows', 0)}")
            lines.append(f"- **Drops**: {data.get('drops', 0):,} ({data.get('tot_pkt_drops', 0):,} total)")
            tcp = data.get("tcpPacketStats", {})
            lines.append(f"- **TCP stats**: retrans={tcp.get('retransmissions', 0):,}, lost={tcp.get('lost', 0):,}, ooo={tcp.get('out_of_order', 0):,}")
            lines.append(f"- **Alerted flows**: {data.get('alerted_flows', 0)} (error: {data.get('alerted_flows_error', 0)}, warn: {data.get('alerted_flows_warning', 0)}, notice: {data.get('alerted_flows_notice', 0)})")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(name="ntopng_get_active_hosts")
    async def ntopng_get_active_hosts(interface: str = "igc3", limit: int = 20) -> str:
        """Get active hosts on an interface. Default: igc3 (LAN). Shows IP, traffic, flows, score."""
        try:
            ifid = IFACE_MAP.get(interface, 1)
            data = await _api("get/host/active.lua", {"ifid": ifid, "currentPage": 1, "perPage": limit, "sortColumn": "column_traffic", "sortOrder": "desc"})
            hosts = data.get("data", []) if isinstance(data, dict) else data
            name = IFACE_NAMES.get(ifid, interface)
            lines = [f"# Active Hosts — {name}", f"Total: {len(hosts)}", ""]
            lines.append("| Host | IP | Sent | Rcvd | Flows | Score |")
            lines.append("|------|----|----|------|-------|-------|")
            for h in hosts:
                hostname = h.get("name", h.get("ip", "?"))
                ip = h.get("ip", "?")
                b = h.get("bytes", {})
                sent = _fmt_bytes(b.get("sent", 0))
                rcvd = _fmt_bytes(b.get("rcvd", 0))
                flows = h.get("num_flows", {}).get("total", 0)
                score = h.get("score", {}).get("total", 0)
                lines.append(f"| {hostname} | {ip} | {sent} | {rcvd} | {flows} | {score} |")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(name="ntopng_get_active_flows")
    async def ntopng_get_active_flows(interface: str = "igc3", limit: int = 20) -> str:
        """Get active flows on an interface. Shows src/dst, protocol, application, throughput."""
        try:
            ifid = IFACE_MAP.get(interface, 1)
            data = await _api("get/flow/active.lua", {"ifid": ifid, "currentPage": 1, "perPage": limit, "sortColumn": "column_thpt", "sortOrder": "desc"})
            flows = data.get("data", []) if isinstance(data, dict) else data
            name = IFACE_NAMES.get(ifid, interface)
            lines = [f"# Active Flows — {name}", f"Total: {len(flows)}", ""]
            for f in flows[:limit]:
                client = f.get("client", {})
                server = f.get("server", {})
                src = client.get("name", client.get("ip", "?"))
                dst = server.get("name", server.get("ip", "?"))
                proto = f.get("proto", {}).get("l7", "?")
                thpt = f.get("thpt", {}).get("bps", 0)
                b = f.get("bytes", 0)
                lines.append(f"- **{src}** → **{dst}** [{proto}] {_fmt_bps(thpt)} ({_fmt_bytes(b)})")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(name="ntopng_get_l7_stats")
    async def ntopng_get_l7_stats(interface: str = "pppoe0") -> str:
        """Get Layer-7 application protocol stats for an interface (e.g. TLS, DNS, YouTube)."""
        try:
            ifid = IFACE_MAP.get(interface, 0)
            data = await _api("get/interface/l7/stats.lua", {"ifid": ifid, "ndpistats_mode": "sinceStartup"})
            name = IFACE_NAMES.get(ifid, interface)
            lines = [f"# L7 Application Stats — {name}", ""]
            labels = data.get("labels", []) if isinstance(data, dict) else []
            series = data.get("series", []) if isinstance(data, dict) else []
            if labels and series:
                total = sum(series)
                lines.append("| Application | Bytes | % |")
                lines.append("|-------------|-------|---|")
                paired = sorted(zip(labels, series), key=lambda x: x[1], reverse=True)
                for app, b in paired:
                    pct = (b / total * 100) if total > 0 else 0
                    lines.append(f"| {app} | {_fmt_bytes(b)} | {pct:.1f}% |")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(name="ntopng_get_host_data")
    async def ntopng_get_host_data(host: str, interface: str = "igc3") -> str:
        """Get detailed data for a specific host by IP address."""
        try:
            ifid = IFACE_MAP.get(interface, 1)
            data = await _api("get/host/data.lua", {"ifid": ifid, "host": host})
            lines = [f"# Host: {data.get('name', host)}", ""]
            lines.append(f"- **IP**: {data.get('ip', host)}")
            lines.append(f"- **MAC**: {data.get('mac', 'N/A')}")
            lines.append(f"- **OS**: {data.get('os_detail', data.get('os', 'Unknown'))}")
            lines.append(f"- **Country**: {data.get('country', 'N/A')}")
            b = data.get("bytes", {})
            if isinstance(b, dict):
                lines.append(f"- **Traffic**: sent={_fmt_bytes(b.get('sent', 0))}, rcvd={_fmt_bytes(b.get('rcvd', 0))}")
            lines.append(f"- **Flows**: {data.get('active_flows.as_client', 0)} as client, {data.get('active_flows.as_server', 0)} as server")
            lines.append(f"- **Score**: {data.get('score', 0)}")
            lines.append(f"- **Alerts**: {data.get('num_alerts', 0)}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(name="ntopng_get_alerts")
    async def ntopng_get_alerts(alert_type: str = "flow", interface: str = "igc3", limit: int = 20) -> str:
        """Get ntopng alerts. Types: flow, host, interface, network, system. Default: flow alerts on LAN."""
        try:
            ifid = IFACE_MAP.get(interface, 1)
            endpoint = f"get/{alert_type}/alert/list.lua"
            data = await _api(endpoint, {"ifid": ifid, "currentPage": 1, "perPage": limit, "status": "historical"})
            records = data.get("records", []) if isinstance(data, dict) else data
            lines = [f"# ntopng {alert_type.title()} Alerts — {IFACE_NAMES.get(ifid, interface)}", ""]
            if not records:
                return f"No {alert_type} alerts found."
            for a in records[:limit]:
                severity = a.get("severity", {}).get("label", "?")
                alert_name = a.get("alert_id", {}).get("label", "?")
                ts = a.get("tstamp", {}).get("value", "?")
                flow = a.get("flow", {})
                cli = flow.get("cli_ip", {}).get("label_long", "?")
                srv = flow.get("srv_ip", {}).get("label_long", "?")
                proto = a.get("l7_proto", {}).get("label", "")
                lines.append(f"- [{severity}] **{alert_name}**: {cli} → {srv} {proto} (at {ts})")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(name="ntopng_get_alert_stats")
    async def ntopng_get_alert_stats(interface: str = "igc3") -> str:
        """Get alert severity and type counters summary for an interface."""
        try:
            ifid = IFACE_MAP.get(interface, 1)
            severity = await _api("get/alert/severity/counters.lua", {"ifid": ifid, "status": "historical"})
            types = await _api("get/alert/type/counters.lua", {"ifid": ifid, "status": "historical"})
            name = IFACE_NAMES.get(ifid, interface)
            lines = [f"# ntopng Alert Statistics — {name}", ""]
            lines.append("## By Severity")
            if isinstance(severity, list):
                for item in sorted(severity, key=lambda x: x.get("count", 0), reverse=True):
                    entity = item.get("entity_label", "")
                    sev = item.get("name", "?")
                    count = item.get("count", 0)
                    lines.append(f"- {sev} ({entity}): {count:,}")
            lines.append("")
            lines.append("## By Type")
            if isinstance(types, list):
                for item in sorted(types, key=lambda x: x.get("count", 0), reverse=True)[:15]:
                    lines.append(f"- {item.get('name', '?')}: {item.get('count', 0):,}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(name="ntopng_get_system_health")
    async def ntopng_get_system_health() -> str:
        """Get ntopng system health including Redis stats and interface summary."""
        try:
            health = await _api("get/system/health/redis.lua")
            lines = ["# ntopng System Health", ""]
            lines.append("## Redis")
            if isinstance(health, dict):
                lines.append(f"- Memory: {_fmt_bytes(health.get('memory', 0))}")
                lines.append(f"- Keys: {health.get('dbsize', 'N/A')}")
                lines.append(f"- Health: {health.get('health', 'N/A')}")
            lines.append("")
            lines.append("## Interfaces")
            ifaces = await _api("get/ntopng/interfaces.lua")
            for iface in ifaces:
                ifid = iface["ifid"]
                name = IFACE_NAMES.get(ifid, iface["ifname"])
                data = await _api("get/interface/data.lua", {"ifid": ifid})
                drops = data.get("drops", 0)
                tot_drops = data.get("tot_pkt_drops", 0)
                lines.append(f"- {name}: {data.get('num_hosts', 0)} hosts, {data.get('num_flows', 0)} flows, drops={drops:,} ({tot_drops:,} total)")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(name="ntopng_set_host_alias")
    async def ntopng_set_host_alias(host: str, alias: str) -> str:
        """Set a friendly name alias for a host IP address."""
        try:
            url = f"{NTOPNG_URL}/lua/rest/v2/set/host/alias.lua"
            headers = {"Authorization": f"Token {NTOPNG_API_TOKEN}"}
            async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
                response = await client.post(url, headers=headers, data={"host": host, "custom_name": alias})
                response.raise_for_status()
                data = response.json()
                if data.get("rc") == 0:
                    return f"Set alias for {host} to '{alias}'"
                return f"Failed: {data.get('rc_str_hr', data.get('rc_str'))}"
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(name="ntopng_set_interface_alias")
    async def ntopng_set_interface_alias(ifid: int, alias: str) -> str:
        """Set a friendly name for an interface (e.g. ifid=0 alias='WAN - Vodafone')."""
        try:
            url = f"{NTOPNG_URL}/lua/rest/v2/set/interface/alias.lua"
            headers = {"Authorization": f"Token {NTOPNG_API_TOKEN}"}
            async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
                response = await client.post(url, headers=headers, data={"ifid": ifid, "custom_name": alias})
                response.raise_for_status()
                data = response.json()
                if data.get("rc") == 0:
                    return f"Set interface {ifid} alias to '{alias}'"
                return f"Failed: {data.get('rc_str_hr', data.get('rc_str'))}"
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(name="ntopng_get_network_discovery")
    async def ntopng_get_network_discovery(interface: str = "igc3") -> str:
        """Run network discovery on an interface to find devices."""
        try:
            ifid = IFACE_MAP.get(interface, 1)
            data = await _api("get/network/discovery/discover.lua", {"ifid": ifid})
            lines = [f"# Network Discovery — {IFACE_NAMES.get(ifid, interface)}", ""]
            if isinstance(data, list):
                for d in data:
                    ip = d.get("ip", "?")
                    mac = d.get("mac", "?")
                    name = d.get("name", d.get("manufacturer", "Unknown"))
                    lines.append(f"- {ip} ({mac}) — {name}")
            elif isinstance(data, dict):
                for ip, info in data.items():
                    name = info.get("name", info.get("manufacturer", "Unknown")) if isinstance(info, dict) else str(info)
                    lines.append(f"- {ip} — {name}")
            return "\n".join(lines) if len(lines) > 2 else "No devices discovered."
        except Exception as e:
            return f"Error: {e}"
