"""Merge and ranking algorithm for multi-path retrieval.

Implements:
- Path weighting (graph > vectors > documents > fallback)
- Freshness decay for all paths
- Success bonus for proven solutions
- Domain match bonus for aligned results
- Deduplication with graph priority
- Score explanation for transparency
"""

import logging
import math
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

# =============================================================================
# Configuration
# =============================================================================

# Path weights - graph traversal trusted most
PATH_WEIGHTS = {
    "graph_traversal": 1.3,
    "problem_vectors": 1.0,
    "document_content": 0.9,
    "legacy_fallback": 0.7,
}

# Time decay configuration (in days)
FRESHNESS_HALF_LIFE_DAYS = 30.0  # Score halves every 30 days of staleness
FRESHNESS_MAX_PENALTY = 0.4  # Minimum freshness multiplier (never below 40%)

# Bonus thresholds
SUCCESS_RATE_THRESHOLD = 0.8  # Minimum success rate for bonus
SUCCESS_MIN_EXECUTIONS = 3  # Minimum executions to apply success bonus
SUCCESS_BONUS_MAX = 0.2  # Maximum bonus for proven solutions

# Domain alignment bonus
DOMAIN_MATCH_BONUS = 0.15


# =============================================================================
# Scoring Functions
# =============================================================================

def freshness_factor(
    timestamp: Optional[str],
    half_life_days: float = FRESHNESS_HALF_LIFE_DAYS
) -> float:
    """Calculate freshness decay factor for a result.

    Uses exponential decay: factor = 2^(-days_stale / half_life)
    Capped at FRESHNESS_MAX_PENALTY to avoid over-penalizing old content.

    Args:
        timestamp: ISO format timestamp of last update/use
        half_life_days: Number of days for score to halve

    Returns:
        Float between FRESHNESS_MAX_PENALTY and 1.0
    """
    if not timestamp:
        return FRESHNESS_MAX_PENALTY  # Unknown age gets penalty

    try:
        if isinstance(timestamp, str):
            # Handle various ISO formats
            ts = timestamp.replace("Z", "+00:00")
            if "+" not in ts and "T" in ts:
                ts = ts + "+00:00"
            last_update = datetime.fromisoformat(ts.replace("+00:00", ""))
        else:
            last_update = timestamp

        now = datetime.utcnow()
        days_stale = (now - last_update).total_seconds() / 86400

        if days_stale <= 0:
            return 1.0

        # Exponential decay: 2^(-days/half_life)
        factor = math.pow(2, -days_stale / half_life_days)
        return max(factor, FRESHNESS_MAX_PENALTY)

    except Exception as e:
        logger.warning(f"Failed to parse timestamp {timestamp}: {e}")
        return FRESHNESS_MAX_PENALTY


def success_bonus(
    success_rate: Optional[float],
    execution_count: Optional[int]
) -> float:
    """Calculate bonus for proven successful solutions.

    Only applies if:
    - success_rate >= SUCCESS_RATE_THRESHOLD
    - execution_count >= SUCCESS_MIN_EXECUTIONS

    Args:
        success_rate: Float between 0.0 and 1.0
        execution_count: Number of times executed

    Returns:
        Bonus between 0.0 and SUCCESS_BONUS_MAX
    """
    if success_rate is None or execution_count is None:
        return 0.0

    if execution_count < SUCCESS_MIN_EXECUTIONS:
        return 0.0

    if success_rate < SUCCESS_RATE_THRESHOLD:
        return 0.0

    # Linear scale from threshold to 100%
    # At 80% -> 0, at 100% -> SUCCESS_BONUS_MAX
    scale = (success_rate - SUCCESS_RATE_THRESHOLD) / (1.0 - SUCCESS_RATE_THRESHOLD)
    return scale * SUCCESS_BONUS_MAX


def domain_match_bonus(query_domain: Optional[str], result_domain: Optional[str]) -> float:
    """Calculate bonus for domain alignment.

    Args:
        query_domain: Detected domain from query
        result_domain: Domain of the result

    Returns:
        DOMAIN_MATCH_BONUS if domains match, 0.0 otherwise
    """
    if not query_domain or not result_domain:
        return 0.0

    if query_domain.lower() == result_domain.lower():
        return DOMAIN_MATCH_BONUS

    # Partial match for related domains
    related_domains = {
        "kubernetes": ["k8s", "container", "pod"],
        "k8s": ["kubernetes", "container", "pod"],
        "dns": ["network", "networking"],
        "network": ["dns", "networking", "firewall"],
        "observability": ["monitoring", "metrics", "alerts"],
        "monitoring": ["observability", "metrics", "alerts"],
    }

    query_lower = query_domain.lower()
    result_lower = result_domain.lower()

    if query_lower in related_domains:
        if result_lower in related_domains[query_lower]:
            return DOMAIN_MATCH_BONUS * 0.5  # Half bonus for related

    return 0.0


def compute_final_score(
    result: Dict[str, Any],
    path_name: str,
    query_domain: Optional[str] = None
) -> Tuple[float, Dict[str, float]]:
    """Compute final score for a result with all factors.

    Args:
        result: Result dict with score, timestamps, success_rate, etc.
        path_name: Which retrieval path ("graph_traversal", "problem_vectors", etc.)
        query_domain: Detected domain from query for alignment bonus

    Returns:
        Tuple of (final_score, breakdown_dict)
    """
    breakdown = {}

    # Base score from retrieval
    base_score = float(result.get("score", 0.5))
    breakdown["base_score"] = base_score

    # Path weight
    path_weight = PATH_WEIGHTS.get(path_name, 1.0)
    breakdown["path_weight"] = path_weight

    # Freshness factor
    timestamp = (
        result.get("last_used") or
        result.get("last_executed") or
        result.get("updated_at") or
        result.get("indexed_at") or
        result.get("created_at")
    )
    fresh = freshness_factor(timestamp)
    breakdown["freshness"] = fresh

    # Success bonus
    s_bonus = success_bonus(
        result.get("success_rate"),
        result.get("execution_count")
    )
    breakdown["success_bonus"] = s_bonus

    # Domain match bonus
    d_bonus = domain_match_bonus(query_domain, result.get("domain"))
    breakdown["domain_bonus"] = d_bonus

    # Compute final score
    # Formula: (base * path_weight * freshness) + bonuses
    final = (base_score * path_weight * fresh) + s_bonus + d_bonus
    breakdown["final_score"] = final

    return final, breakdown


# =============================================================================
# Deduplication
# =============================================================================

def deduplicate_prefer_graph(
    results: List[Dict[str, Any]],
    id_fields: List[str] = None
) -> List[Dict[str, Any]]:
    """Deduplicate results, preferring graph source on collision.

    Deduplicates based on:
    1. neo4j_id (primary key for dual-indexed items)
    2. Fallback to id field

    Graph traversal results are kept over vector search results when
    the same item appears in both.

    Args:
        results: List of result dicts (already scored)
        id_fields: Fields to check for identity (default: neo4j_id, id)

    Returns:
        Deduplicated list
    """
    if id_fields is None:
        id_fields = ["neo4j_id", "id"]

    seen = {}  # key -> (result, is_graph)

    for result in results:
        # Find an identifier
        item_id = None
        for field in id_fields:
            if result.get(field):
                item_id = str(result[field])
                break

        if not item_id:
            # No ID, include anyway (shouldn't happen)
            continue

        is_graph = result.get("_source") == "graph_traversal"

        if item_id in seen:
            existing, existing_is_graph = seen[item_id]

            # Graph always wins
            if is_graph and not existing_is_graph:
                seen[item_id] = (result, True)
            elif not is_graph and existing_is_graph:
                # Keep existing graph result
                pass
            elif result.get("_final_score", 0) > existing.get("_final_score", 0):
                # Same source type, keep higher score
                seen[item_id] = (result, is_graph)
        else:
            seen[item_id] = (result, is_graph)

    return [item for item, _ in seen.values()]


# =============================================================================
# Main Merge Function
# =============================================================================

def merge_and_rank(
    path_results: Dict[str, List[Dict[str, Any]]],
    query_domain: Optional[str] = None,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """Merge results from multiple paths, score, deduplicate, and rank.

    Args:
        path_results: Dict mapping path name to list of results
            e.g. {"graph_traversal": [...], "problem_vectors": [...]}
        query_domain: Detected domain for alignment bonus
        limit: Maximum results to return

    Returns:
        Sorted list of deduplicated results with _final_score and _score_breakdown
    """
    all_results = []

    for path_name, results in path_results.items():
        for result in results:
            # Skip error results
            if result.get("error"):
                continue

            # Compute score
            final_score, breakdown = compute_final_score(
                result, path_name, query_domain
            )

            # Enrich result
            enriched = {
                **result,
                "_source": path_name,
                "_final_score": final_score,
                "_score_breakdown": breakdown,
            }
            all_results.append(enriched)

    # Deduplicate, preferring graph sources
    deduplicated = deduplicate_prefer_graph(all_results)

    # Sort by final score descending
    deduplicated.sort(key=lambda x: x.get("_final_score", 0), reverse=True)

    return deduplicated[:limit]


# =============================================================================
# Explanation / Debugging
# =============================================================================

def explain_score(result: Dict[str, Any]) -> str:
    """Generate human-readable explanation of a result's score.

    Args:
        result: Result with _score_breakdown from merge_and_rank

    Returns:
        Multi-line string explaining the score
    """
    breakdown = result.get("_score_breakdown", {})
    source = result.get("_source", "unknown")

    lines = [
        f"Source: {source}",
        f"Base Score: {breakdown.get('base_score', 'N/A'):.3f}",
        f"Path Weight: {breakdown.get('path_weight', 'N/A'):.2f}x",
        f"Freshness: {breakdown.get('freshness', 'N/A'):.2f}x",
        f"Success Bonus: +{breakdown.get('success_bonus', 0):.3f}",
        f"Domain Bonus: +{breakdown.get('domain_bonus', 0):.3f}",
        f"Final Score: {breakdown.get('final_score', 'N/A'):.3f}",
    ]

    return "\n".join(lines)


def explain_all(results: List[Dict[str, Any]]) -> str:
    """Explain scores for all results.

    Args:
        results: List of results from merge_and_rank

    Returns:
        Formatted explanation of all results
    """
    lines = []
    for i, result in enumerate(results, 1):
        title = (
            result.get("title") or
            result.get("description", "")[:50] or
            result.get("id", "Unknown")
        )
        lines.append(f"#{i}: {title}")
        lines.append(explain_score(result))
        lines.append("")

    return "\n".join(lines)
