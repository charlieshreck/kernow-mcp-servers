"""A2A Orchestrator Server - FastAPI service for parallel alert investigation."""

import os
import logging
import asyncio
from typing import Optional, List, Dict, Any
from datetime import datetime

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# Import canonical models from models.py - single source of truth
from a2a_orchestrator.models import (
    InvestigationGrade,
    SpecialistFinding,
    InvestigateRequest as InvestigateRequestModel,
    InvestigateResponse as InvestigateResponseModel,
    PlanStep,
    PlanMatchType,
    DecisionAction,
    PlanAndDecideRequest as PlanRequestModel,
    PlanAndDecideResponse as PlanResponseModel,
    ValidateAndDocumentRequest as ValidateRequestModel,
    ValidateAndDocumentResponse as ValidateResponseModel,
    IncidentDocument,
    ValidationVerdict,
)
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
# Request/Response Models (API layer - use canonical models from models.py)
# =============================================================================

class AlertLabels(BaseModel):
    """Alert labels for Kubernetes alerts."""
    namespace: Optional[str] = None
    pod: Optional[str] = None
    service: Optional[str] = None
    node: Optional[str] = None

    class Config:
        extra = "allow"


class Alert(BaseModel):
    """Alert input for investigation."""
    name: str
    labels: AlertLabels = AlertLabels()
    severity: str = "warning"
    description: Optional[str] = None
    fingerprint: Optional[str] = None


class InvestigateRequest(BaseModel):
    """API request for investigation."""
    request_id: str
    alert: Alert
    context: dict = {}


# Response uses canonical InvestigateResponseModel from models.py
# Finding uses SpecialistFinding from models.py


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


async def investigate_parallel(alert: Alert, timeout: float = 15.0) -> List[SpecialistFinding]:
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

    # Collect results - convert to SpecialistFinding
    findings = []
    for name, task in tasks.items():
        if task in done and not task.cancelled():
            try:
                result = task.result()
                if result:
                    # Convert specialist Finding to canonical SpecialistFinding
                    findings.append(SpecialistFinding(
                        specialist=result.agent,
                        status=result.status,
                        summary=result.issue or f"Alert: {alert.name}",
                        evidence=result.evidence if isinstance(result.evidence, list) else [result.evidence] if result.evidence else [],
                        tools_called=result.tools_used,
                        confidence=0.8 if result.status in ("PASS", "WARN") else 0.5,
                        latency_ms=result.latency_ms,
                        error=None
                    ))
            except Exception as e:
                logger.error(f"Specialist {name} failed: {e}")
                findings.append(SpecialistFinding(
                    specialist=name,
                    status="ERROR",
                    summary=f"Investigation failed: {str(e)[:100]}",
                    evidence=[],
                    tools_called=[],
                    confidence=0.0,
                    latency_ms=0,
                    error=str(e)[:200]
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


@app.post("/v1/investigate", response_model=InvestigateResponseModel)
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

        # Determine grade based on findings
        fail_count = sum(1 for f in findings if f.status == "FAIL")
        error_count = sum(1 for f in findings if f.status == "ERROR")
        total = len(findings) or 1

        if error_count > total / 2:
            grade = InvestigationGrade.INCONCLUSIVE
        elif fail_count > 0 and any(f.status == "PASS" for f in findings):
            grade = InvestigationGrade.CONFLICTING
        elif fail_count > 0:
            grade = InvestigationGrade.CLEAR
        else:
            grade = InvestigationGrade.PARTIAL

        # Determine recommended domain based on highest-weighted failing specialist
        recommended_domain = "infrastructure"  # default
        for name in ["security", "devops", "sre", "network", "database"]:
            for f in findings:
                if f.specialist == name and f.status == "FAIL":
                    recommended_domain = name
                    break

        latency_ms = int((datetime.now() - start_time).total_seconds() * 1000)

        return InvestigateResponseModel(
            request_id=request.request_id,
            grade=grade,
            confidence=synthesis_result.confidence,
            findings=findings,
            synthesis=synthesis_result.synthesis,
            recommended_domain=recommended_domain,
            escalation_reason=None if synthesis_result.verdict == "ACTIONABLE" else "Investigation inconclusive",
            fallback_used=False,
            latency_ms=latency_ms
        )

    except Exception as e:
        logger.warning(f"A2A investigation failed, using fallback: {e}")

        # Fallback to qwen
        fallback_result = await qwen_fallback_assess(request.alert)
        latency_ms = int((datetime.now() - start_time).total_seconds() * 1000)

        return InvestigateResponseModel(
            request_id=request.request_id,
            grade=InvestigationGrade.INCONCLUSIVE,
            confidence=fallback_result.confidence,
            findings=[SpecialistFinding(
                specialist="qwen-fallback",
                status="WARN" if fallback_result.verdict == "ACTIONABLE" else "PASS",
                summary=fallback_result.synthesis,
                evidence=[],
                tools_called=[],
                confidence=fallback_result.confidence,
                latency_ms=0,
                error=None
            )],
            synthesis=fallback_result.synthesis,
            recommended_domain="infrastructure",
            escalation_reason="Fallback assessment used",
            fallback_used=True,
            latency_ms=latency_ms
        )


# =============================================================================
# Plan & Decide - Use canonical models from models.py
# PlanStep, PlanAndDecideRequest/Response, ValidateAndDocumentRequest/Response
# are imported at the top of the file
# =============================================================================

# API-specific request models that accept the Alert model defined here
class PlanAndDecideRequest(BaseModel):
    """API request for plan generation."""
    request_id: str
    alert: Alert
    investigation: dict  # Accept dict for flexibility, convert to model internally
    context: dict = {}


class ValidateAndDocumentRequest(BaseModel):
    """API request for validation."""
    request_id: str
    alert: Alert
    investigation: dict
    plan: dict
    execution_result: dict
    context: dict = {}


# =============================================================================
# Planner Logic
# =============================================================================

RUNBOOK_EXACT_THRESHOLD = float(os.environ.get("RUNBOOK_EXACT_THRESHOLD", "0.95"))
RUNBOOK_SIMILAR_THRESHOLD = float(os.environ.get("RUNBOOK_SIMILAR_THRESHOLD", "0.80"))


async def search_runbooks_for_alert(alert: Alert, investigation: dict) -> dict:
    """Search knowledge-mcp for matching runbooks using tiered lookup.

    Tier 1: Exact match by alertname
    Tier 2: Semantic search fallback
    """
    from a2a_orchestrator.mcp_client import call_mcp_tool

    # Build context for semantic search fallback
    context_parts = []
    if alert.description:
        context_parts.append(alert.description[:100])

    # Add key issues from investigation
    findings = investigation.get("findings", [])
    for finding in findings[:3]:
        issue = finding.get("summary") or finding.get("issue")
        if issue:
            context_parts.append(issue[:50])

    context = " ".join(context_parts)[:300] if context_parts else None

    # Use tiered lookup: exact match by alertname first, then semantic fallback
    result = await call_mcp_tool("knowledge", "lookup_runbook_tiered", {
        "alertname": alert.name,
        "context": context,
        "exact_threshold": RUNBOOK_EXACT_THRESHOLD,
        "semantic_threshold": RUNBOOK_SIMILAR_THRESHOLD
    })

    # Convert tiered lookup response to expected format
    if result.get("status") == "success":
        output = result.get("output", {})
        if isinstance(output, str):
            import json
            try:
                output = json.loads(output)
            except json.JSONDecodeError:
                return result

        match_type = output.get("match_type", "NO_MATCH")
        runbook = output.get("runbook")

        if runbook and match_type in ("EXACT", "SIMILAR"):
            # Transform to list format expected by downstream code
            return {
                "status": "success",
                "output": [{
                    "id": runbook.get("id"),
                    "score": output.get("score", 1.0 if match_type == "EXACT" else 0.8),
                    "name": runbook.get("title"),
                    "alertname": runbook.get("alertname"),
                    "steps": runbook.get("steps", []),
                    "automation_level": runbook.get("automation_level", "manual"),
                    "path": runbook.get("path"),
                    "match_type": match_type,
                    "alternatives": output.get("alternatives", [])
                }]
            }

    return result


def classify_runbook_match(score: float) -> str:
    """Classify runbook match type based on score."""
    if score >= RUNBOOK_EXACT_THRESHOLD:
        return "EXACT"
    elif score >= RUNBOOK_SIMILAR_THRESHOLD:
        return "SIMILAR"
    else:
        return "NO_MATCH"


async def generate_plan_from_investigation(alert: Alert, investigation: dict) -> List[PlanStep]:
    """Generate a plan based on investigation findings when no runbook matches."""
    from a2a_orchestrator.llm import gemini_analyze
    from a2a_orchestrator.tool_catalog import TOOL_CATALOG, command_to_tool

    # Use Gemini to generate plan steps - handle both dict and model findings
    findings = investigation.get("findings", [])
    findings_text = "\n".join([
        f"- {f.get('specialist', f.get('agent', 'unknown'))}: {f.get('summary', f.get('issue', ''))} (recommendation: {f.get('recommendation', 'N/A')})"
        for f in findings if f.get('summary') or f.get('issue')
    ])

    # Available tools for the plan
    tools_list = "\n".join([
        f"- {name}: {spec.description} (required args: {spec.required_args})"
        for name, spec in TOOL_CATALOG.items()
    ])

    system_prompt = f"""You are a remediation planner. Generate a step-by-step plan to resolve this alert.

IMPORTANT: Use tool-based execution instead of raw commands. Available tools:
{tools_list}

Output JSON with a "steps" array where each step has:
- order: step number (1, 2, 3...)
- action: what to do (human-readable description)
- tool: MCP tool name from the list above (e.g., "kubectl_restart_deployment")
- arguments: dict of tool arguments (e.g., {{"deployment_name": "my-app", "namespace": "prod"}})
- rollback_tool: tool to undo this step (optional)
- rollback_args: arguments for rollback tool (optional)
- risk: low/medium/high

Example step:
{{"order": 1, "action": "Restart the failing pod", "tool": "kubectl_delete_pod", "arguments": {{"pod_name": "app-xyz", "namespace": "prod"}}, "risk": "medium"}}"""

    try:
        result = await gemini_analyze(
            system_prompt=system_prompt,
            alert=alert,
            evidence=f"Investigation findings:\n{findings_text}\n\nSynthesis: {investigation.get('synthesis', 'No synthesis available')}"
        )

        steps = result.get("steps", [])
        plan_steps = []

        for s in steps[:5]:  # Max 5 steps
            # If Gemini returned legacy command, try to convert it
            if s.get("command") and not s.get("tool"):
                tool_name, args = command_to_tool(s["command"])
                if tool_name:
                    s["tool"] = tool_name
                    s["arguments"] = args

            plan_steps.append(PlanStep(**s))

        return plan_steps

    except Exception as e:
        logger.warning(f"Plan generation failed: {e}")
        # Return generic investigation step
        return [PlanStep(
            order=1,
            action="Manual investigation required",
            tool=None,
            arguments=None,
            risk="low"
        )]


def decide_action(
    match_type: str,
    investigation: dict,
    plan: List[PlanStep],
    alert: Alert
) -> tuple[str, str, bool]:
    """Decide whether to execute, escalate, or wait.

    Returns: (decision, rationale, requires_approval)
    """
    # Get grade/confidence from investigation dict
    grade = investigation.get("grade", "INCONCLUSIVE")
    confidence = investigation.get("confidence", 0.5)

    # Always escalate if investigation was inconclusive or conflicting
    if grade in ("INCONCLUSIVE", "CONFLICTING"):
        return "ESCALATE", "Investigation inconclusive - human review needed", True

    # Always escalate if no plan
    if match_type == "NO_PLAN" or not plan:
        return "ESCALATE", "No matching runbook and could not generate plan", True

    # High-risk plans always need approval
    high_risk_steps = [s for s in plan if s.risk == "high"]
    if high_risk_steps:
        return "EXECUTE", f"Plan has {len(high_risk_steps)} high-risk steps - approval required", True

    # EXACT match with high confidence can auto-execute
    if match_type == "EXACT" and confidence >= 0.9:
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


@app.post("/v1/plan_and_decide", response_model=PlanResponseModel)
@app.post("/v1/plan", response_model=PlanResponseModel)
async def plan_and_decide(request: PlanAndDecideRequest):
    """Generate execution plan and decide action based on investigation.

    Available at both /v1/plan_and_decide (legacy) and /v1/plan (preferred).
    """
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
            # Parse runbook search results (handles JSON, markdown, or structured output)
            try:
                import json
                import re

                runbooks = None
                if isinstance(output, list):
                    runbooks = output
                elif isinstance(output, str):
                    # Try direct JSON parse first
                    try:
                        runbooks = json.loads(output)
                    except json.JSONDecodeError:
                        # Try to extract JSON from markdown code blocks
                        json_match = re.search(r'```(?:json)?\s*([\[\{].*?[\]\}])\s*```', output, re.DOTALL)
                        if json_match:
                            runbooks = json.loads(json_match.group(1))
                        else:
                            # Try to find bare JSON array or object
                            array_match = re.search(r'(\[[\s\S]*\])', output)
                            if array_match:
                                try:
                                    runbooks = json.loads(array_match.group(1))
                                except json.JSONDecodeError:
                                    pass

                if runbooks and isinstance(runbooks, list) and len(runbooks) > 0:
                    best_match = runbooks[0]
                    runbook_score = best_match.get("score", 0)
                    match_type = classify_runbook_match(runbook_score)

                    if match_type in ("EXACT", "SIMILAR"):
                        runbook_id = best_match.get("id")
                        runbook_name = best_match.get("name")

                        # Extract steps from runbook with tool-based execution support
                        from a2a_orchestrator.tool_catalog import command_to_tool

                        steps = best_match.get("steps", [])
                        for i, step in enumerate(steps[:5]):
                            tool_name = step.get("tool")
                            arguments = step.get("arguments")

                            # Convert legacy command to tool if needed
                            if not tool_name and step.get("command"):
                                tool_name, arguments = command_to_tool(step.get("command"))

                            plan.append(PlanStep(
                                order=i + 1,
                                action=step.get("action", str(step)),
                                tool=tool_name,
                                arguments=arguments,
                                command=step.get("command"),  # Keep for backwards compat
                                rollback_tool=step.get("rollback_tool"),
                                rollback_args=step.get("rollback_args"),
                                rollback=step.get("rollback"),  # Keep for backwards compat
                                risk=step.get("risk", "low")
                            ))

                        # Note any required tweaks for SIMILAR match
                        if match_type == "SIMILAR":
                            tweaks.append(f"Adapted from {runbook_name} (score: {runbook_score:.2f})")
            except Exception as e:
                logger.warning(f"Failed to parse runbook results: {e}")

        # If no runbook match, generate plan from investigation
        # Handle both dict investigation and "verdict" or "grade" field for compatibility
        investigation_dict = request.investigation
        grade = investigation_dict.get("grade", "CLEAR")
        is_actionable = grade in ("CLEAR", "PARTIAL") or investigation_dict.get("verdict") == "ACTIONABLE"

        if match_type in ("NO_MATCH", "NO_PLAN") and is_actionable:
            plan = await generate_plan_from_investigation(request.alert, investigation_dict)
            if plan:
                match_type = "GENERATED"

        # Decide action
        decision, rationale, requires_approval = decide_action(
            match_type, investigation_dict, plan, request.alert
        )

        # Calculate risk level
        if any(s.risk == "high" for s in plan):
            risk_level = "high"
        elif any(s.risk == "medium" for s in plan):
            risk_level = "medium"
        else:
            risk_level = "low"

        investigation_confidence = investigation_dict.get("confidence", 0.5)

        return PlanResponseModel(
            request_id=request.request_id,
            match_type=PlanMatchType(match_type) if match_type in [e.value for e in PlanMatchType] else PlanMatchType.NO_PLAN,
            runbook_id=runbook_id,
            runbook_name=runbook_name,
            runbook_score=runbook_score if runbook_score > 0 else None,
            plan=plan,
            tweaks_applied=tweaks,
            decision=DecisionAction.EXECUTE if decision == "EXECUTE" else DecisionAction.ESCALATE if decision == "ESCALATE" else DecisionAction.WAIT,
            decision_rationale=rationale,
            confidence=investigation_confidence * (runbook_score if runbook_score > 0 else 0.5),
            risk_level=risk_level,
            requires_approval=requires_approval,
            escalation_reason=rationale if decision == "ESCALATE" else None,
            fallback_used=False
        )

    except Exception as e:
        logger.error(f"Plan and decide failed: {e}")
        return PlanResponseModel(
            request_id=request.request_id,
            match_type=PlanMatchType.NO_PLAN,
            plan=[],
            tweaks_applied=[],
            decision=DecisionAction.ESCALATE,
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
    investigation: dict,
    plan: dict,
    execution_result: dict,
    verdict: str
) -> IncidentDocument:
    """Generate incident documentation."""
    from datetime import datetime

    # Extract values from dicts
    inv_grade = investigation.get("grade", "UNKNOWN")
    inv_confidence = investigation.get("confidence", 0.5)
    inv_synthesis = investigation.get("synthesis", "No synthesis available")

    plan_runbook_name = plan.get("runbook_name")
    plan_match_type = plan.get("match_type", "UNKNOWN")
    plan_decision = plan.get("decision", "UNKNOWN")
    plan_steps = plan.get("plan", [])
    plan_tweaks = plan.get("tweaks_applied", [])
    plan_runbook_id = plan.get("runbook_id")

    # Build timeline
    timeline = [
        f"Alert received: {alert.name} ({alert.severity})",
        f"Investigation completed: {inv_grade} ({inv_confidence:.0%} confidence)",
    ]

    if plan_runbook_name:
        timeline.append(f"Matched runbook: {plan_runbook_name} ({plan_match_type})")
    else:
        timeline.append(f"Plan generated: {plan_match_type}")

    timeline.append(f"Decision: {plan_decision}")

    if execution_result.get("started_at"):
        timeline.append(f"Execution started: {execution_result['started_at']}")
    if execution_result.get("completed_at"):
        timeline.append(f"Execution completed: {execution_result['completed_at']}")

    timeline.append(f"Validation result: {verdict}")

    # Root cause from investigation
    root_cause = inv_synthesis

    # Resolution summary
    if verdict == "RESOLVED":
        resolution = f"Successfully resolved via {plan_match_type} plan"
        if plan_runbook_name:
            resolution += f" (runbook: {plan_runbook_name})"
    elif verdict == "FALSE_POSITIVE":
        resolution = "Determined to be a false positive - no action required"
    else:
        resolution = "Partially resolved or still failing - manual follow-up required"

    # Lessons learned
    lessons = []
    if plan_match_type == "SIMILAR":
        lessons.append(f"Runbook '{plan_runbook_name}' should be updated to handle this case")
    if plan_match_type == "GENERATED":
        lessons.append("Consider creating a new runbook for this alert pattern")
    if verdict == "FALSE_POSITIVE":
        lessons.append("Consider tuning alert threshold or adding suppression rule")

    # Runbook proposal for non-exact matches
    runbook_proposal = None
    if plan_match_type in ("SIMILAR", "GENERATED") and verdict == "RESOLVED":
        steps_text = "\n".join([
            f"{s.get('order', i+1)}. {s.get('action', 'Unknown action')}"
            for i, s in enumerate(plan_steps)
        ])
        runbook_proposal = f"""
## Proposed Runbook: {alert.name}

### Trigger
Alert: {alert.name}
Labels: {dict(alert.labels)}

### Steps
{steps_text}

### Notes
- Generated from successful resolution on {datetime.now().isoformat()}
- Original match: {plan_runbook_name or 'None'}
- Tweaks applied: {', '.join(plan_tweaks) if plan_tweaks else 'None'}
"""

    return IncidentDocument(
        title=f"Incident: {alert.name}",
        summary=f"{verdict} - {inv_synthesis[:200]}",
        timeline=timeline,
        root_cause=root_cause,
        resolution=resolution,
        lessons_learned=lessons,
        runbook_proposal=runbook_proposal
    )


@app.post("/v1/validate_and_document", response_model=ValidateResponseModel)
@app.post("/v1/validate", response_model=ValidateResponseModel)
async def validate_and_document(request: ValidateAndDocumentRequest):
    """Validate resolution and generate incident documentation.

    Available at both /v1/validate_and_document (legacy) and /v1/validate (preferred).
    """
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

        # Determine runbook action - access plan as dict
        plan_dict = request.plan
        plan_match_type = plan_dict.get("match_type", "")
        plan_runbook_id = plan_dict.get("runbook_id")

        runbook_action = None
        if verdict == "RESOLVED":
            if plan_match_type == "SIMILAR":
                runbook_action = "UPDATE"
            elif plan_match_type == "GENERATED":
                runbook_action = "CREATE"
        elif verdict == "FALSE_POSITIVE" and plan_runbook_id:
            runbook_action = "REVIEW"

        return ValidateResponseModel(
            request_id=request.request_id,
            verdict=ValidationVerdict(verdict) if verdict in [e.value for e in ValidationVerdict] else ValidationVerdict.STILL_FAILING,
            validation_evidence=evidence,
            confidence=confidence,
            document=document,
            runbook_action=runbook_action,
            escalation_reason=f"Validation: {verdict}" if verdict in ("STILL_FAILING", "PARTIAL") else None,
            fallback_used=False
        )

    except Exception as e:
        logger.error(f"Validation failed: {e}")
        return ValidateResponseModel(
            request_id=request.request_id,
            verdict=ValidationVerdict.STILL_FAILING,
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
