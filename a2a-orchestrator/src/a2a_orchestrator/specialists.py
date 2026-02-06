"""Specialist Agents - Domain-specific investigation functions."""

import os
import logging
from datetime import datetime

from a2a_orchestrator.mcp_client import (
    kubectl_get_pods,
    kubectl_get_events,
    kubectl_logs,
    list_secrets,
    query_metrics,
    coroot_get_anomalies,
    adguard_get_rewrites,
    adguard_get_query_log,
    adguard_get_stats,
    get_deployments,
    get_ingresses,
    search_runbooks,
    search_entities,
    call_mcp_tool,
)
from a2a_orchestrator.llm import gemini_analyze

logger = logging.getLogger(__name__)


# Pydantic model imported from server to avoid circular import
class Finding:
    def __init__(self, agent: str, status: str, issue: str = None,
                 evidence: str = None, recommendation: str = None,
                 tools_used: list = None, latency_ms: int = 0):
        self.agent = agent
        self.status = status
        self.issue = issue
        self.evidence = evidence
        self.recommendation = recommendation
        self.tools_used = tools_used or []
        self.latency_ms = latency_ms


# =============================================================================
# DevOps Specialist - Kubernetes pods, deployments, resources
# =============================================================================

DEVOPS_PROMPT = """You are a DevOps specialist investigating a Kubernetes alert in the Kernow homelab.

INFRASTRUCTURE:
- Clusters: prod (10.10.0.0/24 - apps, media), agentic (10.20.0.0/24 - AI platform), monit (10.30.0.0/24 - monitoring)
- GitOps: ArgoCD on prod cluster only - don't suggest manual kubectl apply
- Storage: TrueNAS (hdd for media, media pool for fast storage)
- Common namespaces: default, media, ai-platform, monitoring

Analyze the provided pod status, events, and logs to determine:
1. What is the root cause (OOM, crashloop, image pull, resource limits)?
2. Is this actionable or a false positive?
3. What is the recommended fix (restart, scale, check storage, etc.)?

Be concise. Focus on the actual issue, not general advice.
Output JSON with: status (PASS/WARN/FAIL), issue, recommendation
"""


async def devops_investigate(alert) -> Finding:
    """DevOps specialist: K8s pods, deployments, OOM, crashloops."""
    start = datetime.now()
    tools_used = []

    try:
        namespace = alert.labels.namespace or "default"
        pod = alert.labels.pod

        # Gather evidence
        evidence_parts = []

        # Get pod status
        if pod:
            pod_result = await kubectl_get_pods(namespace=namespace, name=pod)
            tools_used.append("kubectl_get_pods")
            if pod_result.get("status") == "success":
                evidence_parts.append(f"Pod status:\n{pod_result.get('output', '')[:500]}")

            # Get events for this pod
            events_result = await kubectl_get_events(
                namespace=namespace,
                field_selector=f"involvedObject.name={pod}"
            )
            tools_used.append("kubectl_get_events")
            if events_result.get("status") == "success":
                evidence_parts.append(f"Events:\n{events_result.get('output', '')[:500]}")

            # Get logs if crashlooping
            if "crash" in alert.name.lower() or "oom" in alert.name.lower():
                logs_result = await kubectl_logs(namespace=namespace, pod=pod, tail=30)
                tools_used.append("kubectl_logs")
                if logs_result.get("status") == "success":
                    evidence_parts.append(f"Logs:\n{logs_result.get('output', '')[:500]}")

        evidence = "\n\n".join(evidence_parts) if evidence_parts else "No pod data available"

        # Analyze with Gemini
        analysis = await gemini_analyze(
            system_prompt=DEVOPS_PROMPT,
            alert=alert,
            evidence=evidence
        )

        latency_ms = int((datetime.now() - start).total_seconds() * 1000)

        return Finding(
            agent="devops",
            status=analysis.get("status", "WARN"),
            issue=analysis.get("issue", f"Alert: {alert.name}"),
            evidence=evidence[:1000],
            recommendation=analysis.get("recommendation"),
            tools_used=tools_used,
            latency_ms=latency_ms
        )

    except Exception as e:
        logger.error(f"DevOps investigation failed: {e}")
        return Finding(
            agent="devops",
            status="ERROR",
            issue=str(e)[:200],
            tools_used=tools_used
        )


# =============================================================================
# Network Specialist - DNS, routing, connectivity
# =============================================================================

NETWORK_PROMPT = """You are a Network specialist investigating a connectivity/DNS alert in the Kernow homelab.

INFRASTRUCTURE KNOWLEDGE:
- Networks: prod (10.10.0.0/24), agentic (10.20.0.0/24), monit (10.30.0.0/24)
- DNS: AdGuard (10.10.0.1:53) -> Unbound -> Internet
- Split-DNS: AdGuard rewrites for specific domains -> Caddy; *.kernow.io wildcard in Unbound -> Traefik (10.10.0.90)
- Key IPs: OPNsense/AdGuard/Caddy (10.10.0.1), Prod LB (10.10.0.90), Agentic LB (10.20.0.90)

Analyze the provided DNS records, query logs, and network state to determine:
1. Is there a DNS misconfiguration (missing rewrite, wrong IP)?
2. Is a service unreachable (ingress, deployment issue)?
3. Is this a split-DNS routing problem?

Be concise. Focus on the actual issue.
Output JSON with: status (PASS/WARN/FAIL), issue, recommendation
"""


async def network_investigate(alert) -> Finding:
    """Network specialist: DNS, routing, firewall, connectivity."""
    start = datetime.now()
    tools_used = []

    try:
        evidence_parts = []
        namespace = alert.labels.namespace or "default"
        service = alert.labels.service

        # Always gather DNS context for network issues
        # Check rewrites for the service/namespace
        rewrites = await adguard_get_rewrites()
        tools_used.append("adguard_list_rewrites")
        if rewrites.get("status") == "success":
            output = rewrites.get("output", "")
            # Filter to relevant rewrites if we have a service name
            if service:
                lines = [l for l in output.split('\n') if service.lower() in l.lower() or namespace.lower() in l.lower()]
                if lines:
                    evidence_parts.append(f"Relevant DNS Rewrites:\n" + '\n'.join(lines[:10]))
                else:
                    evidence_parts.append(f"No DNS rewrite found for {service} (may use *.kernow.io wildcard)")
            else:
                evidence_parts.append(f"DNS Rewrites (sample):\n{output[:400]}")

        # Check recent DNS queries if connectivity issue
        if any(x in alert.name.lower() for x in ["dns", "resolve", "unreachable", "timeout", "connection"]):
            search_term = service or namespace
            query_log = await adguard_get_query_log(search=search_term, limit=20)
            tools_used.append("adguard_get_query_log")
            if query_log.get("status") == "success":
                evidence_parts.append(f"Recent DNS queries for {search_term}:\n{query_log.get('output', '')[:400]}")

        # Check service/ingress if we have a service name
        if service:
            # Get service endpoints
            svc_result = await call_mcp_tool(
                "infrastructure", "kubectl_get_services",
                {"namespace": namespace, "name": service}
            )
            tools_used.append("kubectl_get_services")
            if svc_result.get("status") == "success":
                evidence_parts.append(f"Service:\n{svc_result.get('output', '')[:400]}")

            # Get ingress for this namespace
            ingress_result = await get_ingresses(namespace)
            tools_used.append("kubectl_get_ingresses")
            if ingress_result.get("status") == "success":
                evidence_parts.append(f"Ingresses:\n{ingress_result.get('output', '')[:400]}")

        # Check deployments for the service
        if service:
            deploy_result = await get_deployments(namespace)
            tools_used.append("kubectl_get_deployments")
            if deploy_result.get("status") == "success":
                output = deploy_result.get("output", "")
                # Filter to relevant deployment
                lines = [l for l in output.split('\n') if service.lower() in l.lower()]
                if lines:
                    evidence_parts.append(f"Deployment status:\n" + '\n'.join(lines[:5]))

        evidence = "\n\n".join(evidence_parts) if evidence_parts else "No network data available"

        analysis = await gemini_analyze(
            system_prompt=NETWORK_PROMPT,
            alert=alert,
            evidence=evidence
        )

        latency_ms = int((datetime.now() - start).total_seconds() * 1000)

        return Finding(
            agent="network",
            status=analysis.get("status", "PASS"),
            issue=analysis.get("issue"),
            evidence=evidence[:1500],
            recommendation=analysis.get("recommendation"),
            tools_used=tools_used,
            latency_ms=latency_ms
        )

    except Exception as e:
        logger.error(f"Network investigation failed: {e}")
        return Finding(
            agent="network",
            status="ERROR",
            issue=str(e)[:200],
            tools_used=tools_used
        )


# =============================================================================
# Security Specialist - Secrets, auth, certificates
# =============================================================================

SECURITY_PROMPT = """You are a Security specialist investigating an auth/secrets alert.

Analyze the provided secret status and auth logs to determine:
1. Are required secrets present?
2. Is there an auth failure?
3. Are certificates valid?

Be concise. Focus on the actual issue.
Output JSON with: status (PASS/WARN/FAIL), issue, recommendation
"""


async def security_investigate(alert) -> Finding:
    """Security specialist: Secrets, auth failures, certs."""
    start = datetime.now()
    tools_used = []

    try:
        evidence_parts = []
        namespace = alert.labels.namespace or "default"

        # Check secrets for the namespace/service
        service = alert.labels.service or alert.labels.pod
        if service:
            # Check common secret paths
            for path in [f"/platform/{service}", f"/infrastructure/{service}"]:
                secrets_result = await list_secrets(path)
                tools_used.append("list_secrets")
                if secrets_result.get("status") == "success":
                    evidence_parts.append(f"Secrets at {path}:\n{secrets_result.get('output', '')[:300]}")
                    break

        # Check for auth-related events
        if any(x in alert.name.lower() for x in ["auth", "401", "403", "forbidden"]):
            events = await kubectl_get_events(namespace=namespace)
            tools_used.append("kubectl_get_events")
            if events.get("status") == "success":
                # Filter for auth-related events
                output = events.get("output", "")
                evidence_parts.append(f"Events:\n{output[:500]}")

        evidence = "\n\n".join(evidence_parts) if evidence_parts else "No security data available"

        analysis = await gemini_analyze(
            system_prompt=SECURITY_PROMPT,
            alert=alert,
            evidence=evidence
        )

        latency_ms = int((datetime.now() - start).total_seconds() * 1000)

        return Finding(
            agent="security",
            status=analysis.get("status", "PASS"),
            issue=analysis.get("issue"),
            evidence=evidence[:1000],
            recommendation=analysis.get("recommendation"),
            tools_used=tools_used,
            latency_ms=latency_ms
        )

    except Exception as e:
        logger.error(f"Security investigation failed: {e}")
        return Finding(
            agent="security",
            status="ERROR",
            issue=str(e)[:200],
            tools_used=tools_used
        )


# =============================================================================
# SRE Specialist - Metrics, latency, anomalies
# =============================================================================

SRE_PROMPT = """You are an SRE specialist investigating a performance/availability alert in the Kernow homelab.

MONITORING STACK:
- VictoriaMetrics: metrics storage (PromQL compatible) at monit cluster
- AlertManager: alert routing, deduplication
- Coroot: service dependency mapping, anomaly detection
- Gatus: endpoint health checks
- Grafana: dashboards

Analyze the provided metrics and anomalies to determine:
1. What is causing the latency/error rate?
2. Is this a transient spike or persistent issue?
3. What is the recommended mitigation?

Be concise. Focus on the actual issue.
Output JSON with: status (PASS/WARN/FAIL), issue, recommendation
"""


async def sre_investigate(alert) -> Finding:
    """SRE specialist: Metrics, latency, anomalies."""
    start = datetime.now()
    tools_used = []

    try:
        evidence_parts = []

        # Get recent anomalies from Coroot
        anomalies = await coroot_get_anomalies()
        tools_used.append("coroot_get_recent_anomalies")
        if anomalies.get("status") == "success":
            evidence_parts.append(f"Recent anomalies:\n{anomalies.get('output', '')[:500]}")

        # Query relevant metrics
        service = alert.labels.service or alert.labels.pod
        if service:
            # Error rate
            error_query = f'sum(rate(http_requests_total{{service="{service}",status=~"5.."}}[5m]))'
            error_result = await query_metrics(error_query)
            tools_used.append("query_metrics_instant")
            if error_result.get("status") == "success":
                evidence_parts.append(f"Error rate:\n{error_result.get('output', '')[:200]}")

            # Latency
            latency_query = f'histogram_quantile(0.95, rate(http_request_duration_seconds_bucket{{service="{service}"}}[5m]))'
            latency_result = await query_metrics(latency_query)
            if latency_result.get("status") == "success":
                evidence_parts.append(f"P95 latency:\n{latency_result.get('output', '')[:200]}")

        evidence = "\n\n".join(evidence_parts) if evidence_parts else "No metrics data available"

        analysis = await gemini_analyze(
            system_prompt=SRE_PROMPT,
            alert=alert,
            evidence=evidence
        )

        latency_ms = int((datetime.now() - start).total_seconds() * 1000)

        return Finding(
            agent="sre",
            status=analysis.get("status", "PASS"),
            issue=analysis.get("issue"),
            evidence=evidence[:1000],
            recommendation=analysis.get("recommendation"),
            tools_used=tools_used,
            latency_ms=latency_ms
        )

    except Exception as e:
        logger.error(f"SRE investigation failed: {e}")
        return Finding(
            agent="sre",
            status="ERROR",
            issue=str(e)[:200],
            tools_used=tools_used
        )


# =============================================================================
# Database Specialist - Qdrant, Neo4j, query failures
# =============================================================================

DATABASE_PROMPT = """You are a Database specialist investigating a data/query alert.

Analyze the provided database status and query errors to determine:
1. Is the database healthy?
2. Are queries failing?
3. Is there a sync issue?

Be concise. Focus on the actual issue.
Output JSON with: status (PASS/WARN/FAIL), issue, recommendation
"""


async def database_investigate(alert) -> Finding:
    """Database specialist: Qdrant, Neo4j, query failures."""
    start = datetime.now()
    tools_used = []

    try:
        evidence_parts = []

        # Search for related entities
        alert_context = f"{alert.name} {alert.description or ''}"
        entities = await search_entities(alert_context[:100])
        tools_used.append("search_entities")
        if entities.get("status") == "success":
            evidence_parts.append(f"Related entities:\n{entities.get('output', '')[:500]}")

        # Search for relevant runbooks
        runbooks = await search_runbooks(alert.name)
        tools_used.append("search_runbooks")
        if runbooks.get("status") == "success":
            evidence_parts.append(f"Related runbooks:\n{runbooks.get('output', '')[:500]}")

        evidence = "\n\n".join(evidence_parts) if evidence_parts else "No database data available"

        analysis = await gemini_analyze(
            system_prompt=DATABASE_PROMPT,
            alert=alert,
            evidence=evidence
        )

        latency_ms = int((datetime.now() - start).total_seconds() * 1000)

        return Finding(
            agent="database",
            status=analysis.get("status", "PASS"),
            issue=analysis.get("issue"),
            evidence=evidence[:1000],
            recommendation=analysis.get("recommendation"),
            tools_used=tools_used,
            latency_ms=latency_ms
        )

    except Exception as e:
        logger.error(f"Database investigation failed: {e}")
        return Finding(
            agent="database",
            status="ERROR",
            issue=str(e)[:200],
            tools_used=tools_used
        )
