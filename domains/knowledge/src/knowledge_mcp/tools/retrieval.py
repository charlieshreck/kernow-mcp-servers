"""Multi-path retrieval orchestration for knowledge search.

Implements the three-path retrieval strategy:
- Path 1: Problem Vectors (Qdrant knowledge_nodes)
- Path 2: Document Content (Qdrant documents collection)
- Path 3: Graph Traversal (Neo4j - only if domain_confidence > 0.8)

All paths run in parallel for performance (<400ms budget).
Results are merged, deduplicated, and ranked using utils/ranking.py.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

from fastmcp import FastMCP

from knowledge_mcp.utils.ranking import merge_and_rank, explain_score

logger = logging.getLogger(__name__)

# =============================================================================
# Configuration
# =============================================================================

# Domain classification keywords
DOMAIN_KEYWORDS = {
    "kubernetes": [
        "pod", "deployment", "service", "namespace", "kubectl", "k8s",
        "container", "crashloopbackoff", "oom", "evicted", "pending",
        "kubelet", "node", "ingress", "helm", "argocd", "talos"
    ],
    "dns": [
        "dns", "resolve", "nslookup", "dig", "domain", "record",
        "a record", "cname", "mx", "unbound", "adguard", "pihole"
    ],
    "network": [
        "network", "firewall", "opnsense", "vlan", "subnet", "route",
        "nat", "dhcp", "gateway", "switch", "wifi", "unifi", "tailscale"
    ],
    "security": [
        "security", "ssl", "tls", "certificate", "auth", "permission",
        "rbac", "secret", "credential", "token", "encrypt", "infisical"
    ],
    "observability": [
        "alert", "metric", "log", "trace", "monitoring", "prometheus",
        "grafana", "victoriametrics", "loki", "coroot", "keep", "gatus"
    ],
    "storage": [
        "storage", "pvc", "volume", "disk", "zfs", "truenas", "nfs",
        "iscsi", "backup", "snapshot", "pool", "dataset"
    ],
    "media": [
        "plex", "sonarr", "radarr", "transmission", "jellyfin",
        "media", "transcode", "library", "stream"
    ],
    "infrastructure": [
        "proxmox", "vm", "lxc", "container", "terraform", "ansible",
        "cloudflare", "tunnel", "caddy", "traefik", "nginx"
    ],
}

# Minimum confidence to trigger graph traversal
GRAPH_DOMAIN_CONFIDENCE_THRESHOLD = 0.8

# Timeout for each retrieval path (ms)
PATH_TIMEOUT_MS = 300

# Default limits
DEFAULT_VECTOR_LIMIT = 15
DEFAULT_DOCUMENT_LIMIT = 10
DEFAULT_GRAPH_LIMIT = 10


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class PathResult:
    """Result from a single retrieval path."""
    path_name: str
    results: List[Dict[str, Any]]
    latency_ms: float
    error: Optional[str] = None


@dataclass
class RetrievalResult:
    """Combined result from all retrieval paths."""
    query: str
    detected_domain: Optional[str]
    domain_confidence: float
    results: List[Dict[str, Any]]
    path_results: Dict[str, PathResult] = field(default_factory=dict)
    total_latency_ms: float = 0.0
    paths_executed: List[str] = field(default_factory=list)


# =============================================================================
# Domain Classification
# =============================================================================

def keyword_classify(query: str) -> Tuple[Optional[str], float]:
    """Classify query domain using keyword matching.

    Returns:
        Tuple of (domain, confidence) where confidence is 0.0-1.0
    """
    query_lower = query.lower()
    words = set(re.findall(r'\w+', query_lower))

    domain_scores = {}
    for domain, keywords in DOMAIN_KEYWORDS.items():
        matches = sum(1 for kw in keywords if kw in query_lower or kw in words)
        if matches > 0:
            # Score based on match density
            domain_scores[domain] = matches / len(keywords)

    if not domain_scores:
        return None, 0.0

    # Return highest scoring domain
    best_domain = max(domain_scores, key=domain_scores.get)
    confidence = min(domain_scores[best_domain] * 3, 1.0)  # Scale up, cap at 1.0

    return best_domain, confidence


# =============================================================================
# Retrieval Path Functions
# =============================================================================

async def path_problem_vectors(
    query: str,
    domain: Optional[str],
    limit: int = DEFAULT_VECTOR_LIMIT,
    min_score: float = 0.5
) -> PathResult:
    """Path 1: Search Qdrant knowledge_nodes collection.

    This is the primary path for finding similar problems and runbooks.
    """
    from knowledge_mcp.tools.qdrant import search_knowledge_nodes

    start = datetime.utcnow()
    try:
        results = await search_knowledge_nodes(
            query=query,
            node_type=None,  # Search both problems and runbooks
            domain=domain,
            limit=limit,
            min_score=min_score
        )

        # Handle error results
        if results and isinstance(results[0], dict) and results[0].get("error"):
            return PathResult(
                path_name="problem_vectors",
                results=[],
                latency_ms=(datetime.utcnow() - start).total_seconds() * 1000,
                error=results[0]["error"]
            )

        return PathResult(
            path_name="problem_vectors",
            results=results,
            latency_ms=(datetime.utcnow() - start).total_seconds() * 1000
        )

    except Exception as e:
        logger.error(f"path_problem_vectors failed: {e}")
        return PathResult(
            path_name="problem_vectors",
            results=[],
            latency_ms=(datetime.utcnow() - start).total_seconds() * 1000,
            error=str(e)
        )


async def path_document_content(
    query: str,
    domain: Optional[str],
    limit: int = DEFAULT_DOCUMENT_LIMIT,
    min_score: float = 0.4
) -> PathResult:
    """Path 2: Search Qdrant documents collection.

    Finds solutions, artifacts, and documentation content.
    """
    from knowledge_mcp.tools.qdrant import vector_search_documents

    start = datetime.utcnow()
    try:
        results = await vector_search_documents(
            query=query,
            doc_type=None,  # Search all document types
            domain=domain,
            limit=limit,
            min_score=min_score
        )

        # Handle error results
        if results and isinstance(results[0], dict) and results[0].get("error"):
            return PathResult(
                path_name="document_content",
                results=[],
                latency_ms=(datetime.utcnow() - start).total_seconds() * 1000,
                error=results[0]["error"]
            )

        return PathResult(
            path_name="document_content",
            results=results,
            latency_ms=(datetime.utcnow() - start).total_seconds() * 1000
        )

    except Exception as e:
        logger.error(f"path_document_content failed: {e}")
        return PathResult(
            path_name="document_content",
            results=[],
            latency_ms=(datetime.utcnow() - start).total_seconds() * 1000,
            error=str(e)
        )


async def path_graph_traversal(
    query: str,
    domain: str,
    limit: int = DEFAULT_GRAPH_LIMIT
) -> PathResult:
    """Path 3: Neo4j graph traversal for proven runbooks.

    Only executed when domain_confidence > GRAPH_DOMAIN_CONFIDENCE_THRESHOLD.
    Finds runbooks with high success rates in the detected domain.
    """
    from knowledge_mcp.tools.neo4j import get_proven_runbooks, get_problems_by_domain

    start = datetime.utcnow()
    try:
        # Get proven runbooks for this domain
        runbooks = await get_proven_runbooks(
            domain=domain,
            min_success_rate=0.7,
            min_executions=3,
            limit=limit
        )

        # Handle error
        if runbooks and isinstance(runbooks[0], dict) and runbooks[0].get("error"):
            return PathResult(
                path_name="graph_traversal",
                results=[],
                latency_ms=(datetime.utcnow() - start).total_seconds() * 1000,
                error=runbooks[0]["error"]
            )

        # Also get problems in this domain
        problems = await get_problems_by_domain(domain=domain, limit=limit // 2)

        # Combine results
        combined = []

        for r in runbooks:
            if not r.get("error"):
                combined.append({
                    **r,
                    "type": "runbook",
                    "score": r.get("success_rate", 0.5),
                    "domain": domain,
                })

        for p in problems:
            if not p.get("error"):
                combined.append({
                    **p,
                    "type": "problem",
                    "score": p.get("weight", 0.5),
                    "domain": domain,
                })

        return PathResult(
            path_name="graph_traversal",
            results=combined,
            latency_ms=(datetime.utcnow() - start).total_seconds() * 1000
        )

    except Exception as e:
        logger.error(f"path_graph_traversal failed: {e}")
        return PathResult(
            path_name="graph_traversal",
            results=[],
            latency_ms=(datetime.utcnow() - start).total_seconds() * 1000,
            error=str(e)
        )


async def path_legacy_fallback(
    query: str,
    limit: int = 10
) -> PathResult:
    """Fallback: Search legacy runbooks collection in Qdrant.

    Used when other paths return insufficient results.
    """
    from knowledge_mcp.tools.qdrant import search_runbooks

    start = datetime.utcnow()
    try:
        results = await search_runbooks(
            query=query,
            limit=limit,
            min_score=0.5
        )

        # Handle error results
        if results and isinstance(results[0], dict) and results[0].get("error"):
            return PathResult(
                path_name="legacy_fallback",
                results=[],
                latency_ms=(datetime.utcnow() - start).total_seconds() * 1000,
                error=results[0]["error"]
            )

        return PathResult(
            path_name="legacy_fallback",
            results=results,
            latency_ms=(datetime.utcnow() - start).total_seconds() * 1000
        )

    except Exception as e:
        logger.error(f"path_legacy_fallback failed: {e}")
        return PathResult(
            path_name="legacy_fallback",
            results=[],
            latency_ms=(datetime.utcnow() - start).total_seconds() * 1000,
            error=str(e)
        )


# =============================================================================
# Main Retrieval Function
# =============================================================================

async def execute_retrieval(
    query: str,
    domain: Optional[str] = None,
    include_legacy: bool = True,
    limit: int = 10
) -> RetrievalResult:
    """Execute multi-path retrieval with parallel execution.

    Args:
        query: The search query
        domain: Optional domain override (otherwise auto-classified)
        include_legacy: Whether to include legacy runbook search
        limit: Maximum final results

    Returns:
        RetrievalResult with merged, ranked results
    """
    start = datetime.utcnow()

    # Domain classification
    if domain:
        detected_domain = domain
        domain_confidence = 1.0
    else:
        detected_domain, domain_confidence = keyword_classify(query)

    logger.info(
        f"Retrieval: query='{query[:50]}...' "
        f"domain={detected_domain} confidence={domain_confidence:.2f}"
    )

    # Build task list for parallel execution
    tasks = [
        path_problem_vectors(query, detected_domain),
        path_document_content(query, detected_domain),
    ]

    # Only add graph traversal if domain confidence is high
    if detected_domain and domain_confidence >= GRAPH_DOMAIN_CONFIDENCE_THRESHOLD:
        tasks.append(path_graph_traversal(query, detected_domain))
        logger.info(f"Graph traversal enabled for domain '{detected_domain}'")

    # Optionally add legacy fallback
    if include_legacy:
        tasks.append(path_legacy_fallback(query))

    # Execute all paths in parallel with timeout
    try:
        path_results_list = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=PATH_TIMEOUT_MS / 1000 * len(tasks)  # Scale timeout by task count
        )
    except asyncio.TimeoutError:
        logger.warning("Retrieval timeout - returning partial results")
        path_results_list = []

    # Process results
    path_results = {}
    path_data = {}
    paths_executed = []

    for result in path_results_list:
        if isinstance(result, Exception):
            logger.error(f"Path failed with exception: {result}")
            continue

        if isinstance(result, PathResult):
            path_results[result.path_name] = result
            path_data[result.path_name] = result.results
            paths_executed.append(result.path_name)

    # Merge and rank results
    ranked_results = merge_and_rank(
        path_results=path_data,
        query_domain=detected_domain,
        limit=limit
    )

    total_latency = (datetime.utcnow() - start).total_seconds() * 1000

    return RetrievalResult(
        query=query,
        detected_domain=detected_domain,
        domain_confidence=domain_confidence,
        results=ranked_results,
        path_results=path_results,
        total_latency_ms=total_latency,
        paths_executed=paths_executed
    )


# =============================================================================
# Health Check
# =============================================================================

async def get_status() -> dict:
    """Health check for retrieval module."""
    return {"status": "healthy"}


# =============================================================================
# Tool Registration
# =============================================================================

def register_tools(mcp: FastMCP):
    """Register retrieval tools with the MCP server."""

    @mcp.tool()
    async def retrieve(
        query: str,
        domain: Optional[str] = None,
        limit: int = 10,
        include_legacy: bool = True,
        explain: bool = False
    ) -> dict:
        """Multi-path semantic retrieval for knowledge search.

        Executes parallel retrieval across:
        - Path 1: Problem Vectors (Qdrant knowledge_nodes)
        - Path 2: Document Content (Qdrant documents collection)
        - Path 3: Graph Traversal (Neo4j - if domain confidence > 0.8)
        - Path 4: Legacy Fallback (Qdrant runbooks - optional)

        Results are merged, deduplicated, and ranked using:
        - Path weights (graph > vectors > documents > legacy)
        - Freshness decay
        - Success rate bonus
        - Domain alignment bonus

        Args:
            query: Search query (e.g., "kubernetes pod crashloopbackoff")
            domain: Optional domain override (auto-detected if not provided)
            limit: Maximum results to return (default 10)
            include_legacy: Include legacy runbook search (default True)
            explain: Include score explanations for debugging (default False)

        Returns:
            Dict with:
            - results: Ranked list of matching items
            - detected_domain: Auto-detected domain (if any)
            - domain_confidence: Confidence in domain detection
            - paths_executed: Which retrieval paths ran
            - total_latency_ms: End-to-end latency
        """
        try:
            result = await execute_retrieval(
                query=query,
                domain=domain,
                include_legacy=include_legacy,
                limit=limit
            )

            # Build response
            response = {
                "query": result.query,
                "detected_domain": result.detected_domain,
                "domain_confidence": round(result.domain_confidence, 2),
                "paths_executed": result.paths_executed,
                "total_latency_ms": round(result.total_latency_ms, 1),
                "result_count": len(result.results),
                "results": [],
            }

            # Format results
            for r in result.results:
                item = {
                    "id": r.get("id") or r.get("neo4j_id") or r.get("runbook_id") or r.get("problem_id"),
                    "type": r.get("type"),
                    "title": r.get("title") or r.get("description", "")[:80],
                    "domain": r.get("domain"),
                    "score": round(r.get("_final_score", 0), 3),
                    "source": r.get("_source"),
                }

                # Add content preview if available
                if r.get("content"):
                    item["preview"] = r["content"][:200]
                elif r.get("description"):
                    item["preview"] = r["description"][:200]

                # Add success metrics if available
                if r.get("success_rate") is not None:
                    item["success_rate"] = r["success_rate"]
                if r.get("execution_count") is not None:
                    item["execution_count"] = r["execution_count"]

                # Add score explanation if requested
                if explain:
                    item["score_breakdown"] = r.get("_score_breakdown", {})

                response["results"].append(item)

            # Add path latencies
            response["path_latencies"] = {
                name: round(pr.latency_ms, 1)
                for name, pr in result.path_results.items()
            }

            # Add path errors if any
            path_errors = {
                name: pr.error
                for name, pr in result.path_results.items()
                if pr.error
            }
            if path_errors:
                response["path_errors"] = path_errors

            return response

        except Exception as e:
            logger.error(f"retrieve failed: {e}")
            return {"error": str(e)}

    @mcp.tool()
    async def retrieve_with_context(
        query: str,
        problem_id: Optional[str] = None,
        domain: Optional[str] = None,
        limit: int = 10
    ) -> dict:
        """Contextual retrieval starting from a known problem.

        If problem_id is provided, first gets solutions for that problem,
        then expands with related problems and general retrieval.

        Useful for:
        - Finding solutions when you know the problem ID
        - Expanding search from a known starting point
        - Getting related problems that might have solutions

        Args:
            query: Search query
            problem_id: Optional known problem ID to start from
            domain: Optional domain override
            limit: Maximum results

        Returns:
            Dict with results prioritizing contextual matches
        """
        try:
            results = []

            # If we have a problem ID, get its solutions first
            if problem_id:
                from knowledge_mcp.tools.neo4j import (
                    get_solutions_for_problem,
                    get_related_problems
                )

                # Get direct solutions
                solutions = await get_solutions_for_problem(problem_id)
                if solutions and not solutions[0].get("error"):
                    for s in solutions:
                        s["_source"] = "direct_solution"
                        s["score"] = s.get("confidence", 0.5)
                    results.extend(solutions)

                # Get related problems
                related = await get_related_problems(problem_id)
                if related and not related[0].get("error"):
                    for r in related:
                        r["_source"] = "related_problem"
                        r["score"] = 0.6  # Slightly lower weight
                    results.extend(related[:3])

            # Run standard retrieval
            retrieval = await execute_retrieval(
                query=query,
                domain=domain,
                include_legacy=True,
                limit=limit
            )

            # Combine: direct solutions first, then retrieval results
            all_results = results + retrieval.results

            # Deduplicate
            seen_ids = set()
            unique_results = []
            for r in all_results:
                rid = r.get("id") or r.get("neo4j_id") or r.get("runbook_id")
                if rid and rid not in seen_ids:
                    seen_ids.add(rid)
                    unique_results.append(r)

            return {
                "query": query,
                "context_problem_id": problem_id,
                "detected_domain": retrieval.detected_domain,
                "domain_confidence": round(retrieval.domain_confidence, 2),
                "result_count": len(unique_results[:limit]),
                "results": [
                    {
                        "id": r.get("id") or r.get("neo4j_id") or r.get("runbook_id"),
                        "type": r.get("type"),
                        "title": r.get("title") or r.get("description", "")[:80],
                        "source": r.get("_source"),
                        "score": round(r.get("score", 0) if isinstance(r.get("score"), (int, float)) else 0, 3),
                    }
                    for r in unique_results[:limit]
                ],
            }

        except Exception as e:
            logger.error(f"retrieve_with_context failed: {e}")
            return {"error": str(e)}

    @mcp.tool()
    async def classify_domain(query: str) -> dict:
        """Classify a query's domain using keyword matching.

        Useful for:
        - Understanding how queries are routed
        - Debugging domain detection
        - Testing classification before retrieval

        Args:
            query: Query to classify

        Returns:
            Dict with detected domain and confidence
        """
        domain, confidence = keyword_classify(query)

        # Also return matched keywords for transparency
        matched_keywords = []
        query_lower = query.lower()
        if domain:
            for kw in DOMAIN_KEYWORDS.get(domain, []):
                if kw in query_lower:
                    matched_keywords.append(kw)

        return {
            "query": query,
            "detected_domain": domain,
            "confidence": round(confidence, 2),
            "matched_keywords": matched_keywords,
            "graph_traversal_eligible": confidence >= GRAPH_DOMAIN_CONFIDENCE_THRESHOLD,
        }
