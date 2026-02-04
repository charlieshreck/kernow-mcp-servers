"""A2A Orchestrator Server - FastAPI service for parallel alert investigation."""

import os
import logging
import asyncio
from typing import Optional
from datetime import datetime

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from a2a_orchestrator.specialists import (
    devops_investigate,
    network_investigate,
    security_investigate,
    sre_investigate,
    database_investigate,
)
from a2a_orchestrator.synthesis import synthesize_findings
from a2a_orchestrator.fallback import qwen_fallback_assess

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="A2A Orchestrator",
    description="Parallel specialist agents for alert triage",
    version="1.0.0"
)


# =============================================================================
# Request/Response Models
# =============================================================================

class AlertLabels(BaseModel):
    namespace: Optional[str] = None
    pod: Optional[str] = None
    service: Optional[str] = None
    node: Optional[str] = None
    # Allow any additional labels
    class Config:
        extra = "allow"


class Alert(BaseModel):
    name: str
    labels: AlertLabels = AlertLabels()
    severity: str = "warning"
    description: Optional[str] = None
    fingerprint: Optional[str] = None


class InvestigateRequest(BaseModel):
    request_id: str
    alert: Alert
    context: dict = {}


class Finding(BaseModel):
    agent: str
    status: str  # PASS, WARN, FAIL, ERROR
    issue: Optional[str] = None
    evidence: Optional[str] = None
    recommendation: Optional[str] = None
    tools_used: list[str] = []
    latency_ms: int = 0


class InvestigateResponse(BaseModel):
    request_id: str
    verdict: str  # ACTIONABLE, UNKNOWN, FALSE_POSITIVE
    confidence: float
    findings: list[Finding]
    synthesis: str
    suggested_action: Optional[str] = None
    fallback_used: bool = False
    latency_ms: int = 0


# =============================================================================
# Specialist Dispatch
# =============================================================================

SPECIALISTS = {
    "devops": devops_investigate,
    "network": network_investigate,
    "security": security_investigate,
    "sre": sre_investigate,
    "database": database_investigate,
}

# Domain weights for synthesis
DOMAIN_AUTHORITY = {
    "security": 1.0,
    "devops": 0.9,
    "sre": 0.8,
    "network": 0.7,
    "database": 0.6,
}


async def investigate_parallel(alert: Alert, timeout: float = 15.0) -> list[Finding]:
    """Fan out to all specialists in parallel with timeout."""
    tasks = {}

    for name, func in SPECIALISTS.items():
        tasks[name] = asyncio.create_task(func(alert))

    # Wait with timeout
    done, pending = await asyncio.wait(
        tasks.values(),
        timeout=timeout,
        return_when=asyncio.ALL_COMPLETED
    )

    # Cancel stragglers
    for task in pending:
        task.cancel()
        logger.warning(f"Specialist timed out after {timeout}s")

    # Collect results
    findings = []
    for name, task in tasks.items():
        if task in done and not task.cancelled():
            try:
                result = task.result()
                if result:
                    findings.append(result)
            except Exception as e:
                logger.error(f"Specialist {name} failed: {e}")
                findings.append(Finding(
                    agent=name,
                    status="ERROR",
                    issue=f"Investigation failed: {str(e)[:100]}",
                    tools_used=[]
                ))

    return findings


# =============================================================================
# API Endpoints
# =============================================================================

@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "service": "a2a-orchestrator"}


@app.get("/v1/agents")
async def list_agents():
    """List available specialist agents."""
    return {
        "agents": list(SPECIALISTS.keys()),
        "weights": DOMAIN_AUTHORITY
    }


@app.post("/v1/investigate", response_model=InvestigateResponse)
async def investigate(request: InvestigateRequest):
    """Investigate an alert using parallel specialists."""
    start_time = datetime.now()
    logger.info(f"Investigating alert: {request.alert.name} [{request.request_id}]")

    try:
        # Try Gemini-powered specialists
        findings = await investigate_parallel(request.alert)

        # Synthesize results
        synthesis_result = await synthesize_findings(
            findings=findings,
            alert=request.alert,
            domain_weights=DOMAIN_AUTHORITY
        )

        latency_ms = int((datetime.now() - start_time).total_seconds() * 1000)

        return InvestigateResponse(
            request_id=request.request_id,
            verdict=synthesis_result.verdict,
            confidence=synthesis_result.confidence,
            findings=findings,
            synthesis=synthesis_result.synthesis,
            suggested_action=synthesis_result.suggested_action,
            fallback_used=False,
            latency_ms=latency_ms
        )

    except Exception as e:
        logger.warning(f"A2A investigation failed, using fallback: {e}")

        # Fallback to qwen
        fallback_result = await qwen_fallback_assess(request.alert)
        latency_ms = int((datetime.now() - start_time).total_seconds() * 1000)

        return InvestigateResponse(
            request_id=request.request_id,
            verdict=fallback_result.verdict,
            confidence=fallback_result.confidence,
            findings=[Finding(
                agent="qwen-fallback",
                status="WARN" if fallback_result.verdict == "ACTIONABLE" else "PASS",
                issue=fallback_result.synthesis,
                recommendation=fallback_result.suggested_action,
                tools_used=[]
            )],
            synthesis=fallback_result.synthesis,
            suggested_action=fallback_result.suggested_action,
            fallback_used=True,
            latency_ms=latency_ms
        )


# =============================================================================
# Plan & Decide Models
# =============================================================================

class PlanStep(BaseModel):
    order: int
    action: str
    command: Optional[str] = None
    rollback: Optional[str] = None
    risk: str = "low"


class PlanAndDecideRequest(BaseModel):
    request_id: str
    alert: Alert
    investigation: InvestigateResponse
    context: dict = {}


class PlanAndDecideResponse(BaseModel):
    request_id: str
    match_type: str  # EXACT, SIMILAR, GENERATED, NO_PLAN
    runbook_id: Optional[str] = None
    runbook_name: Optional[str] = None
    runbook_score: Optional[float] = None
    plan: list[PlanStep]
    tweaks_applied: list[str] = []
    decision: str  # EXECUTE, ESCALATE, WAIT
    decision_rationale: str
    confidence: float
    risk_level: str = "medium"
    requires_approval: bool = True
    escalation_reason: Optional[str] = None
    fallback_used: bool = False


# =============================================================================
# Validate & Document Models
# =============================================================================

class IncidentDocument(BaseModel):
    title: str
    summary: str
    timeline: list[str]
    root_cause: str
    resolution: str
    lessons_learned: list[str] = []
    runbook_proposal: Optional[str] = None


class ValidateAndDocumentRequest(BaseModel):
    request_id: str
    alert: Alert
    investigation: InvestigateResponse
    plan: PlanAndDecideResponse
    execution_result: dict
    context: dict = {}


class ValidateAndDocumentResponse(BaseModel):
    request_id: str
    verdict: str  # RESOLVED, PARTIAL, STILL_FAILING, FALSE_POSITIVE
    validation_evidence: list[str]
    confidence: float
    document: IncidentDocument
    runbook_action: Optional[str] = None  # UPDATE, CREATE, REVIEW
    escalation_reason: Optional[str] = None
    fallback_used: bool = False


# =============================================================================
# Planner Logic
# =============================================================================

RUNBOOK_EXACT_THRESHOLD = float(os.environ.get("RUNBOOK_EXACT_THRESHOLD", "0.95"))
RUNBOOK_SIMILAR_THRESHOLD = float(os.environ.get("RUNBOOK_SIMILAR_THRESHOLD", "0.80"))


async def search_runbooks_for_alert(alert: Alert, investigation: InvestigateResponse) -> dict:
    """Search knowledge-mcp for matching runbooks."""
    from a2a_orchestrator.mcp_client import call_mcp_tool

    # Build search query from alert and findings
    query_parts = [alert.name]
    if alert.description:
        query_parts.append(alert.description[:100])

    # Add key issues from investigation
    for finding in investigation.findings[:3]:
        if finding.issue:
            query_parts.append(finding.issue[:50])

    query = " ".join(query_parts)[:200]

    result = await call_mcp_tool("knowledge", "search_runbooks", {"query": query, "limit": 3})
    return result


def classify_runbook_match(score: float) -> str:
    """Classify runbook match type based on score."""
    if score >= RUNBOOK_EXACT_THRESHOLD:
        return "EXACT"
    elif score >= RUNBOOK_SIMILAR_THRESHOLD:
        return "SIMILAR"
    else:
        return "NO_MATCH"


async def generate_plan_from_investigation(alert: Alert, investigation: InvestigateResponse) -> list[PlanStep]:
    """Generate a plan based on investigation findings when no runbook matches."""
    from a2a_orchestrator.llm import gemini_analyze

    # Use Gemini to generate plan steps
    findings_text = "\n".join([
        f"- {f.agent}: {f.issue} (recommendation: {f.recommendation})"
        for f in investigation.findings if f.issue
    ])

    system_prompt = """You are a remediation planner. Generate a step-by-step plan to resolve this alert.

Output JSON with a "steps" array where each step has:
- order: step number (1, 2, 3...)
- action: what to do
- command: kubectl/CLI command if applicable
- rollback: how to undo this step
- risk: low/medium/high"""

    try:
        result = await gemini_analyze(
            system_prompt=system_prompt,
            alert=alert,
            evidence=f"Investigation findings:\n{findings_text}\n\nSynthesis: {investigation.synthesis}"
        )

        steps = result.get("steps", [])
        return [PlanStep(**s) for s in steps[:5]]  # Max 5 steps

    except Exception as e:
        logger.warning(f"Plan generation failed: {e}")
        # Return generic investigation step
        return [PlanStep(
            order=1,
            action="Manual investigation required",
            command=None,
            rollback=None,
            risk="low"
        )]


def decide_action(
    match_type: str,
    investigation: InvestigateResponse,
    plan: list[PlanStep],
    alert: Alert
) -> tuple[str, str, bool]:
    """Decide whether to execute, escalate, or wait.

    Returns: (decision, rationale, requires_approval)
    """
    # Always escalate if investigation was inconclusive or conflicting
    if investigation.verdict == "UNKNOWN":
        return "ESCALATE", "Investigation inconclusive - human review needed", True

    # Always escalate if no plan
    if match_type == "NO_PLAN" or not plan:
        return "ESCALATE", "No matching runbook and could not generate plan", True

    # High-risk plans always need approval
    high_risk_steps = [s for s in plan if s.risk == "high"]
    if high_risk_steps:
        return "EXECUTE", f"Plan has {len(high_risk_steps)} high-risk steps - approval required", True

    # EXACT match with high confidence can auto-execute
    if match_type == "EXACT" and investigation.confidence >= 0.9:
        if alert.severity in ("critical", "error"):
            return "EXECUTE", "Exact runbook match with high confidence - approval required for critical", True
        else:
            return "EXECUTE", "Exact runbook match with high confidence", False

    # SIMILAR match always needs approval
    if match_type == "SIMILAR":
        return "EXECUTE", "Similar runbook match - tweaks may be needed, approval required", True

    # GENERATED plan always needs approval
    if match_type == "GENERATED":
        return "EXECUTE", "Generated plan - human verification required", True

    # Default: escalate
    return "ESCALATE", "Unable to determine safe action", True


@app.post("/v1/plan_and_decide", response_model=PlanAndDecideResponse)
async def plan_and_decide(request: PlanAndDecideRequest):
    """Generate execution plan and decide action based on investigation."""
    logger.info(f"Planning for alert: {request.alert.name} [{request.request_id}]")

    try:
        # Search for matching runbooks
        runbook_result = await search_runbooks_for_alert(request.alert, request.investigation)

        runbook_id = None
        runbook_name = None
        runbook_score = 0.0
        plan = []
        tweaks = []
        match_type = "NO_PLAN"

        if runbook_result.get("status") == "success":
            output = runbook_result.get("output", "")
            # Parse runbook search results (expecting JSON or structured output)
            try:
                import json
                runbooks = json.loads(output) if isinstance(output, str) else output
                if runbooks and isinstance(runbooks, list) and len(runbooks) > 0:
                    best_match = runbooks[0]
                    runbook_score = best_match.get("score", 0)
                    match_type = classify_runbook_match(runbook_score)

                    if match_type in ("EXACT", "SIMILAR"):
                        runbook_id = best_match.get("id")
                        runbook_name = best_match.get("name")

                        # Extract steps from runbook
                        steps = best_match.get("steps", [])
                        for i, step in enumerate(steps[:5]):
                            plan.append(PlanStep(
                                order=i + 1,
                                action=step.get("action", str(step)),
                                command=step.get("command"),
                                rollback=step.get("rollback"),
                                risk=step.get("risk", "low")
                            ))

                        # Note any required tweaks for SIMILAR match
                        if match_type == "SIMILAR":
                            tweaks.append(f"Adapted from {runbook_name} (score: {runbook_score:.2f})")
            except Exception as e:
                logger.warning(f"Failed to parse runbook results: {e}")

        # If no runbook match, generate plan from investigation
        if match_type in ("NO_MATCH", "NO_PLAN") and request.investigation.verdict == "ACTIONABLE":
            plan = await generate_plan_from_investigation(request.alert, request.investigation)
            if plan:
                match_type = "GENERATED"

        # Decide action
        decision, rationale, requires_approval = decide_action(
            match_type, request.investigation, plan, request.alert
        )

        # Calculate risk level
        if any(s.risk == "high" for s in plan):
            risk_level = "high"
        elif any(s.risk == "medium" for s in plan):
            risk_level = "medium"
        else:
            risk_level = "low"

        return PlanAndDecideResponse(
            request_id=request.request_id,
            match_type=match_type,
            runbook_id=runbook_id,
            runbook_name=runbook_name,
            runbook_score=runbook_score if runbook_score > 0 else None,
            plan=plan,
            tweaks_applied=tweaks,
            decision=decision,
            decision_rationale=rationale,
            confidence=request.investigation.confidence * (runbook_score if runbook_score > 0 else 0.5),
            risk_level=risk_level,
            requires_approval=requires_approval,
            escalation_reason=rationale if decision == "ESCALATE" else None,
            fallback_used=False
        )

    except Exception as e:
        logger.error(f"Plan and decide failed: {e}")
        return PlanAndDecideResponse(
            request_id=request.request_id,
            match_type="NO_PLAN",
            plan=[],
            tweaks_applied=[],
            decision="ESCALATE",
            decision_rationale=f"Planning failed: {str(e)[:100]}",
            confidence=0.0,
            risk_level="high",
            requires_approval=True,
            escalation_reason=f"Planning error: {str(e)[:100]}",
            fallback_used=False
        )


# =============================================================================
# Validator & Documenter Logic
# =============================================================================

async def validate_resolution(alert: Alert, execution_result: dict) -> tuple[str, list[str], float]:
    """Validate that the alert is actually resolved.

    Returns: (verdict, evidence, confidence)
    """
    from a2a_orchestrator.mcp_client import call_mcp_tool

    evidence = []
    checks_passed = 0
    total_checks = 0

    # Check 1: Alert status via observability-mcp
    total_checks += 1
    try:
        alerts_result = await call_mcp_tool("observability", "list_alerts")
        if alerts_result.get("status") == "success":
            output = alerts_result.get("output", "")
            if alert.name not in output and alert.fingerprint not in str(output):
                evidence.append(f"Alert '{alert.name}' no longer in active alerts")
                checks_passed += 1
            else:
                evidence.append(f"Alert '{alert.name}' still present in active alerts")
    except Exception as e:
        evidence.append(f"Alert check failed: {e}")

    # Check 2: Pod/service status if applicable
    if alert.labels.pod or alert.labels.service:
        total_checks += 1
        try:
            namespace = alert.labels.namespace or "default"
            target = alert.labels.pod or alert.labels.service

            pods_result = await call_mcp_tool(
                "infrastructure", "kubectl_get_pods",
                {"namespace": namespace}
            )
            if pods_result.get("status") == "success":
                output = pods_result.get("output", "")
                if "Running" in output and target in output:
                    evidence.append(f"Pod/service '{target}' is Running")
                    checks_passed += 1
                elif "CrashLoopBackOff" in output or "Error" in output:
                    evidence.append(f"Pod/service '{target}' still has issues")
        except Exception as e:
            evidence.append(f"Pod check failed: {e}")

    # Check 3: Execution result
    total_checks += 1
    if execution_result.get("success", False):
        evidence.append("Execution completed successfully")
        checks_passed += 1
    else:
        evidence.append(f"Execution had errors: {execution_result.get('error', 'unknown')}")

    # Determine verdict
    confidence = checks_passed / max(total_checks, 1)

    if checks_passed == total_checks:
        verdict = "RESOLVED"
    elif checks_passed >= total_checks / 2:
        verdict = "PARTIAL"
    elif checks_passed == 0 and execution_result.get("false_positive"):
        verdict = "FALSE_POSITIVE"
    else:
        verdict = "STILL_FAILING"

    return verdict, evidence, confidence


async def generate_incident_document(
    alert: Alert,
    investigation: InvestigateResponse,
    plan: PlanAndDecideResponse,
    execution_result: dict,
    verdict: str
) -> IncidentDocument:
    """Generate incident documentation."""
    from datetime import datetime

    # Build timeline
    timeline = [
        f"Alert received: {alert.name} ({alert.severity})",
        f"Investigation completed: {investigation.verdict} ({investigation.confidence:.0%} confidence)",
    ]

    if plan.runbook_name:
        timeline.append(f"Matched runbook: {plan.runbook_name} ({plan.match_type})")
    else:
        timeline.append(f"Plan generated: {plan.match_type}")

    timeline.append(f"Decision: {plan.decision}")

    if execution_result.get("started_at"):
        timeline.append(f"Execution started: {execution_result['started_at']}")
    if execution_result.get("completed_at"):
        timeline.append(f"Execution completed: {execution_result['completed_at']}")

    timeline.append(f"Validation result: {verdict}")

    # Root cause from investigation
    root_cause = investigation.synthesis

    # Resolution summary
    if verdict == "RESOLVED":
        resolution = f"Successfully resolved via {plan.match_type} plan"
        if plan.runbook_name:
            resolution += f" (runbook: {plan.runbook_name})"
    elif verdict == "FALSE_POSITIVE":
        resolution = "Determined to be a false positive - no action required"
    else:
        resolution = f"Partially resolved or still failing - manual follow-up required"

    # Lessons learned
    lessons = []
    if plan.match_type == "SIMILAR":
        lessons.append(f"Runbook '{plan.runbook_name}' should be updated to handle this case")
    if plan.match_type == "GENERATED":
        lessons.append("Consider creating a new runbook for this alert pattern")
    if verdict == "FALSE_POSITIVE":
        lessons.append("Consider tuning alert threshold or adding suppression rule")

    # Runbook proposal for non-exact matches
    runbook_proposal = None
    if plan.match_type in ("SIMILAR", "GENERATED") and verdict == "RESOLVED":
        steps_text = "\n".join([f"{s.order}. {s.action}" for s in plan.plan])
        runbook_proposal = f"""
## Proposed Runbook: {alert.name}

### Trigger
Alert: {alert.name}
Labels: {dict(alert.labels)}

### Steps
{steps_text}

### Notes
- Generated from successful resolution on {datetime.now().isoformat()}
- Original match: {plan.runbook_name or 'None'}
- Tweaks applied: {', '.join(plan.tweaks_applied) if plan.tweaks_applied else 'None'}
"""

    return IncidentDocument(
        title=f"Incident: {alert.name}",
        summary=f"{verdict} - {investigation.synthesis[:200]}",
        timeline=timeline,
        root_cause=root_cause,
        resolution=resolution,
        lessons_learned=lessons,
        runbook_proposal=runbook_proposal
    )


@app.post("/v1/validate_and_document", response_model=ValidateAndDocumentResponse)
async def validate_and_document(request: ValidateAndDocumentRequest):
    """Validate resolution and generate incident documentation."""
    logger.info(f"Validating resolution for: {request.alert.name} [{request.request_id}]")

    try:
        # Validate the resolution
        verdict, evidence, confidence = await validate_resolution(
            request.alert, request.execution_result
        )

        # Generate documentation
        document = await generate_incident_document(
            request.alert,
            request.investigation,
            request.plan,
            request.execution_result,
            verdict
        )

        # Determine runbook action
        runbook_action = None
        if verdict == "RESOLVED":
            if request.plan.match_type == "SIMILAR":
                runbook_action = "UPDATE"
            elif request.plan.match_type == "GENERATED":
                runbook_action = "CREATE"
        elif verdict == "FALSE_POSITIVE" and request.plan.runbook_id:
            runbook_action = "REVIEW"

        return ValidateAndDocumentResponse(
            request_id=request.request_id,
            verdict=verdict,
            validation_evidence=evidence,
            confidence=confidence,
            document=document,
            runbook_action=runbook_action,
            escalation_reason=f"Validation: {verdict}" if verdict in ("STILL_FAILING", "PARTIAL") else None,
            fallback_used=False
        )

    except Exception as e:
        logger.error(f"Validation failed: {e}")
        return ValidateAndDocumentResponse(
            request_id=request.request_id,
            verdict="STILL_FAILING",
            validation_evidence=[f"Validation error: {str(e)[:100]}"],
            confidence=0.0,
            document=IncidentDocument(
                title=f"Incident: {request.alert.name}",
                summary=f"Validation failed: {str(e)[:100]}",
                timeline=["Validation error occurred"],
                root_cause="Unknown - validation failed",
                resolution="Manual investigation required",
                lessons_learned=[]
            ),
            runbook_action=None,
            escalation_reason=f"Validation error: {str(e)[:100]}",
            fallback_used=False
        )


def main():
    """Run the server."""
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")
    logger.info(f"Starting A2A Orchestrator on {host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
