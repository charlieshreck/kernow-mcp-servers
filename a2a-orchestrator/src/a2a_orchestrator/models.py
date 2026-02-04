"""Pydantic models for A2A Orchestrator API."""

from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


# === Investigation Models ===

class InvestigationGrade(str, Enum):
    """Grade for investigation findings."""
    CLEAR = "CLEAR"              # Strong consensus, actionable
    PARTIAL = "PARTIAL"          # Some findings, needs more info
    INCONCLUSIVE = "INCONCLUSIVE"  # Not enough evidence
    CONFLICTING = "CONFLICTING"  # Specialists disagree


class SpecialistFinding(BaseModel):
    """Finding from a single specialist agent."""
    specialist: str
    status: str  # PASS, FAIL, WARN, SKIP
    summary: str
    evidence: List[str] = Field(default_factory=list)
    tools_called: List[str] = Field(default_factory=list)
    confidence: float = 0.0
    latency_ms: int = 0
    error: Optional[str] = None


class InvestigateRequest(BaseModel):
    """Request to investigate an alert."""
    request_id: str
    alert: Dict[str, Any]
    context: Optional[Dict[str, Any]] = None


class InvestigateResponse(BaseModel):
    """Response from investigation phase."""
    request_id: str
    grade: InvestigationGrade
    confidence: float
    findings: List[SpecialistFinding]
    synthesis: str
    recommended_domain: str
    escalation_reason: Optional[str] = None
    fallback_used: bool = False
    latency_ms: int = 0


# === Plan & Decide Models ===

class PlanMatchType(str, Enum):
    """How the plan was generated."""
    EXACT = "EXACT"        # Runbook match >= 0.95
    SIMILAR = "SIMILAR"    # Runbook match 0.80-0.95
    GENERATED = "GENERATED"  # New plan from findings
    NO_PLAN = "NO_PLAN"    # Cannot generate plan


class DecisionAction(str, Enum):
    """Decision on what to do with the plan."""
    EXECUTE = "EXECUTE"    # Proceed with execution
    ESCALATE = "ESCALATE"  # Needs human decision
    WAIT = "WAIT"          # Wait and re-evaluate


class StateCapture(BaseModel):
    """Definition for capturing pre-execution state."""
    key: str  # Name for the captured value (e.g., "replicas")
    tool: str  # MCP tool to call (e.g., "kubectl_get_deployment")
    extract: str  # JSONPath or field to extract (e.g., "spec.replicas")


class PlanStep(BaseModel):
    """A single step in an execution plan - v2 schema with tool-based execution."""
    order: int
    action: str  # Human-readable description

    # Tool-based execution (preferred)
    tool: Optional[str] = None  # MCP tool name (e.g., "kubectl_restart_deployment")
    arguments: Optional[Dict[str, Any]] = None  # Tool arguments

    # Legacy command support (deprecated, for backwards compatibility)
    command: Optional[str] = None

    # Pre-execution state capture for rollback
    pre_capture: Optional[List[StateCapture]] = None

    # Rollback configuration
    rollback_tool: Optional[str] = None
    rollback_args: Optional[Dict[str, Any]] = None  # Can use {captured.key} placeholders
    rollback: Optional[str] = None  # Legacy command-based rollback (deprecated)

    risk: str = "low"  # low, medium, high

    def is_tool_based(self) -> bool:
        """Check if this step uses tool-based execution."""
        return self.tool is not None and self.arguments is not None


class PlanAndDecideRequest(BaseModel):
    """Request to generate plan and decide action."""
    request_id: str
    alert: Dict[str, Any]
    investigation: InvestigateResponse
    context: Optional[Dict[str, Any]] = None


class PlanAndDecideResponse(BaseModel):
    """Response with plan and decision."""
    request_id: str
    match_type: PlanMatchType
    runbook_id: Optional[str] = None
    runbook_name: Optional[str] = None
    runbook_score: Optional[float] = None
    plan: List[PlanStep]
    tweaks_applied: List[str] = Field(default_factory=list)
    decision: DecisionAction
    decision_rationale: str
    confidence: float
    risk_level: str = "medium"
    requires_approval: bool = True
    escalation_reason: Optional[str] = None
    fallback_used: bool = False


# === Validate & Document Models ===

class ValidationVerdict(str, Enum):
    """Verdict from validation phase."""
    RESOLVED = "RESOLVED"
    PARTIAL = "PARTIAL"
    STILL_FAILING = "STILL_FAILING"
    FALSE_POSITIVE = "FALSE_POSITIVE"


class ValidateAndDocumentRequest(BaseModel):
    """Request to validate resolution and document incident."""
    request_id: str
    alert: Dict[str, Any]
    investigation: InvestigateResponse
    plan: PlanAndDecideResponse
    execution_result: Dict[str, Any]
    context: Optional[Dict[str, Any]] = None


class IncidentDocument(BaseModel):
    """Generated incident documentation."""
    title: str
    summary: str
    timeline: List[str]
    root_cause: str
    resolution: str
    lessons_learned: List[str] = Field(default_factory=list)
    runbook_proposal: Optional[str] = None  # For SIMILAR/GENERATED matches


class ValidateAndDocumentResponse(BaseModel):
    """Response from validation and documentation phase."""
    request_id: str
    verdict: ValidationVerdict
    validation_evidence: List[str]
    confidence: float
    document: IncidentDocument
    runbook_action: Optional[str] = None  # "UPDATE", "CREATE", "REVIEW", None
    escalation_reason: Optional[str] = None
    fallback_used: bool = False
