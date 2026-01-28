"""Neo4j knowledge graph tools for relationship queries.

Provides both MCP tools and shared internal functions used by REST endpoints.
"""

import os
import logging
from typing import Optional, List, Dict, Any

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
NEO4J_URL = os.environ.get("NEO4J_URL", "http://neo4j.ai-platform.svc:7474")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")

# Write operations blocked in read-only queries
_WRITE_KEYWORDS = ["CREATE", "MERGE", "DELETE", "SET", "REMOVE", "DROP"]


async def neo4j_query(cypher: str, params: dict = None) -> Dict[str, Any]:
    """Execute a Cypher query against Neo4j."""
    async with httpx.AsyncClient(timeout=30.0) as client:
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


def parse_results(data: dict) -> List[dict]:
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


def format_result_raw(data: dict) -> dict:
    """Format Neo4j response preserving columns and raw row data."""
    results = data.get("results", [])
    if not results:
        return {"columns": [], "data": []}
    first = results[0]
    columns = first.get("columns", [])
    rows = [row.get("row", []) for row in first.get("data", [])]
    return {"columns": columns, "data": rows}


async def get_status() -> dict:
    """Get Neo4j status for health checks."""
    try:
        result = await neo4j_query("RETURN 1 as health")
        return {"status": "healthy"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


# =============================================================================
# Shared internal functions (used by both MCP tools and REST endpoints)
# =============================================================================

async def _query_graph_impl(cypher: str) -> dict:
    """Execute read-only Cypher query. Returns raw format with columns and data."""
    cypher_upper = cypher.upper().strip()
    if any(kw in cypher_upper for kw in _WRITE_KEYWORDS):
        return {"error": "Only read-only queries allowed. Use MATCH, RETURN, WITH, etc."}
    result = await neo4j_query(cypher)
    if result.get("errors"):
        return {"error": str(result["errors"])}
    return format_result_raw(result)


async def _get_entity_context_impl(entity_id: str, entity_type: str = "Host") -> dict:
    """Get entity with all relationships, using coalesce for robust identification."""
    cypher = f"""
    MATCH (e:{entity_type})
    WHERE e.ip = $id OR e.hostname = $id OR e.mac = $id
       OR e.name = $id OR e.vmid = $id OR e.title = $id
    WITH e LIMIT 1
    OPTIONAL MATCH (e)-[r]->(related)
    OPTIONAL MATCH (e)<-[r2]-(related2)
    RETURN e,
      collect(DISTINCT {{
        type: type(r),
        target: coalesce(related.name, related.ip, related.hostname, related.title, toString(related.vmid)),
        target_type: labels(related)[0]
      }}) as out,
      collect(DISTINCT {{
        type: type(r2),
        source: coalesce(related2.name, related2.ip, related2.hostname, related2.title, toString(related2.vmid)),
        source_type: labels(related2)[0]
      }}) as inc
    """
    result = await neo4j_query(cypher, {"id": entity_id})
    if result.get("errors") or not result.get("results", [{}])[0].get("data"):
        return {"id": entity_id, "type": entity_type, "found": False}
    data = result["results"][0]["data"][0]["row"]
    props = data[0] or {}
    out_rels = [r for r in data[1] if r.get("target")]
    in_rels = [{"direction": "incoming", **r} for r in data[2] if r.get("source")]
    return {
        "id": entity_id,
        "type": entity_type,
        "found": True,
        "properties": props,
        "relationships": out_rels + in_rels,
    }


async def _get_infrastructure_overview_impl() -> dict:
    """High-level infrastructure overview with entity counts and network summary."""
    cypher = """
    MATCH (n)
    WITH labels(n)[0] as label, count(n) as count
    RETURN collect({type: label, count: count}) as entities
    """
    result = await neo4j_query(cypher)
    records = parse_results(result)

    network_cypher = """
    MATCH (n:Network)
    OPTIONAL MATCH (h)-[:CONNECTED_TO]->(n)
    RETURN n.name as network, n.cidr as cidr, count(h) as host_count
    """
    network_result = await neo4j_query(network_cypher)
    networks = parse_results(network_result)

    return {
        "entities": records[0].get("entities", []) if records else [],
        "networks": networks,
    }


async def _neo4j_write(cypher: str) -> dict:
    """Execute a write Cypher query (for enrichment jobs)."""
    result = await neo4j_query(cypher)
    if result.get("errors"):
        return {"error": str(result["errors"])}
    stats = result.get("results", [{}])[0].get("stats", {})
    return {"status": "ok", "stats": stats}


def register_tools(mcp: FastMCP):
    """Register Neo4j tools with the MCP server."""

    @mcp.tool()
    async def query_graph(cypher: str) -> List[dict]:
        """Execute read-only Cypher query. Examples: 'MATCH (h:Host) RETURN h.ip LIMIT 10'."""
        try:
            result = await _query_graph_impl(cypher)
            if "error" in result:
                return [result]
            return parse_results(await neo4j_query(cypher))
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def get_entity_context(entity_id: str, entity_type: str = "Host") -> dict:
        """Get entity with relationships. entity_id: IP, hostname, MAC, or name."""
        try:
            return await _get_entity_context_impl(entity_id, entity_type)
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def find_dependencies(service_name: str, depth: int = 2) -> dict:
        """Find upstream/downstream dependencies for a service."""
        try:
            cypher = """
            MATCH (s {name: $name})
            OPTIONAL MATCH upstream_path = (u)-[*1..$depth]->(s)
            OPTIONAL MATCH downstream_path = (s)-[*1..$depth]->(d)
            WITH s,
                 collect(DISTINCT {path: [n in nodes(upstream_path) | n.name], rels: [r in relationships(upstream_path) | type(r)]}) as upstream,
                 collect(DISTINCT {path: [n in nodes(downstream_path) | n.name], rels: [r in relationships(downstream_path) | type(r)]}) as downstream
            RETURN s.name as service, upstream, downstream
            """
            result = await neo4j_query(cypher.replace("$depth", str(min(depth, 5))), {"name": service_name})
            records = parse_results(result)
            if records:
                return records[0]
            return {"error": "Service not found"}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def get_impact_analysis(entity_type: str, entity_id: str) -> dict:
        """What breaks if entity fails? Returns affected services and severity."""
        try:
            cypher = """
            MATCH (n)
            WHERE n.ip = $id OR n.hostname = $id OR n.name = $id
            OPTIONAL MATCH (n)<-[*1..3]-(dependent)
            WHERE dependent:Service OR dependent:Application
            WITH n, collect(DISTINCT {name: dependent.name, type: labels(dependent)[0]}) as dependents
            RETURN n.name as entity, dependents,
                   CASE
                     WHEN size(dependents) > 10 THEN 'critical'
                     WHEN size(dependents) > 5 THEN 'high'
                     WHEN size(dependents) > 0 THEN 'medium'
                     ELSE 'low'
                   END as severity
            """
            result = await neo4j_query(cypher, {"id": entity_id})
            records = parse_results(result)
            if records:
                return records[0]
            return {"error": "Entity not found"}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def find_path(from_entity: str, to_entity: str, max_depth: int = 5) -> dict:
        """Find connection path between two entities."""
        try:
            cypher = """
            MATCH (a), (b)
            WHERE (a.ip = $from OR a.hostname = $from OR a.name = $from)
              AND (b.ip = $to OR b.hostname = $to OR b.name = $to)
            MATCH p = shortestPath((a)-[*1..$depth]-(b))
            RETURN [n in nodes(p) | n.name] as path,
                   [r in relationships(p) | type(r)] as relationships,
                   length(p) as hops
            """
            result = await neo4j_query(cypher.replace("$depth", str(min(max_depth, 10))), {
                "from": from_entity,
                "to": to_entity
            })
            records = parse_results(result)
            if records:
                return records[0]
            return {"error": "No path found between entities"}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def get_runbook_for_alert(alert_name: str) -> List[dict]:
        """Find runbooks that resolve an alert."""
        try:
            cypher = """
            MATCH (a:Alert {name: $name})-[:RESOLVED_BY]->(r:Runbook)
            RETURN r.name as runbook, r.path as path, r.automation_level as automation_level
            """
            result = await neo4j_query(cypher, {"name": alert_name})
            records = parse_results(result)
            if records:
                return records
            return [{"message": "No linked runbooks found for this alert"}]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def get_infrastructure_overview() -> dict:
        """High-level infrastructure overview."""
        try:
            return await _get_infrastructure_overview_impl()
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def get_hosts_on_network(network: str) -> List[dict]:
        """Get hosts connected to a network (prod, agentic, monitoring)."""
        try:
            cypher = """
            MATCH (h:Host)-[:CONNECTED_TO]->(n:Network)
            WHERE n.name = $network OR n.cidr CONTAINS $network
            RETURN h.hostname as hostname, h.ip as ip, h.type as type, h.status as status
            ORDER BY h.ip
            """
            result = await neo4j_query(cypher, {"network": network})
            return parse_results(result)
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def get_services_on_host(host_id: str) -> List[dict]:
        """Get services/pods running on a host."""
        try:
            cypher = """
            MATCH (s)-[:RUNS_ON]->(h:Host)
            WHERE h.ip = $id OR h.hostname = $id OR h.name = $id
            RETURN s.name as service, labels(s)[0] as type, s.namespace as namespace, s.status as status
            """
            result = await neo4j_query(cypher, {"id": host_id})
            return parse_results(result)
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def find_orphan_entities(entity_type: Optional[str] = None) -> List[dict]:
        """Find entities with no relationships (potentially stale)."""
        try:
            if entity_type:
                cypher = f"""
                MATCH (n:{entity_type})
                WHERE NOT (n)--()
                RETURN n.name as name, n.ip as ip, labels(n)[0] as type
                LIMIT 50
                """
            else:
                cypher = """
                MATCH (n)
                WHERE NOT (n)--()
                RETURN n.name as name, n.ip as ip, labels(n)[0] as type
                LIMIT 50
                """
            result = await neo4j_query(cypher)
            return parse_results(result)
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def get_stale_entities(hours: int = 24) -> List[dict]:
        """Find entities not seen in specified hours."""
        try:
            cypher = """
            MATCH (n)
            WHERE n.last_seen IS NOT NULL
              AND datetime(n.last_seen) < datetime() - duration({hours: $hours})
            RETURN n.name as name, n.ip as ip, labels(n)[0] as type, n.last_seen as last_seen
            ORDER BY n.last_seen ASC
            LIMIT 50
            """
            result = await neo4j_query(cypher, {"hours": hours})
            return parse_results(result)
        except Exception as e:
            return [{"error": str(e)}]

    # =========================================================================
    # Project 02: Graph Retrieval for Multi-Path Search
    # =========================================================================

    @mcp.tool()
    async def get_solutions_for_problem(
        problem_id: str,
        min_confidence: float = 0.0,
        time_decay_half_life_days: float = 30.0
    ) -> List[dict]:
        """Get solutions linked to a Problem with time-decayed confidence.

        Traverses Problem -> SOLVES <- Runbook relationships.
        Returns solutions ordered by confidence = success_rate * time_decay.

        Args:
            problem_id: The Problem node ID
            min_confidence: Minimum confidence threshold (0.0-1.0)
            time_decay_half_life_days: Half-life for time decay calculation

        Returns:
            List of solutions with confidence scores
        """
        try:
            cypher = """
            MATCH (p:Problem {id: $problem_id})<-[:SOLVES]-(r:Runbook)
            WITH r,
                 coalesce(r.success_rate, 0.5) AS success_rate,
                 coalesce(r.execution_count, 0) AS exec_count,
                 r.last_executed AS last_used,
                 CASE
                   WHEN r.last_executed IS NOT NULL THEN
                     duration.inDays(datetime(r.last_executed), datetime()).days
                   ELSE 90
                 END AS days_stale
            WITH r, success_rate, exec_count, last_used, days_stale,
                 success_rate * (2.0 ^ (-1.0 * days_stale / $half_life)) AS confidence
            WHERE confidence >= $min_confidence
            RETURN r.id AS runbook_id,
                   r.title AS title,
                   r.path AS path,
                   r.automation_level AS automation_level,
                   success_rate,
                   exec_count AS execution_count,
                   last_used,
                   days_stale,
                   confidence
            ORDER BY confidence DESC
            LIMIT 10
            """
            result = await neo4j_query(cypher, {
                "problem_id": problem_id,
                "min_confidence": min_confidence,
                "half_life": time_decay_half_life_days
            })

            if result.get("errors"):
                return [{"error": str(result["errors"])}]

            return parse_results(result)
        except Exception as e:
            logger.error(f"get_solutions_for_problem failed: {e}")
            return [{"error": str(e)}]

    @mcp.tool()
    async def get_proven_runbooks(
        domain: str,
        min_success_rate: float = 0.7,
        min_executions: int = 3,
        limit: int = 10
    ) -> List[dict]:
        """Get runbooks with high success rates in a domain.

        Used by Path 3 (Graph Traversal) when domain confidence > 0.8.
        Returns runbooks proven effective through execution history.

        Args:
            domain: Domain to filter by (infra, security, obs, dns, network, data)
            min_success_rate: Minimum success rate threshold (0.0-1.0)
            min_executions: Minimum execution count for statistical confidence
            limit: Maximum results to return

        Returns:
            List of proven runbooks with success metrics
        """
        try:
            cypher = """
            MATCH (r:Runbook)
            WHERE r.domain = $domain
              AND coalesce(r.success_rate, 0) >= $min_success_rate
              AND coalesce(r.execution_count, 0) >= $min_executions
            WITH r,
                 r.success_rate AS success_rate,
                 r.execution_count AS execution_count,
                 CASE
                   WHEN r.last_executed IS NOT NULL THEN
                     duration.inDays(datetime(r.last_executed), datetime()).days
                   ELSE 90
                 END AS days_stale
            OPTIONAL MATCH (r)-[:SOLVES]->(p:Problem)
            WITH r, success_rate, execution_count, days_stale,
                 collect(p.description)[0..3] AS related_problems
            RETURN r.id AS runbook_id,
                   r.title AS title,
                   r.path AS path,
                   r.automation_level AS automation_level,
                   r.domain AS domain,
                   success_rate,
                   execution_count,
                   days_stale,
                   related_problems
            ORDER BY success_rate DESC, execution_count DESC
            LIMIT $limit
            """
            result = await neo4j_query(cypher, {
                "domain": domain,
                "min_success_rate": min_success_rate,
                "min_executions": min_executions,
                "limit": limit
            })

            if result.get("errors"):
                return [{"error": str(result["errors"])}]

            return parse_results(result)
        except Exception as e:
            logger.error(f"get_proven_runbooks failed: {e}")
            return [{"error": str(e)}]

    @mcp.tool()
    async def get_problems_by_domain(
        domain: str,
        limit: int = 20
    ) -> List[dict]:
        """Get Problems in a domain for graph traversal path.

        Args:
            domain: Domain to filter by
            limit: Maximum results

        Returns:
            List of problems with their linked runbooks
        """
        try:
            cypher = """
            MATCH (p:Problem)
            WHERE p.domain = $domain
            OPTIONAL MATCH (p)<-[:SOLVES]-(r:Runbook)
            WITH p, collect({
                runbook_id: r.id,
                title: r.title,
                success_rate: r.success_rate
            }) AS solutions
            RETURN p.id AS problem_id,
                   p.description AS description,
                   p.domain AS domain,
                   p.tags AS tags,
                   p.weight AS weight,
                   p.last_referenced AS last_referenced,
                   solutions
            ORDER BY p.weight DESC, p.last_referenced DESC
            LIMIT $limit
            """
            result = await neo4j_query(cypher, {
                "domain": domain,
                "limit": limit
            })

            if result.get("errors"):
                return [{"error": str(result["errors"])}]

            return parse_results(result)
        except Exception as e:
            logger.error(f"get_problems_by_domain failed: {e}")
            return [{"error": str(e)}]

    @mcp.tool()
    async def get_related_problems(
        problem_id: str,
        depth: int = 2
    ) -> List[dict]:
        """Find problems related to a given problem through shared solutions.

        Traverses: Problem <- SOLVES - Runbook - SOLVES -> OtherProblem

        Args:
            problem_id: Starting problem ID
            depth: Max relationship depth (1-3)

        Returns:
            List of related problems with relationship paths
        """
        try:
            depth = min(max(depth, 1), 3)  # Clamp to 1-3

            cypher = """
            MATCH (p:Problem {id: $problem_id})
            MATCH path = (p)<-[:SOLVES]-(r:Runbook)-[:SOLVES]->(other:Problem)
            WHERE other.id <> p.id
            WITH other, r, length(path) AS hops
            RETURN DISTINCT other.id AS problem_id,
                   other.description AS description,
                   other.domain AS domain,
                   r.title AS via_runbook,
                   hops
            ORDER BY hops ASC, other.weight DESC
            LIMIT 10
            """
            result = await neo4j_query(cypher, {"problem_id": problem_id})

            if result.get("errors"):
                return [{"error": str(result["errors"])}]

            return parse_results(result)
        except Exception as e:
            logger.error(f"get_related_problems failed: {e}")
            return [{"error": str(e)}]
