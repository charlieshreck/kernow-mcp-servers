"""Shared Pydantic models for A2A Orchestrator."""

from typing import Optional
from pydantic import BaseModel


class AlertLabels(BaseModel):
    namespace: Optional[str] = None
    pod: Optional[str] = None
    service: Optional[str] = None
    node: Optional[str] = None

    class Config:
        extra = "allow"


class Alert(BaseModel):
    name: str
    labels: AlertLabels = AlertLabels()
    severity: str = "warning"
    description: Optional[str] = None
    fingerprint: Optional[str] = None


class Finding(BaseModel):
    agent: str
    status: str  # PASS, WARN, FAIL, ERROR
    issue: Optional[str] = None
    evidence: Optional[str] = None
    recommendation: Optional[str] = None
    tools_used: list[str] = []
    latency_ms: int = 0


class SynthesisResult(BaseModel):
    verdict: str  # ACTIONABLE, UNKNOWN, FALSE_POSITIVE
    confidence: float
    synthesis: str
    suggested_action: Optional[str] = None


class InvestigateRequest(BaseModel):
    request_id: str
    alert: Alert
    context: dict = {}


class InvestigateResponse(BaseModel):
    request_id: str
    verdict: str
    confidence: float
    findings: list[Finding]
    synthesis: str
    suggested_action: Optional[str] = None
    fallback_used: bool = False
    latency_ms: int = 0
