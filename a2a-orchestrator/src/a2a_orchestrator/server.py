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


def main():
    """Run the server."""
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")
    logger.info(f"Starting A2A Orchestrator on {host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
