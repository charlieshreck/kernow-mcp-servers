"""Tool Catalog - Registry of safe MCP tools for plan execution.

This module defines:
- Available MCP tools and their specifications
- Command-to-tool mapping for backwards compatibility
- Tool validation utilities
"""

import re
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple, Callable

logger = logging.getLogger(__name__)


@dataclass
class ToolSpec:
    """Specification for an MCP tool."""
    name: str
    mcp: str  # Which MCP provides this tool (infrastructure, observability, etc.)
    required_args: List[str]
    optional_args: List[str] = field(default_factory=list)
    risk_level: str = "medium"  # low, medium, high
    rollback_tool: Optional[str] = None
    pre_capture: Optional[List[Dict[str, str]]] = None  # State to capture before execution
    description: str = ""


# =============================================================================
# Tool Catalog - Safe execution tools
# =============================================================================

TOOL_CATALOG: Dict[str, ToolSpec] = {
    # === Kubernetes Pod Operations ===
    "kubectl_delete_pod": ToolSpec(
        name="kubectl_delete_pod",
        mcp="infrastructure",
        required_args=["namespace", "pod_name"],
        risk_level="medium",
        rollback_tool=None,  # Pods recreate automatically via controller
        description="Delete a pod (triggers recreation by controller)"
    ),
    "kubectl_get_pods": ToolSpec(
        name="kubectl_get_pods",
        mcp="infrastructure",
        required_args=["namespace"],
        optional_args=["label_selector", "cluster"],
        risk_level="low",
        description="List pods in a namespace"
    ),
    "kubectl_logs": ToolSpec(
        name="kubectl_logs",
        mcp="infrastructure",
        required_args=["pod_name", "namespace"],
        optional_args=["container", "tail_lines", "previous"],
        risk_level="low",
        description="Get pod logs"
    ),

    # === Kubernetes Deployment Operations ===
    "kubectl_restart_deployment": ToolSpec(
        name="kubectl_restart_deployment",
        mcp="infrastructure",
        required_args=["deployment_name", "namespace"],
        risk_level="medium",
        rollback_tool=None,  # Restart is idempotent
        description="Trigger rolling restart of deployment"
    ),
    "kubectl_scale_deployment": ToolSpec(
        name="kubectl_scale_deployment",
        mcp="infrastructure",
        required_args=["deployment_name", "namespace", "replicas"],
        risk_level="high",
        rollback_tool="kubectl_scale_deployment",
        pre_capture=[{"key": "original_replicas", "tool": "kubectl_get_deployments", "extract": "replicas"}],
        description="Scale deployment to N replicas"
    ),
    "kubectl_get_deployments": ToolSpec(
        name="kubectl_get_deployments",
        mcp="infrastructure",
        required_args=["namespace"],
        optional_args=["cluster"],
        risk_level="low",
        description="List deployments in a namespace"
    ),
    "kubectl_rollout_status": ToolSpec(
        name="kubectl_rollout_status",
        mcp="infrastructure",
        required_args=["deployment_name", "namespace"],
        risk_level="low",
        description="Check rollout status of a deployment"
    ),

    # === Kubernetes Service Operations ===
    "kubectl_get_services": ToolSpec(
        name="kubectl_get_services",
        mcp="infrastructure",
        required_args=["namespace"],
        optional_args=["cluster"],
        risk_level="low",
        description="List services in a namespace"
    ),

    # === Kubernetes Events ===
    "kubectl_get_events": ToolSpec(
        name="kubectl_get_events",
        mcp="infrastructure",
        required_args=["namespace"],
        optional_args=["limit", "warning_only", "cluster"],
        risk_level="low",
        description="Get Kubernetes events"
    ),

    # === ArgoCD Operations ===
    "argocd_sync_application": ToolSpec(
        name="argocd_sync_application",
        mcp="infrastructure",
        required_args=["app_name"],
        optional_args=["namespace"],
        risk_level="medium",
        description="Trigger ArgoCD application sync"
    ),
    "argocd_get_applications": ToolSpec(
        name="argocd_get_applications",
        mcp="infrastructure",
        required_args=[],
        optional_args=["namespace"],
        risk_level="low",
        description="List ArgoCD applications"
    ),

    # === Alerting Operations ===
    "create_silence": ToolSpec(
        name="create_silence",
        mcp="observability",
        required_args=["matchers", "duration"],
        optional_args=["comment", "created_by"],
        risk_level="low",
        rollback_tool="delete_silence",
        description="Create an alert silence"
    ),
    "delete_silence": ToolSpec(
        name="delete_silence",
        mcp="observability",
        required_args=["silence_id"],
        risk_level="low",
        description="Delete an alert silence"
    ),
    "list_alerts": ToolSpec(
        name="list_alerts",
        mcp="observability",
        required_args=[],
        optional_args=["state", "silenced"],
        risk_level="low",
        description="List active alerts"
    ),

    # === Metrics Operations ===
    "query_metrics_instant": ToolSpec(
        name="query_metrics_instant",
        mcp="observability",
        required_args=["query"],
        optional_args=["time"],
        risk_level="low",
        description="Query metrics at a point in time"
    ),
}


# =============================================================================
# Command-to-Tool Mapping
# =============================================================================

# Patterns: (regex, tool_name, arg_extractor_function)
CommandPattern = Tuple[str, str, Callable[[re.Match], Dict[str, Any]]]

COMMAND_PATTERNS: List[CommandPattern] = [
    # kubectl delete pod <pod> -n <namespace>
    (
        r"kubectl\s+delete\s+pod[s]?\s+([a-z0-9][a-z0-9\-\.]*)\s+(?:-n\s+|--namespace[=\s])([a-z0-9][a-z0-9\-]*)",
        "kubectl_delete_pod",
        lambda m: {"pod_name": m.group(1), "namespace": m.group(2)}
    ),
    # kubectl delete pod <pod> (default namespace)
    (
        r"kubectl\s+delete\s+pod[s]?\s+([a-z0-9][a-z0-9\-\.]*)\s*$",
        "kubectl_delete_pod",
        lambda m: {"pod_name": m.group(1), "namespace": "default"}
    ),

    # kubectl rollout restart deployment/<name> -n <namespace>
    (
        r"kubectl\s+rollout\s+restart\s+deployment[/]?([a-z0-9][a-z0-9\-]*)\s+(?:-n\s+|--namespace[=\s])([a-z0-9][a-z0-9\-]*)",
        "kubectl_restart_deployment",
        lambda m: {"deployment_name": m.group(1), "namespace": m.group(2)}
    ),
    # kubectl rollout restart deployment/<name> (default namespace)
    (
        r"kubectl\s+rollout\s+restart\s+deployment[/]?([a-z0-9][a-z0-9\-]*)\s*$",
        "kubectl_restart_deployment",
        lambda m: {"deployment_name": m.group(1), "namespace": "default"}
    ),

    # kubectl scale deployment/<name> --replicas=N -n <namespace>
    (
        r"kubectl\s+scale\s+deployment[/]?([a-z0-9][a-z0-9\-]*)\s+--replicas[=\s]?(\d+)\s+(?:-n\s+|--namespace[=\s])([a-z0-9][a-z0-9\-]*)",
        "kubectl_scale_deployment",
        lambda m: {"deployment_name": m.group(1), "replicas": int(m.group(2)), "namespace": m.group(3)}
    ),

    # argocd app sync <app>
    (
        r"argocd\s+app\s+sync\s+([a-z0-9][a-z0-9\-]*)",
        "argocd_sync_application",
        lambda m: {"app_name": m.group(1)}
    ),
]


def command_to_tool(command: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Convert a shell command to a tool call.

    Args:
        command: Shell command string

    Returns:
        Tuple of (tool_name, arguments) or (None, None) if no match
    """
    command = command.strip()

    for pattern, tool_name, extractor in COMMAND_PATTERNS:
        match = re.search(pattern, command, re.IGNORECASE)
        if match:
            try:
                args = extractor(match)
                logger.info(f"Mapped command to tool: {tool_name} with args {args}")
                return tool_name, args
            except Exception as e:
                logger.warning(f"Failed to extract args from command: {e}")
                continue

    logger.debug(f"No tool mapping for command: {command[:50]}...")
    return None, None


# =============================================================================
# Tool Validation
# =============================================================================

def validate_tool_call(tool_name: str, arguments: Dict[str, Any]) -> Tuple[bool, str]:
    """Validate a tool call against the catalog.

    Args:
        tool_name: Name of the tool to call
        arguments: Arguments for the tool

    Returns:
        Tuple of (is_valid, message)
    """
    if tool_name not in TOOL_CATALOG:
        return False, f"Unknown tool: {tool_name}. Available tools: {list(TOOL_CATALOG.keys())}"

    spec = TOOL_CATALOG[tool_name]

    # Check required arguments
    missing = [arg for arg in spec.required_args if arg not in arguments]
    if missing:
        return False, f"Missing required arguments for {tool_name}: {missing}"

    return True, "Valid"


def get_tool_spec(tool_name: str) -> Optional[ToolSpec]:
    """Get the specification for a tool."""
    return TOOL_CATALOG.get(tool_name)


def get_mcp_for_tool(tool_name: str) -> str:
    """Get the MCP that provides a tool.

    Args:
        tool_name: Name of the tool

    Returns:
        MCP name (defaults to 'infrastructure' if unknown)
    """
    spec = TOOL_CATALOG.get(tool_name)
    if spec:
        return spec.mcp
    return "infrastructure"  # Default


def get_risk_level(tool_name: str) -> str:
    """Get the risk level for a tool.

    Args:
        tool_name: Name of the tool

    Returns:
        Risk level ('low', 'medium', 'high')
    """
    spec = TOOL_CATALOG.get(tool_name)
    if spec:
        return spec.risk_level
    return "high"  # Unknown tools are high risk


def get_rollback_spec(tool_name: str) -> Optional[Dict[str, Any]]:
    """Get rollback specification for a tool.

    Args:
        tool_name: Name of the tool

    Returns:
        Dict with rollback_tool and pre_capture info, or None
    """
    spec = TOOL_CATALOG.get(tool_name)
    if not spec or not spec.rollback_tool:
        return None

    return {
        "rollback_tool": spec.rollback_tool,
        "pre_capture": spec.pre_capture or []
    }
