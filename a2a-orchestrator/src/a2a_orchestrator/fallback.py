"""Qwen Fallback - Local LLM assessment when Gemini quota exhausted."""

import os
import json
import logging
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Local qwen via LiteLLM or direct endpoint
QWEN_URL = os.environ.get("QWEN_URL", "http://litellm.ai-platform.svc.cluster.local:4000/v1/chat/completions")
QWEN_API_KEY = os.environ.get("QWEN_API_KEY", "")
QWEN_MODEL = os.environ.get("QWEN_MODEL", "qwen/qwen2.5-coder-14b")


@dataclass
class FallbackResult:
    verdict: str  # ACTIONABLE, UNKNOWN, FALSE_POSITIVE
    confidence: float
    synthesis: str
    suggested_action: Optional[str] = None


FALLBACK_PROMPT = """You are a simplified alert assessment agent. Analyze this alert and determine if it requires action.

Alert: {name}
Severity: {severity}
Description: {description}
Labels: {labels}

Based on the alert name and severity, provide a quick assessment.

Output JSON with:
- verdict: ACTIONABLE (needs fix), UNKNOWN (needs investigation), FALSE_POSITIVE (likely noise)
- confidence: 0.0-1.0 (lower since we have limited context)
- synthesis: Brief explanation (1 sentence)
- suggested_action: What to check first (if actionable)
"""


async def qwen_fallback_assess(alert) -> FallbackResult:
    """Assess alert using local qwen when Gemini unavailable.

    This is a simplified assessment without MCP tool access.
    It relies on pattern matching and the alert metadata only.
    """
    # Build prompt
    labels_str = json.dumps(
        dict(alert.labels) if hasattr(alert.labels, '__dict__') else
        alert.labels.model_dump() if hasattr(alert.labels, 'model_dump') else
        str(alert.labels),
        default=str
    )

    prompt = FALLBACK_PROMPT.format(
        name=alert.name,
        severity=alert.severity,
        description=alert.description or "N/A",
        labels=labels_str
    )

    try:
        headers = {"Content-Type": "application/json"}
        if QWEN_API_KEY:
            headers["Authorization"] = f"Bearer {QWEN_API_KEY}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                QWEN_URL,
                headers=headers,
                json={
                    "model": QWEN_MODEL,
                    "messages": [
                        {"role": "system", "content": "You are an alert triage assistant. Output valid JSON only."},
                        {"role": "user", "content": prompt}
                    ],
                    "response_format": {"type": "json_object"},
                    "max_tokens": 300,
                    "temperature": 0.2
                }
            )

            if response.status_code != 200:
                logger.warning(f"Qwen returned {response.status_code}, using heuristic")
                return heuristic_assess(alert)

            result = response.json()
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "{}")

            try:
                assessment = json.loads(content)
                return FallbackResult(
                    verdict=assessment.get("verdict", "UNKNOWN"),
                    confidence=min(float(assessment.get("confidence", 0.4)), 0.7),  # Cap at 0.7 for fallback
                    synthesis=assessment.get("synthesis", "Assessed via fallback LLM"),
                    suggested_action=assessment.get("suggested_action")
                )
            except json.JSONDecodeError:
                logger.warning("Qwen returned invalid JSON, using heuristic")
                return heuristic_assess(alert)

    except httpx.TimeoutException:
        logger.warning("Qwen timed out, using heuristic")
        return heuristic_assess(alert)
    except Exception as e:
        logger.error(f"Qwen fallback failed: {e}")
        return heuristic_assess(alert)


def heuristic_assess(alert) -> FallbackResult:
    """Pure heuristic assessment when all LLMs unavailable.

    Uses simple pattern matching on alert name and severity.
    """
    name_lower = alert.name.lower()
    severity_lower = alert.severity.lower()

    # Critical patterns - likely actionable
    critical_patterns = [
        "oom", "crashloop", "down", "failed", "error", "critical",
        "disk", "full", "exhausted", "unreachable", "timeout"
    ]

    # Warning patterns - need investigation
    warning_patterns = [
        "high", "elevated", "slow", "latency", "pending", "degraded"
    ]

    # Likely noise patterns
    noise_patterns = [
        "info", "resolved", "cleared", "recovered", "normal"
    ]

    # Check patterns
    if any(p in name_lower for p in critical_patterns) or severity_lower in ("critical", "error"):
        return FallbackResult(
            verdict="ACTIONABLE",
            confidence=0.5,  # Low confidence for heuristic
            synthesis=f"Alert '{alert.name}' matches critical patterns. Requires investigation.",
            suggested_action="Check pod/service status and recent events"
        )

    if any(p in name_lower for p in warning_patterns) or severity_lower == "warning":
        return FallbackResult(
            verdict="UNKNOWN",
            confidence=0.4,
            synthesis=f"Alert '{alert.name}' may indicate an issue. Manual review recommended.",
            suggested_action="Review metrics and logs for the affected component"
        )

    if any(p in name_lower for p in noise_patterns) or severity_lower == "info":
        return FallbackResult(
            verdict="FALSE_POSITIVE",
            confidence=0.5,
            synthesis=f"Alert '{alert.name}' appears informational.",
            suggested_action=None
        )

    # Default to unknown
    return FallbackResult(
        verdict="UNKNOWN",
        confidence=0.3,
        synthesis=f"Unable to classify alert '{alert.name}'. Manual review required.",
        suggested_action="Review alert context and related metrics"
    )
