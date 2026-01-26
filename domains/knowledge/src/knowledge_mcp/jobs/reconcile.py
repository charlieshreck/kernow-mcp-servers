"""Reconciliation job for Qdrant↔Neo4j dual-index consistency.

Detects and repairs drift between the two stores:
1. Orphaned Qdrant entries (no corresponding Neo4j node)
2. Missing Qdrant entries (Neo4j node not indexed)
3. Content hash mismatches (stale embeddings)

Designed to run as a weekly Kubernetes CronJob.
"""

import logging
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Any, Set, Optional

import httpx

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration (same as qdrant.py)
# =============================================================================

import os

QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant.ai-platform.svc:6333")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama.ai-platform.svc:11434")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "nomic-embed-text")
NEO4J_URL = os.environ.get("NEO4J_URL", "http://neo4j.ai-platform.svc:7474")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")


# =============================================================================
# Result Types
# =============================================================================

@dataclass
class ReconcileResult:
    """Result of a reconciliation run."""
    started_at: str
    completed_at: str = ""
    duration_seconds: float = 0.0

    # Counts
    qdrant_total: int = 0
    neo4j_problems: int = 0
    neo4j_runbooks: int = 0

    # Issues found
    orphaned_qdrant: List[str] = field(default_factory=list)
    missing_qdrant: List[Dict[str, str]] = field(default_factory=list)
    hash_mismatches: List[Dict[str, str]] = field(default_factory=list)

    # Actions taken
    deleted_orphans: int = 0
    reindexed_missing: int = 0
    reindexed_stale: int = 0

    # Errors
    errors: List[str] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return bool(self.orphaned_qdrant or self.missing_qdrant or self.hash_mismatches)


# =============================================================================
# HTTP Helpers
# =============================================================================

async def qdrant_request(endpoint: str, method: str = "GET", data: dict = None) -> Dict[str, Any]:
    """Make request to Qdrant API."""
    headers = {}
    if QDRANT_API_KEY:
        headers["api-key"] = QDRANT_API_KEY
    async with httpx.AsyncClient(timeout=60.0) as client:
        url = f"{QDRANT_URL}{endpoint}"
        if method == "GET":
            response = await client.get(url, headers=headers)
        elif method == "POST":
            response = await client.post(url, headers=headers, json=data)
        elif method == "PUT":
            response = await client.put(url, headers=headers, json=data)
        elif method == "DELETE":
            response = await client.delete(url, headers=headers)
        response.raise_for_status()
        return response.json()


async def neo4j_query(cypher: str, params: dict = None) -> Dict[str, Any]:
    """Execute a Cypher query against Neo4j."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{NEO4J_URL}/db/neo4j/tx/commit",
            auth=(NEO4J_USER, NEO4J_PASSWORD),
            json={
                "statements": [{
                    "statement": cypher,
                    "parameters": params or {}
                }]
            }
        )
        response.raise_for_status()
        return response.json()


def parse_neo4j_results(data: dict) -> List[dict]:
    """Parse Neo4j response into simple dict format."""
    results = []
    for result in data.get("results", []):
        columns = result.get("columns", [])
        for row in result.get("data", []):
            record = {}
            for i, col in enumerate(columns):
                val = row.get("row", [])[i] if i < len(row.get("row", [])) else None
                record[col] = val
            results.append(record)
    return results


async def get_embedding(text: str) -> List[float]:
    """Generate embedding using Ollama."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": EMBEDDING_MODEL, "prompt": text}
        )
        response.raise_for_status()
        return response.json().get("embedding", [])


# =============================================================================
# Data Retrieval Functions
# =============================================================================

async def scroll_all_qdrant_ids(collection: str = "knowledge_nodes") -> Dict[str, Dict[str, Any]]:
    """Scroll through all points in Qdrant collection and return IDs with metadata.

    Returns:
        Dict mapping point_id -> {type, neo4j_id, content_hash}
    """
    all_points = {}
    offset = None
    batch_size = 100

    while True:
        body = {
            "limit": batch_size,
            "with_payload": ["type", "neo4j_id", "content_hash"],
        }
        if offset:
            body["offset"] = offset

        result = await qdrant_request(
            f"/collections/{collection}/points/scroll",
            "POST",
            body
        )

        points = result.get("result", {}).get("points", [])
        if not points:
            break

        for p in points:
            point_id = p.get("id")
            payload = p.get("payload", {})
            all_points[point_id] = {
                "type": payload.get("type"),
                "neo4j_id": payload.get("neo4j_id"),
                "content_hash": payload.get("content_hash"),
            }

        # Check for next page
        next_offset = result.get("result", {}).get("next_page_offset")
        if not next_offset:
            break
        offset = next_offset

    return all_points


async def get_all_neo4j_ids() -> Dict[str, Dict[str, Any]]:
    """Get all Problem and Runbook IDs from Neo4j with their content hashes.

    Returns:
        Dict mapping node_id -> {type, content_hash, description/title}
    """
    all_nodes = {}

    # Get Problems
    problems_result = await neo4j_query("""
        MATCH (p:Problem)
        RETURN p.id as id, 'problem' as type,
               p.content_hash as content_hash,
               p.description as content
    """)

    for p in parse_neo4j_results(problems_result):
        node_id = p.get("id")
        if node_id:
            all_nodes[node_id] = {
                "type": "problem",
                "content_hash": p.get("content_hash"),
                "content": p.get("content"),
            }

    # Get Runbooks
    runbooks_result = await neo4j_query("""
        MATCH (r:Runbook)
        RETURN r.id as id, 'runbook' as type,
               r.content_hash as content_hash,
               r.title as title,
               r.description as description,
               r.solution as solution
    """)

    for r in parse_neo4j_results(runbooks_result):
        node_id = r.get("id")
        if node_id:
            # Reconstruct embeddable content for hash comparison
            content = f"{r.get('title', '')}\n{r.get('description', '')}"
            if r.get("solution"):
                content += f"\n{r.get('solution')}"

            all_nodes[node_id] = {
                "type": "runbook",
                "content_hash": r.get("content_hash"),
                "content": content,
            }

    return all_nodes


# =============================================================================
# Reconciliation Actions
# =============================================================================

async def delete_orphaned_qdrant_points(
    point_ids: List[str],
    collection: str = "knowledge_nodes"
) -> int:
    """Delete orphaned points from Qdrant.

    Returns:
        Number of points deleted
    """
    if not point_ids:
        return 0

    try:
        await qdrant_request(
            f"/collections/{collection}/points/delete",
            "POST",
            {"points": point_ids}
        )
        logger.info(f"Deleted {len(point_ids)} orphaned Qdrant points")
        return len(point_ids)
    except Exception as e:
        logger.error(f"Failed to delete orphaned points: {e}")
        return 0


async def reindex_missing_nodes(
    missing_nodes: List[Dict[str, Any]],
    collection: str = "knowledge_nodes"
) -> int:
    """Re-index Neo4j nodes missing from Qdrant.

    Returns:
        Number of nodes re-indexed
    """
    reindexed = 0

    for node in missing_nodes:
        node_id = node.get("id")
        node_type = node.get("type")
        content = node.get("content", "")

        try:
            # Generate embedding
            embedding = await get_embedding(content[:10000])

            # Compute content hash
            content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

            # Index in Qdrant
            await qdrant_request(f"/collections/{collection}/points", "PUT", {
                "points": [{
                    "id": node_id,
                    "vector": embedding,
                    "payload": {
                        "type": node_type,
                        "neo4j_id": node_id,
                        "content_hash": content_hash,
                        "indexed_at": datetime.utcnow().isoformat(),
                        "source": "reconcile",
                    }
                }]
            })

            reindexed += 1
            logger.info(f"Re-indexed missing {node_type}: {node_id}")

        except Exception as e:
            logger.error(f"Failed to re-index {node_id}: {e}")

    return reindexed


async def reindex_stale_nodes(
    stale_nodes: List[Dict[str, Any]],
    collection: str = "knowledge_nodes"
) -> int:
    """Re-index nodes with stale embeddings (content_hash mismatch).

    Returns:
        Number of nodes re-indexed
    """
    reindexed = 0

    for node in stale_nodes:
        node_id = node.get("id")
        node_type = node.get("type")
        content = node.get("content", "")
        new_hash = node.get("new_hash")

        try:
            # Generate new embedding
            embedding = await get_embedding(content[:10000])

            # Get existing payload
            try:
                existing = await qdrant_request(
                    f"/collections/{collection}/points/{node_id}"
                )
                old_payload = existing.get("result", {}).get("payload", {})
            except:
                old_payload = {}

            # Update in Qdrant
            await qdrant_request(f"/collections/{collection}/points", "PUT", {
                "points": [{
                    "id": node_id,
                    "vector": embedding,
                    "payload": {
                        **old_payload,
                        "content_hash": new_hash,
                        "indexed_at": datetime.utcnow().isoformat(),
                        "reindexed_by": "reconcile",
                    }
                }]
            })

            reindexed += 1
            logger.info(f"Re-indexed stale {node_type}: {node_id}")

        except Exception as e:
            logger.error(f"Failed to re-index stale {node_id}: {e}")

    return reindexed


# =============================================================================
# Main Reconciliation Function
# =============================================================================

async def reconcile_dual_index(
    dry_run: bool = False,
    fix_orphans: bool = True,
    fix_missing: bool = True,
    fix_stale: bool = True
) -> ReconcileResult:
    """Run full reconciliation between Qdrant and Neo4j.

    Detects and optionally fixes:
    1. Orphaned Qdrant entries (in Qdrant but not Neo4j)
    2. Missing Qdrant entries (in Neo4j but not Qdrant)
    3. Content hash mismatches (embedding out of date)

    Args:
        dry_run: If True, only detect issues without fixing
        fix_orphans: Delete orphaned Qdrant entries
        fix_missing: Re-index missing Neo4j nodes
        fix_stale: Re-index nodes with stale embeddings

    Returns:
        ReconcileResult with full report
    """
    result = ReconcileResult(started_at=datetime.utcnow().isoformat())
    start_time = datetime.utcnow()

    logger.info(f"Starting reconciliation (dry_run={dry_run})")

    try:
        # Step 1: Get all Qdrant IDs
        logger.info("Fetching Qdrant knowledge_nodes...")
        qdrant_data = await scroll_all_qdrant_ids()
        result.qdrant_total = len(qdrant_data)
        qdrant_ids = set(qdrant_data.keys())
        logger.info(f"Found {result.qdrant_total} Qdrant points")

        # Step 2: Get all Neo4j IDs
        logger.info("Fetching Neo4j Problems and Runbooks...")
        neo4j_data = await get_all_neo4j_ids()
        neo4j_ids = set(neo4j_data.keys())

        result.neo4j_problems = sum(1 for n in neo4j_data.values() if n.get("type") == "problem")
        result.neo4j_runbooks = sum(1 for n in neo4j_data.values() if n.get("type") == "runbook")
        logger.info(f"Found {result.neo4j_problems} Problems, {result.neo4j_runbooks} Runbooks in Neo4j")

        # Step 3: Find orphaned Qdrant entries
        orphaned_ids = qdrant_ids - neo4j_ids
        result.orphaned_qdrant = list(orphaned_ids)
        if orphaned_ids:
            logger.warning(f"Found {len(orphaned_ids)} orphaned Qdrant entries")

        # Step 4: Find missing Qdrant entries
        missing_ids = neo4j_ids - qdrant_ids
        for node_id in missing_ids:
            node = neo4j_data[node_id]
            result.missing_qdrant.append({
                "id": node_id,
                "type": node.get("type"),
            })
        if missing_ids:
            logger.warning(f"Found {len(missing_ids)} Neo4j nodes missing from Qdrant")

        # Step 5: Find content hash mismatches
        common_ids = qdrant_ids & neo4j_ids
        for node_id in common_ids:
            qdrant_hash = qdrant_data[node_id].get("content_hash")
            neo4j_hash = neo4j_data[node_id].get("content_hash")

            # Recompute hash from content if Neo4j hash is missing
            if not neo4j_hash:
                content = neo4j_data[node_id].get("content", "")
                neo4j_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

            if qdrant_hash != neo4j_hash:
                result.hash_mismatches.append({
                    "id": node_id,
                    "type": neo4j_data[node_id].get("type"),
                    "qdrant_hash": qdrant_hash,
                    "neo4j_hash": neo4j_hash,
                    "content": neo4j_data[node_id].get("content"),
                    "new_hash": neo4j_hash,
                })

        if result.hash_mismatches:
            logger.warning(f"Found {len(result.hash_mismatches)} content hash mismatches")

        # Step 6: Apply fixes (unless dry_run)
        if not dry_run:
            # Fix orphans
            if fix_orphans and result.orphaned_qdrant:
                result.deleted_orphans = await delete_orphaned_qdrant_points(
                    result.orphaned_qdrant
                )

            # Fix missing
            if fix_missing and result.missing_qdrant:
                missing_nodes = [
                    {
                        "id": m["id"],
                        "type": m["type"],
                        "content": neo4j_data[m["id"]].get("content", ""),
                    }
                    for m in result.missing_qdrant
                ]
                result.reindexed_missing = await reindex_missing_nodes(missing_nodes)

            # Fix stale
            if fix_stale and result.hash_mismatches:
                result.reindexed_stale = await reindex_stale_nodes(result.hash_mismatches)

    except Exception as e:
        logger.error(f"Reconciliation failed: {e}")
        result.errors.append(str(e))

    # Complete timing
    end_time = datetime.utcnow()
    result.completed_at = end_time.isoformat()
    result.duration_seconds = (end_time - start_time).total_seconds()

    logger.info(
        f"Reconciliation complete in {result.duration_seconds:.1f}s: "
        f"orphans={len(result.orphaned_qdrant)}, "
        f"missing={len(result.missing_qdrant)}, "
        f"stale={len(result.hash_mismatches)}"
    )

    return result


# =============================================================================
# CLI Entry Point (for CronJob)
# =============================================================================

async def main():
    """CLI entry point for running reconciliation."""
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Reconcile Qdrant↔Neo4j dual-index")
    parser.add_argument("--dry-run", action="store_true", help="Detect issues without fixing")
    parser.add_argument("--no-fix-orphans", action="store_true", help="Skip deleting orphans")
    parser.add_argument("--no-fix-missing", action="store_true", help="Skip re-indexing missing")
    parser.add_argument("--no-fix-stale", action="store_true", help="Skip re-indexing stale")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    result = await reconcile_dual_index(
        dry_run=args.dry_run,
        fix_orphans=not args.no_fix_orphans,
        fix_missing=not args.no_fix_missing,
        fix_stale=not args.no_fix_stale
    )

    if args.json:
        output = {
            "started_at": result.started_at,
            "completed_at": result.completed_at,
            "duration_seconds": result.duration_seconds,
            "qdrant_total": result.qdrant_total,
            "neo4j_problems": result.neo4j_problems,
            "neo4j_runbooks": result.neo4j_runbooks,
            "orphaned_count": len(result.orphaned_qdrant),
            "missing_count": len(result.missing_qdrant),
            "stale_count": len(result.hash_mismatches),
            "deleted_orphans": result.deleted_orphans,
            "reindexed_missing": result.reindexed_missing,
            "reindexed_stale": result.reindexed_stale,
            "errors": result.errors,
        }
        print(json.dumps(output, indent=2))
    else:
        print(f"\nReconciliation Report")
        print(f"=" * 50)
        print(f"Started: {result.started_at}")
        print(f"Duration: {result.duration_seconds:.1f}s")
        print()
        print(f"Qdrant knowledge_nodes: {result.qdrant_total}")
        print(f"Neo4j Problems: {result.neo4j_problems}")
        print(f"Neo4j Runbooks: {result.neo4j_runbooks}")
        print()
        print(f"Issues Found:")
        print(f"  Orphaned Qdrant entries: {len(result.orphaned_qdrant)}")
        print(f"  Missing Qdrant entries: {len(result.missing_qdrant)}")
        print(f"  Stale embeddings: {len(result.hash_mismatches)}")
        print()
        print(f"Actions Taken:")
        print(f"  Deleted orphans: {result.deleted_orphans}")
        print(f"  Re-indexed missing: {result.reindexed_missing}")
        print(f"  Re-indexed stale: {result.reindexed_stale}")

        if result.errors:
            print()
            print(f"Errors:")
            for e in result.errors:
                print(f"  - {e}")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
