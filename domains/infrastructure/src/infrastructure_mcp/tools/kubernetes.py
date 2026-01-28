"""Kubernetes cluster management tools using kubectl."""

import os
import json
import subprocess
import logging
from typing import List, Optional

from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Multi-cluster kubeconfig paths
KUBECONFIGS = {
    "agentic": None,  # Uses in-cluster service account
    "prod": "/kubeconfigs/prod/kubeconfig",
    "monit": "/kubeconfigs/monit/kubeconfig",
}


def get_kubeconfig(cluster: str = "agentic") -> Optional[str]:
    """Get kubeconfig path for a cluster."""
    return KUBECONFIGS.get(cluster)


def run_kubectl(args: List[str], timeout: int = 30, cluster: str = "agentic") -> tuple:
    """Run kubectl command and return (stdout, stderr, returncode)."""
    try:
        cmd = ["kubectl"]
        kubeconfig = get_kubeconfig(cluster)
        if kubeconfig:
            cmd.extend(["--kubeconfig", kubeconfig])
        cmd.extend(args)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.stdout, result.stderr, result.returncode
    except Exception as e:
        return "", str(e), 1


def parse_json_output(stdout: str) -> dict:
    """Parse JSON output from kubectl."""
    try:
        return json.loads(stdout)
    except Exception:
        return {}


async def get_status() -> dict:
    """Get Kubernetes status for health checks."""
    try:
        stdout, stderr, rc = run_kubectl(["get", "nodes", "--no-headers"])
        if rc == 0:
            return {"status": "healthy", "nodes": len(stdout.strip().split("\n")) if stdout.strip() else 0}
        return {"status": "unhealthy", "error": stderr}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


def register_tools(mcp: FastMCP):
    """Register Kubernetes tools with the MCP server."""

    # =========================================================================
    # Pods
    # =========================================================================

    @mcp.tool()
    async def kubectl_get_pods(
        namespace: str = "default",
        label_selector: Optional[str] = None,
        all_namespaces: bool = False,
        cluster: str = "agentic"
    ) -> List[dict]:
        """Get pods with status, readiness, and restart count.
        Use all_namespaces=True to get pods across all namespaces.
        cluster: agentic (default), prod, or monit."""
        args = ["get", "pods", "-o", "json"]
        if all_namespaces:
            args.append("-A")
        else:
            args.extend(["-n", namespace])
        if label_selector:
            args.extend(["-l", label_selector])

        stdout, stderr, rc = run_kubectl(args, cluster=cluster)
        if rc != 0:
            return [{"error": stderr}]

        data = parse_json_output(stdout)
        return [{
            "name": pod["metadata"]["name"],
            "namespace": pod["metadata"]["namespace"],
            "status": pod["status"]["phase"],
            "ready": all(c.get("ready", False) for c in pod["status"].get("containerStatuses", [])),
            "restarts": sum(c.get("restartCount", 0) for c in pod["status"].get("containerStatuses", []))
        } for pod in data.get("items", [])]

    @mcp.tool()
    async def kubectl_logs(
        pod_name: str,
        namespace: str = "default",
        tail_lines: int = 100,
        container: Optional[str] = None,
        previous: bool = False
    ) -> str:
        """Get logs from a Kubernetes pod. Use previous=True for crashed container logs."""
        args = ["logs", pod_name, "-n", namespace, f"--tail={tail_lines}"]
        if container:
            args.extend(["-c", container])
        if previous:
            args.append("--previous")

        stdout, stderr, rc = run_kubectl(args)
        return stdout if rc == 0 else f"Error: {stderr}"

    @mcp.tool()
    async def kubectl_delete_pod(pod_name: str, namespace: str = "default") -> str:
        """Delete a pod (useful for forcing restart of a specific pod)."""
        stdout, stderr, rc = run_kubectl(["delete", "pod", pod_name, "-n", namespace])
        return f"Deleted pod {pod_name}" if rc == 0 else f"Error: {stderr}"

    # =========================================================================
    # Deployments
    # =========================================================================

    @mcp.tool()
    async def kubectl_get_deployments(
        namespace: str = "default",
        all_namespaces: bool = False,
        cluster: str = "agentic"
    ) -> List[dict]:
        """Get deployments with replica status.
        cluster: agentic (default), prod, or monit."""
        args = ["get", "deployments", "-o", "json"]
        if all_namespaces:
            args.append("-A")
        else:
            args.extend(["-n", namespace])

        stdout, stderr, rc = run_kubectl(args, cluster=cluster)
        if rc != 0:
            return [{"error": stderr}]

        data = parse_json_output(stdout)
        return [{
            "name": d["metadata"]["name"],
            "namespace": d["metadata"]["namespace"],
            "replicas": d["spec"].get("replicas", 0),
            "ready": d["status"].get("readyReplicas", 0),
            "available": d["status"].get("availableReplicas", 0),
            "updated": d["status"].get("updatedReplicas", 0)
        } for d in data.get("items", [])]

    @mcp.tool()
    async def kubectl_restart_deployment(deployment_name: str, namespace: str = "default") -> str:
        """Restart a deployment by triggering a rolling restart."""
        stdout, stderr, rc = run_kubectl(["rollout", "restart", "deployment", deployment_name, "-n", namespace])
        return f"Restarted {deployment_name}" if rc == 0 else f"Error: {stderr}"

    @mcp.tool()
    async def kubectl_scale_deployment(deployment_name: str, replicas: int, namespace: str = "default") -> str:
        """Scale a deployment to specified number of replicas."""
        stdout, stderr, rc = run_kubectl(["scale", "deployment", deployment_name, f"--replicas={replicas}", "-n", namespace])
        return f"Scaled {deployment_name} to {replicas} replicas" if rc == 0 else f"Error: {stderr}"

    @mcp.tool()
    async def kubectl_rollout_status(deployment_name: str, namespace: str = "default") -> str:
        """Get rollout status of a deployment."""
        stdout, stderr, rc = run_kubectl(["rollout", "status", "deployment", deployment_name, "-n", namespace, "--timeout=5s"])
        return stdout if rc == 0 else stderr

    # =========================================================================
    # Services
    # =========================================================================

    @mcp.tool()
    async def kubectl_get_services(
        namespace: str = "default",
        all_namespaces: bool = False,
        cluster: str = "agentic"
    ) -> List[dict]:
        """Get services with type, cluster IP, and ports.
        cluster: agentic (default), prod, or monit."""
        args = ["get", "services", "-o", "json"]
        if all_namespaces:
            args.append("-A")
        else:
            args.extend(["-n", namespace])

        stdout, stderr, rc = run_kubectl(args, cluster=cluster)
        if rc != 0:
            return [{"error": stderr}]

        data = parse_json_output(stdout)
        return [{
            "name": s["metadata"]["name"],
            "namespace": s["metadata"]["namespace"],
            "type": s["spec"].get("type", "ClusterIP"),
            "cluster_ip": s["spec"].get("clusterIP"),
            "ports": [{"port": p.get("port"), "target": p.get("targetPort"), "nodePort": p.get("nodePort")} for p in s["spec"].get("ports", [])]
        } for s in data.get("items", [])]

    # =========================================================================
    # StatefulSets
    # =========================================================================

    @mcp.tool()
    async def kubectl_get_statefulsets(
        namespace: str = "default",
        all_namespaces: bool = False,
        cluster: str = "agentic"
    ) -> List[dict]:
        """Get statefulsets with replica status.
        cluster: agentic (default), prod, or monit."""
        args = ["get", "statefulsets", "-o", "json"]
        if all_namespaces:
            args.append("-A")
        else:
            args.extend(["-n", namespace])

        stdout, stderr, rc = run_kubectl(args, cluster=cluster)
        if rc != 0:
            return [{"error": stderr}]

        data = parse_json_output(stdout)
        return [{
            "name": s["metadata"]["name"],
            "namespace": s["metadata"]["namespace"],
            "replicas": s["spec"].get("replicas", 0),
            "ready": s["status"].get("readyReplicas", 0)
        } for s in data.get("items", [])]

    @mcp.tool()
    async def kubectl_restart_statefulset(statefulset_name: str, namespace: str = "default") -> str:
        """Restart a statefulset."""
        stdout, stderr, rc = run_kubectl(["rollout", "restart", "statefulset", statefulset_name, "-n", namespace])
        return f"Restarted {statefulset_name}" if rc == 0 else f"Error: {stderr}"

    # =========================================================================
    # DaemonSets
    # =========================================================================

    @mcp.tool()
    async def kubectl_get_daemonsets(
        namespace: str = "default",
        all_namespaces: bool = False,
        cluster: str = "agentic"
    ) -> List[dict]:
        """Get daemonsets with scheduling status.
        cluster: agentic (default), prod, or monit."""
        args = ["get", "daemonsets", "-o", "json"]
        if all_namespaces:
            args.append("-A")
        else:
            args.extend(["-n", namespace])

        stdout, stderr, rc = run_kubectl(args, cluster=cluster)
        if rc != 0:
            return [{"error": stderr}]

        data = parse_json_output(stdout)
        return [{
            "name": d["metadata"]["name"],
            "namespace": d["metadata"]["namespace"],
            "desired": d["status"].get("desiredNumberScheduled", 0),
            "ready": d["status"].get("numberReady", 0),
            "available": d["status"].get("numberAvailable", 0)
        } for d in data.get("items", [])]

    # =========================================================================
    # Jobs/CronJobs
    # =========================================================================

    @mcp.tool()
    async def kubectl_get_jobs(
        namespace: str = "default",
        all_namespaces: bool = False
    ) -> List[dict]:
        """Get jobs with completion status."""
        args = ["get", "jobs", "-o", "json"]
        if all_namespaces:
            args.append("-A")
        else:
            args.extend(["-n", namespace])

        stdout, stderr, rc = run_kubectl(args)
        if rc != 0:
            return [{"error": stderr}]

        data = parse_json_output(stdout)
        return [{
            "name": j["metadata"]["name"],
            "namespace": j["metadata"]["namespace"],
            "completions": j["spec"].get("completions", 1),
            "succeeded": j["status"].get("succeeded", 0),
            "failed": j["status"].get("failed", 0),
            "active": j["status"].get("active", 0)
        } for j in data.get("items", [])]

    @mcp.tool()
    async def kubectl_get_cronjobs(
        namespace: str = "default",
        all_namespaces: bool = False
    ) -> List[dict]:
        """Get cronjobs with schedule and last run info."""
        args = ["get", "cronjobs", "-o", "json"]
        if all_namespaces:
            args.append("-A")
        else:
            args.extend(["-n", namespace])

        stdout, stderr, rc = run_kubectl(args)
        if rc != 0:
            return [{"error": stderr}]

        data = parse_json_output(stdout)
        return [{
            "name": c["metadata"]["name"],
            "namespace": c["metadata"]["namespace"],
            "schedule": c["spec"].get("schedule"),
            "suspended": c["spec"].get("suspend", False),
            "last_schedule": c["status"].get("lastScheduleTime")
        } for c in data.get("items", [])]

    @mcp.tool()
    async def kubectl_create_job_from_cronjob(cronjob_name: str, job_name: str, namespace: str = "default") -> str:
        """Manually trigger a cronjob by creating a job from it."""
        stdout, stderr, rc = run_kubectl(["create", "job", job_name, f"--from=cronjob/{cronjob_name}", "-n", namespace])
        return f"Created job {job_name} from cronjob {cronjob_name}" if rc == 0 else f"Error: {stderr}"

    # =========================================================================
    # ConfigMaps/Secrets
    # =========================================================================

    @mcp.tool()
    async def kubectl_get_configmaps(namespace: str = "default") -> List[dict]:
        """Get configmap names and data keys."""
        stdout, stderr, rc = run_kubectl(["get", "configmaps", "-n", namespace, "-o", "json"])
        if rc != 0:
            return [{"error": stderr}]

        data = parse_json_output(stdout)
        return [{
            "name": c["metadata"]["name"],
            "keys": list(c.get("data", {}).keys())
        } for c in data.get("items", [])]

    @mcp.tool()
    async def kubectl_get_secrets(namespace: str = "default") -> List[dict]:
        """Get secret names and types (values NOT exposed for security)."""
        stdout, stderr, rc = run_kubectl(["get", "secrets", "-n", namespace, "-o", "json"])
        if rc != 0:
            return [{"error": stderr}]

        data = parse_json_output(stdout)
        return [{
            "name": s["metadata"]["name"],
            "type": s.get("type"),
            "keys": list(s.get("data", {}).keys())
        } for s in data.get("items", [])]

    # =========================================================================
    # Storage
    # =========================================================================

    @mcp.tool()
    async def kubectl_get_pvcs(
        namespace: str = "default",
        all_namespaces: bool = False,
        cluster: str = "agentic"
    ) -> List[dict]:
        """Get persistent volume claims with status and capacity.
        cluster: agentic (default), prod, or monit."""
        args = ["get", "pvc", "-o", "json"]
        if all_namespaces:
            args.append("-A")
        else:
            args.extend(["-n", namespace])

        stdout, stderr, rc = run_kubectl(args, cluster=cluster)
        if rc != 0:
            return [{"error": stderr}]

        data = parse_json_output(stdout)
        return [{
            "name": p["metadata"]["name"],
            "namespace": p["metadata"]["namespace"],
            "status": p["status"].get("phase"),
            "capacity": p["status"].get("capacity", {}).get("storage"),
            "storage_class": p["spec"].get("storageClassName"),
            "volume_name": p["spec"].get("volumeName")
        } for p in data.get("items", [])]

    # =========================================================================
    # Nodes
    # =========================================================================

    @mcp.tool()
    async def kubectl_get_nodes(cluster: str = "agentic") -> List[dict]:
        """Get all cluster nodes with status, version, and conditions.
        cluster: agentic (default), prod, or monit."""
        stdout, stderr, rc = run_kubectl(["get", "nodes", "-o", "json"], cluster=cluster)
        if rc != 0:
            return [{"error": stderr}]

        data = parse_json_output(stdout)
        nodes = []
        for n in data.get("items", []):
            conditions = {c["type"]: c["status"] for c in n["status"].get("conditions", [])}
            nodes.append({
                "name": n["metadata"]["name"],
                "ready": conditions.get("Ready") == "True",
                "version": n["status"].get("nodeInfo", {}).get("kubeletVersion", "unknown"),
                "os": n["status"].get("nodeInfo", {}).get("osImage", "unknown"),
                "conditions": conditions
            })
        return nodes

    # =========================================================================
    # Namespaces
    # =========================================================================

    @mcp.tool()
    async def kubectl_get_namespaces(cluster: str = "agentic") -> List[dict]:
        """Get all namespaces in the cluster.
        cluster: agentic (default), prod, or monit."""
        stdout, stderr, rc = run_kubectl(["get", "namespaces", "-o", "json"], cluster=cluster)
        if rc != 0:
            return [{"error": stderr}]

        data = parse_json_output(stdout)
        return [{
            "name": ns["metadata"]["name"],
            "status": ns["status"].get("phase")
        } for ns in data.get("items", [])]

    # =========================================================================
    # Events
    # =========================================================================

    @mcp.tool()
    async def kubectl_get_events(
        namespace: str = "default",
        limit: int = 20,
        warning_only: bool = False,
        cluster: str = "agentic"
    ) -> List[dict]:
        """Get Kubernetes events. Use warning_only=True to filter warnings.
        cluster: agentic (default), prod, or monit."""
        stdout, stderr, rc = run_kubectl(["get", "events", "-n", namespace, "-o", "json", "--sort-by=.lastTimestamp"], cluster=cluster)
        if rc != 0:
            return [{"error": stderr}]

        data = parse_json_output(stdout)
        events = data.get("items", [])[-limit:]
        if warning_only:
            events = [e for e in events if e.get("type") == "Warning"]

        return [{
            "type": e.get("type"),
            "reason": e.get("reason"),
            "message": e.get("message", "")[:200],
            "object": f"{e.get('involvedObject', {}).get('kind', '')}/{e.get('involvedObject', {}).get('name', '')}",
            "count": e.get("count", 1),
            "last_seen": e.get("lastTimestamp")
        } for e in events]

    # =========================================================================
    # Ingresses
    # =========================================================================

    @mcp.tool()
    async def kubectl_get_ingresses(
        namespace: str = "default",
        all_namespaces: bool = False,
        cluster: str = "agentic"
    ) -> List[dict]:
        """Get ingresses with hosts and paths.
        cluster: agentic (default), prod, or monit."""
        args = ["get", "ingress", "-o", "json"]
        if all_namespaces:
            args.append("-A")
        else:
            args.extend(["-n", namespace])

        stdout, stderr, rc = run_kubectl(args, cluster=cluster)
        if rc != 0:
            return [{"error": stderr}]

        data = parse_json_output(stdout)
        result = []
        for i in data.get("items", []):
            hosts = []
            for rule in i["spec"].get("rules", []):
                host = rule.get("host", "*")
                path_entries = []
                for p in rule.get("http", {}).get("paths", []):
                    entry = {"path": p.get("path", "/")}
                    backend = p.get("backend", {}).get("service", {})
                    if backend:
                        entry["service"] = backend.get("name", "")
                        port = backend.get("port", {})
                        entry["port"] = port.get("number") or port.get("name", "")
                    path_entries.append(entry)
                hosts.append({"host": host, "paths": path_entries})
            tls_hosts = [t.get("hosts", []) for t in i["spec"].get("tls", [])]
            result.append({
                "name": i["metadata"]["name"],
                "namespace": i["metadata"]["namespace"],
                "class": i["spec"].get("ingressClassName"),
                "hosts": hosts,
                "tls": len(tls_hosts) > 0
            })
        return result

    # =========================================================================
    # ArgoCD
    # =========================================================================

    @mcp.tool()
    async def argocd_get_applications(namespace: str = "argocd") -> List[dict]:
        """Get ArgoCD applications with sync status and destination info."""
        # ArgoCD runs in prod cluster, not agentic
        stdout, stderr, rc = run_kubectl(["get", "applications.argoproj.io", "-n", namespace, "-o", "json"], cluster="prod")
        if rc != 0:
            return [{"error": stderr}]

        data = parse_json_output(stdout)
        return [{
            "name": a["metadata"]["name"],
            "project": a["spec"].get("project", "default"),
            "sync_status": a["status"].get("sync", {}).get("status"),
            "health": a["status"].get("health", {}).get("status"),
            "repo": a["spec"].get("source", {}).get("repoURL"),
            "path": a["spec"].get("source", {}).get("path"),
            "destination_namespace": a["spec"].get("destination", {}).get("namespace"),
            "destination_server": a["spec"].get("destination", {}).get("server")
        } for a in data.get("items", [])]

    @mcp.tool()
    async def argocd_sync_application(app_name: str, namespace: str = "argocd") -> str:
        """Trigger sync for an ArgoCD application."""
        # ArgoCD runs in prod cluster, not agentic
        patch = '{"operation": {"initiatedBy": {"username": "infrastructure-mcp"}, "sync": {"prune": true}}}'
        stdout, stderr, rc = run_kubectl(["patch", "application", app_name, "-n", namespace, "--type", "merge", "-p", patch], cluster="prod")
        return f"Triggered sync for {app_name}" if rc == 0 else f"Error: {stderr}"

    # =========================================================================
    # Describe/YAML
    # =========================================================================

    @mcp.tool()
    async def kubectl_describe(resource_type: str, name: str, namespace: str = "default", cluster: str = "agentic") -> str:
        """Get detailed description of a Kubernetes resource.
        resource_type: pod, deployment, service, statefulset, etc.
        cluster: agentic (default), prod, or monit."""
        stdout, stderr, rc = run_kubectl(["describe", resource_type, name, "-n", namespace], cluster=cluster)
        return stdout[:5000] if rc == 0 else f"Error: {stderr}"

    @mcp.tool()
    async def kubectl_get_yaml(resource_type: str, name: str, namespace: str = "default", cluster: str = "agentic") -> str:
        """Get YAML manifest of a Kubernetes resource.
        cluster: agentic (default), prod, or monit."""
        stdout, stderr, rc = run_kubectl(["get", resource_type, name, "-n", namespace, "-o", "yaml"], cluster=cluster)
        return stdout[:8000] if rc == 0 else f"Error: {stderr}"

    # =========================================================================
    # Cluster Health
    # =========================================================================

    @mcp.tool()
    async def get_cluster_health() -> dict:
        """Get overall cluster health summary."""
        errors = []

        # Get nodes directly (don't call MCP-decorated functions)
        stdout, stderr, rc = run_kubectl(["get", "nodes", "-o", "json"])
        if rc != 0:
            errors.append(f"Node query: {stderr}")
            nodes = []
        else:
            data = parse_json_output(stdout)
            nodes = []
            for node in data.get("items", []):
                conditions = {c["type"]: c["status"] for c in node["status"].get("conditions", [])}
                nodes.append({
                    "name": node["metadata"]["name"],
                    "ready": conditions.get("Ready") == "True"
                })

        # Get pods directly
        stdout, stderr, rc = run_kubectl(["get", "pods", "-A", "-o", "json"])
        if rc != 0:
            errors.append(f"Pod query: {stderr}")
            pods = []
        else:
            data = parse_json_output(stdout)
            pods = []
            for pod in data.get("items", []):
                pods.append({
                    "status": pod["status"]["phase"],
                    "ready": all(c.get("ready", False) for c in pod["status"].get("containerStatuses", []))
                })

        # Get warning events directly
        stdout, stderr, rc = run_kubectl(["get", "events", "-A", "--field-selector=type=Warning", "-o", "json"])
        if rc != 0:
            warning_count = 0
        else:
            data = parse_json_output(stdout)
            warning_count = min(len(data.get("items", [])), 10)

        unhealthy_pods = [p for p in pods if not p.get("ready") or p.get("status") != "Running"]

        result = {
            "nodes": {
                "total": len(nodes),
                "ready": sum(1 for n in nodes if n.get("ready"))
            },
            "pods": {
                "total": len(pods),
                "running": len([p for p in pods if p.get("status") == "Running"]),
                "unhealthy": len(unhealthy_pods)
            },
            "recent_warnings": warning_count,
            "healthy": len(unhealthy_pods) == 0 and all(n.get("ready") for n in nodes) if nodes else False
        }

        if errors:
            result["errors"] = errors

        return result
