"""Qdrant vector database tools for semantic search."""

import os
import logging
from typing import Optional, List, Dict, Any
from uuid import uuid4
from datetime import datetime

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant.ai-platform.svc:6333")
QDRANT_API_KEY = os.environ.get("QDRANT_API_KEY", "")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama.ai-platform.svc:11434")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "nomic-embed-text")


async def get_embedding(text: str) -> List[float]:
    """Generate embedding using Ollama."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": EMBEDDING_MODEL, "prompt": text}
        )
        response.raise_for_status()
        return response.json().get("embedding", [])


async def qdrant_request(endpoint: str, method: str = "GET", data: dict = None) -> Dict[str, Any]:
    """Make request to Qdrant API."""
    headers = {}
    if QDRANT_API_KEY:
        headers["api-key"] = QDRANT_API_KEY
    async with httpx.AsyncClient(timeout=30.0) as client:
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


async def search_collection(collection: str, query: str, limit: int = 5, min_score: float = 0.5) -> List[Dict]:
    """Search a collection with semantic query."""
    try:
        embedding = await get_embedding(query)
        result = await qdrant_request(f"/collections/{collection}/points/search", "POST", {
            "vector": embedding,
            "limit": limit,
            "with_payload": True,
            "score_threshold": min_score
        })
        return result.get("result", [])
    except Exception as e:
        logger.error(f"Search failed: {e}")
        return []


async def get_status() -> dict:
    """Get Qdrant status for health checks."""
    try:
        result = await qdrant_request("/collections")
        collections = result.get("result", {}).get("collections", [])
        return {"status": "healthy", "collections": len(collections)}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


# Neo4j helpers for dual-indexing (Project 02)
NEO4J_URL = os.environ.get("NEO4J_URL", "http://neo4j.ai-platform.svc:7474")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")


async def _neo4j_query(cypher: str, params: dict = None) -> Dict[str, Any]:
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


def _parse_neo4j_results(data: dict) -> List[dict]:
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


def register_tools(mcp: FastMCP):
    """Register Qdrant tools with the MCP server."""

    @mcp.tool()
    async def qdrant_list_collections() -> List[dict]:
        """List all available Qdrant vector collections with point counts."""
        try:
            result = await qdrant_request("/collections")
            collections = []
            for c in result.get("result", {}).get("collections", []):
                name = c.get("name")
                info = await qdrant_request(f"/collections/{name}")
                count = info.get("result", {}).get("points_count", 0)
                collections.append({"name": name, "points": count})
            return collections
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def get_collection_info(collection: str) -> dict:
        """Get detailed info about a collection."""
        try:
            result = await qdrant_request(f"/collections/{collection}")
            return result.get("result", {})
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def search_runbooks(query: str, limit: int = 5, min_score: float = 0.6) -> List[dict]:
        """Search runbooks for solutions to issues. Returns title, solution, and path."""
        try:
            results = await search_collection("runbooks", query, limit, min_score)
            return [{
                "id": r.get("id"),
                "score": r.get("score"),
                "title": r.get("payload", {}).get("title"),
                "trigger_pattern": r.get("payload", {}).get("trigger_pattern"),
                "solution": r.get("payload", {}).get("solution", "")[:500],
                "automation_level": r.get("payload", {}).get("automation_level", "manual"),
                "path": r.get("payload", {}).get("path")
            } for r in results]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def get_runbook(runbook_id: str) -> dict:
        """Get full runbook content by ID."""
        try:
            result = await qdrant_request(f"/collections/runbooks/points/{runbook_id}")
            point = result.get("result", {})
            return point.get("payload", {})
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def add_runbook(title: str, trigger_pattern: str, solution: str, path: Optional[str] = None) -> dict:
        """Add a new runbook to the knowledge base."""
        try:
            embedding = await get_embedding(f"{title} {trigger_pattern} {solution}")
            point_id = str(uuid4())
            await qdrant_request("/collections/runbooks/points", "PUT", {
                "points": [{
                    "id": point_id,
                    "vector": embedding,
                    "payload": {
                        "title": title,
                        "trigger_pattern": trigger_pattern,
                        "solution": solution,
                        "path": path,
                        "automation_level": "manual",
                        "created_at": datetime.utcnow().isoformat(),
                        "execution_count": 0,
                        "success_count": 0
                    }
                }]
            })
            return {"success": True, "id": point_id}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def list_runbooks(limit: int = 50) -> List[dict]:
        """List all runbooks (titles and IDs)."""
        try:
            result = await qdrant_request("/collections/runbooks/points/scroll", "POST", {
                "limit": limit,
                "with_payload": True
            })
            return [{
                "id": p.get("id"),
                "title": p.get("payload", {}).get("title"),
                "automation_level": p.get("payload", {}).get("automation_level")
            } for p in result.get("result", {}).get("points", [])]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def update_runbook(
        runbook_id: str,
        automation_level: Optional[str] = None,
        success_rate: Optional[float] = None,
        execution_count: Optional[int] = None,
        success_count: Optional[int] = None
    ) -> dict:
        """Update runbook metadata including autonomy level and execution stats."""
        try:
            updates = {}
            if automation_level:
                updates["automation_level"] = automation_level
            if success_rate is not None:
                updates["success_rate"] = success_rate
            if execution_count is not None:
                updates["execution_count"] = execution_count
            if success_count is not None:
                updates["success_count"] = success_count

            await qdrant_request(f"/collections/runbooks/points/{runbook_id}", "PUT", {
                "payload": updates
            })
            return {"success": True, "updated": list(updates.keys())}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def record_runbook_execution(runbook_id: str, success: bool, resolution_time: Optional[int] = None) -> dict:
        """Record a runbook execution and update statistics."""
        try:
            # Get current stats
            result = await qdrant_request(f"/collections/runbooks/points/{runbook_id}")
            payload = result.get("result", {}).get("payload", {})

            exec_count = payload.get("execution_count", 0) + 1
            success_count = payload.get("success_count", 0) + (1 if success else 0)
            success_rate = success_count / exec_count if exec_count > 0 else 0

            updates = {
                "execution_count": exec_count,
                "success_count": success_count,
                "success_rate": success_rate,
                "last_executed": datetime.utcnow().isoformat()
            }
            if resolution_time:
                updates["last_resolution_time"] = resolution_time

            await qdrant_request(f"/collections/runbooks/points/{runbook_id}", "PUT", {
                "payload": updates
            })
            return {"success": True, "execution_count": exec_count, "success_rate": success_rate}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def get_autonomy_config(level: str) -> dict:
        """Get the configuration for an autonomy level."""
        configs = {
            "manual": {"requires_approval": True, "min_success_rate": 0, "min_executions": 0},
            "prompted": {"requires_approval": True, "min_success_rate": 0.7, "min_executions": 5},
            "standard": {"requires_approval": False, "min_success_rate": 0.9, "min_executions": 10},
            "autonomous": {"requires_approval": False, "min_success_rate": 0.95, "min_executions": 20}
        }
        return configs.get(level, {"error": f"Unknown level: {level}"})

    @mcp.tool()
    async def list_autonomy_candidates(min_executions: int = 10, min_success_rate: float = 0.9) -> List[dict]:
        """List runbooks eligible for autonomy upgrade."""
        try:
            result = await qdrant_request("/collections/runbooks/points/scroll", "POST", {
                "limit": 100,
                "with_payload": True
            })
            candidates = []
            for p in result.get("result", {}).get("points", []):
                payload = p.get("payload", {})
                exec_count = payload.get("execution_count", 0)
                success_rate = payload.get("success_rate", 0)
                level = payload.get("automation_level", "manual")

                if exec_count >= min_executions and success_rate >= min_success_rate:
                    suggested = {"manual": "prompted", "prompted": "standard", "standard": "autonomous"}.get(level)
                    if suggested:
                        candidates.append({
                            "id": p.get("id"),
                            "title": payload.get("title"),
                            "current_level": level,
                            "suggested_level": suggested,
                            "execution_count": exec_count,
                            "success_rate": success_rate
                        })
            return candidates
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def search_documentation(query: str, limit: int = 5, min_score: float = 0.5) -> List[dict]:
        """Search documentation for information."""
        try:
            results = await search_collection("documentation", query, limit, min_score)
            return [{
                "id": r.get("id"),
                "score": r.get("score"),
                "title": r.get("payload", {}).get("title"),
                "content": r.get("payload", {}).get("content", "")[:500],
                "tags": r.get("payload", {}).get("tags", []),
                "path": r.get("payload", {}).get("path")
            } for r in results]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def add_documentation(title: str, content: str, path: Optional[str] = None, tags: Optional[List[str]] = None) -> dict:
        """Add documentation to the knowledge base."""
        try:
            embedding = await get_embedding(f"{title} {content}")
            point_id = str(uuid4())
            await qdrant_request("/collections/documentation/points", "PUT", {
                "points": [{
                    "id": point_id,
                    "vector": embedding,
                    "payload": {
                        "title": title,
                        "content": content,
                        "path": path,
                        "tags": tags or [],
                        "created_at": datetime.utcnow().isoformat()
                    }
                }]
            })
            return {"success": True, "id": point_id}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def search_entities(query: str, limit: int = 10) -> List[dict]:
        """Semantic search for network entities. Examples: 'Chromecast devices', 'IoT on guest VLAN'."""
        try:
            results = await search_collection("entities", query, limit, 0.5)
            return [{
                "id": r.get("id"),
                "score": r.get("score"),
                "hostname": r.get("payload", {}).get("hostname"),
                "ip": r.get("payload", {}).get("ip"),
                "mac": r.get("payload", {}).get("mac"),
                "entity_type": r.get("payload", {}).get("entity_type"),
                "network": r.get("payload", {}).get("network"),
                "manufacturer": r.get("payload", {}).get("manufacturer"),
                "location": r.get("payload", {}).get("location")
            } for r in results]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def get_entity(identifier: str) -> dict:
        """Get entity by IP, MAC, or hostname."""
        try:
            # Search by identifier in payload
            result = await qdrant_request("/collections/entities/points/scroll", "POST", {
                "limit": 1,
                "with_payload": True,
                "filter": {
                    "should": [
                        {"key": "ip", "match": {"value": identifier}},
                        {"key": "mac", "match": {"value": identifier.lower()}},
                        {"key": "hostname", "match": {"value": identifier}}
                    ]
                }
            })
            points = result.get("result", {}).get("points", [])
            if points:
                return points[0].get("payload", {})
            return {"error": "Entity not found"}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def get_entities_by_type(entity_type: str, limit: int = 50) -> List[dict]:
        """Get entities by type: sonoff, chromecast, nas, printer, switch, etc."""
        try:
            result = await qdrant_request("/collections/entities/points/scroll", "POST", {
                "limit": limit,
                "with_payload": True,
                "filter": {"must": [{"key": "entity_type", "match": {"value": entity_type}}]}
            })
            return [p.get("payload", {}) for p in result.get("result", {}).get("points", [])]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def get_entities_by_network(network: str, limit: int = 100) -> List[dict]:
        """Get entities by network: prod, iot-vlan, guest, management, etc."""
        try:
            result = await qdrant_request("/collections/entities/points/scroll", "POST", {
                "limit": limit,
                "with_payload": True,
                "filter": {"must": [{"key": "network", "match": {"value": network}}]}
            })
            return [p.get("payload", {}) for p in result.get("result", {}).get("points", [])]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def add_entity(
        hostname: str,
        ip: str,
        mac: Optional[str] = None,
        entity_type: Optional[str] = None,
        network: Optional[str] = None,
        manufacturer: Optional[str] = None,
        model: Optional[str] = None,
        location: Optional[str] = None,
        capabilities: Optional[List[str]] = None,
        notes: Optional[str] = None
    ) -> dict:
        """Add a new entity to the knowledge base."""
        try:
            text = f"{hostname} {ip} {entity_type or ''} {manufacturer or ''} {notes or ''}"
            embedding = await get_embedding(text)
            point_id = str(uuid4())
            await qdrant_request("/collections/entities/points", "PUT", {
                "points": [{
                    "id": point_id,
                    "vector": embedding,
                    "payload": {
                        "hostname": hostname,
                        "ip": ip,
                        "mac": mac,
                        "entity_type": entity_type,
                        "network": network,
                        "manufacturer": manufacturer,
                        "model": model,
                        "location": location,
                        "capabilities": capabilities or [],
                        "notes": notes,
                        "created_at": datetime.utcnow().isoformat()
                    }
                }]
            })
            return {"success": True, "id": point_id}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def delete_entity(entity_id: str) -> dict:
        """Delete an entity by its ID."""
        try:
            await qdrant_request(f"/collections/entities/points/delete", "POST", {
                "points": [entity_id]
            })
            return {"success": True}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def delete_entities_by_ip(ip: str) -> dict:
        """Delete all entities matching an IP address. Useful for cleaning up duplicates."""
        try:
            await qdrant_request("/collections/entities/points/delete", "POST", {
                "filter": {"must": [{"key": "ip", "match": {"value": ip}}]}
            })
            return {"success": True}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def list_entity_types() -> List[dict]:
        """List all unique entity types with counts."""
        try:
            result = await qdrant_request("/collections/entities/points/scroll", "POST", {
                "limit": 1000,
                "with_payload": ["entity_type"]
            })
            type_counts = {}
            for p in result.get("result", {}).get("points", []):
                t = p.get("payload", {}).get("entity_type", "unknown")
                type_counts[t] = type_counts.get(t, 0) + 1
            return [{"type": k, "count": v} for k, v in sorted(type_counts.items(), key=lambda x: -x[1])]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def search_decisions(query: str, limit: int = 5) -> List[dict]:
        """Search architectural decisions and past solutions."""
        try:
            results = await search_collection("decisions", query, limit, 0.5)
            return [{
                "id": r.get("id"),
                "score": r.get("score"),
                "title": r.get("payload", {}).get("title"),
                "decision": r.get("payload", {}).get("decision"),
                "rationale": r.get("payload", {}).get("rationale"),
                "alternatives": r.get("payload", {}).get("alternatives", [])
            } for r in results]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def add_decision(
        title: str,
        decision: str,
        rationale: str,
        alternatives: Optional[List[str]] = None,
        context: Optional[str] = None
    ) -> dict:
        """Record an architectural decision."""
        try:
            embedding = await get_embedding(f"{title} {decision} {rationale}")
            point_id = str(uuid4())
            await qdrant_request("/collections/decisions/points", "PUT", {
                "points": [{
                    "id": point_id,
                    "vector": embedding,
                    "payload": {
                        "title": title,
                        "decision": decision,
                        "rationale": rationale,
                        "alternatives": alternatives or [],
                        "context": context,
                        "created_at": datetime.utcnow().isoformat()
                    }
                }]
            })
            return {"success": True, "id": point_id}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def get_similar_events(event_description: str, limit: int = 5, min_score: float = 0.7) -> List[dict]:
        """Find similar historical events for pattern matching."""
        try:
            results = await search_collection("agent_events", event_description, limit, min_score)
            return [{
                "id": r.get("id"),
                "score": r.get("score"),
                "event_type": r.get("payload", {}).get("event_type"),
                "description": r.get("payload", {}).get("description"),
                "resolution": r.get("payload", {}).get("resolution"),
                "timestamp": r.get("payload", {}).get("timestamp")
            } for r in results]
        except Exception as e:
            return [{"error": str(e)}]

    @mcp.tool()
    async def log_event(
        event_type: str,
        description: str,
        source_agent: str = "claude-agent",
        metadata: Optional[Dict[str, Any]] = None,
        resolution: Optional[str] = None
    ) -> dict:
        """Log an event to agent_events collection for learning."""
        try:
            embedding = await get_embedding(f"{event_type} {description}")
            point_id = str(uuid4())
            await qdrant_request("/collections/agent_events/points", "PUT", {
                "points": [{
                    "id": point_id,
                    "vector": embedding,
                    "payload": {
                        "event_type": event_type,
                        "description": description,
                        "source_agent": source_agent,
                        "metadata": metadata or {},
                        "resolution": resolution,
                        "timestamp": datetime.utcnow().isoformat()
                    }
                }]
            })
            return {"success": True, "event_id": point_id}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def update_event(
        event_id: str,
        score: Optional[float] = None,
        feedback: Optional[str] = None,
        resolution: Optional[str] = None
    ) -> dict:
        """Update an existing event with feedback or outcome."""
        try:
            updates = {}
            if score is not None:
                updates["score"] = score
            if feedback:
                updates["feedback"] = feedback
            if resolution:
                updates["resolution"] = resolution
            updates["updated_at"] = datetime.utcnow().isoformat()

            await qdrant_request(f"/collections/agent_events/points/{event_id}", "PUT", {
                "payload": updates
            })
            return {"success": True}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def get_event(event_id: str) -> dict:
        """Get a specific event by ID."""
        try:
            result = await qdrant_request(f"/collections/agent_events/points/{event_id}")
            return result.get("result", {}).get("payload", {})
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def list_recent_events(
        limit: int = 50,
        event_type: Optional[str] = None,
        source_agent: Optional[str] = None
    ) -> List[dict]:
        """List recent events, optionally filtered by type or source."""
        try:
            filter_conditions = []
            if event_type:
                filter_conditions.append({"key": "event_type", "match": {"value": event_type}})
            if source_agent:
                filter_conditions.append({"key": "source_agent", "match": {"value": source_agent}})

            body = {"limit": limit, "with_payload": True}
            if filter_conditions:
                body["filter"] = {"must": filter_conditions}

            result = await qdrant_request("/collections/agent_events/points/scroll", "POST", body)
            events = [p.get("payload", {}) for p in result.get("result", {}).get("points", [])]
            return sorted(events, key=lambda x: x.get("timestamp", ""), reverse=True)[:limit]
        except Exception as e:
            return [{"error": str(e)}]

    # =========================================================================
    # Project 02: Dual-Indexing & Retrieval - New Tools
    # =========================================================================

    @mcp.tool()
    async def search_knowledge_nodes(
        query: str,
        node_type: Optional[str] = None,
        domain: Optional[str] = None,
        limit: int = 10,
        min_score: float = 0.6
    ) -> List[dict]:
        """Search dual-indexed knowledge nodes (Problems, Runbooks).

        Returns neo4j_id for graph traversal.

        Args:
            query: Search query
            node_type: Filter by 'problem' or 'runbook'
            domain: Filter by domain (infra, security, dns, etc.)
            limit: Max results
            min_score: Minimum similarity score
        """
        try:
            embedding = await get_embedding(query)

            filter_conditions = []
            if node_type:
                filter_conditions.append({"key": "type", "match": {"value": node_type}})
            if domain:
                filter_conditions.append({"key": "domain", "match": {"value": domain}})

            body = {
                "vector": embedding,
                "limit": limit,
                "with_payload": True,
                "score_threshold": min_score
            }
            if filter_conditions:
                body["filter"] = {"must": filter_conditions}

            result = await qdrant_request("/collections/knowledge_nodes/points/search", "POST", body)

            return [{
                "id": r.get("id"),
                "score": r.get("score"),
                "neo4j_id": r.get("payload", {}).get("neo4j_id"),
                "type": r.get("payload", {}).get("type"),
                "domain": r.get("payload", {}).get("domain"),
                "content_hash": r.get("payload", {}).get("content_hash")
            } for r in result.get("result", [])]
        except Exception as e:
            logger.error(f"search_knowledge_nodes failed: {e}")
            return [{"error": str(e)}]

    @mcp.tool()
    async def vector_search_documents(
        query: str,
        doc_type: Optional[str] = None,
        domain: Optional[str] = None,
        limit: int = 10,
        min_score: float = 0.5
    ) -> List[dict]:
        """Vector search the documents collection (solutions, artifacts, documentation).

        Args:
            query: Search query
            doc_type: Filter by type (solution, artifact, runbook_step, documentation)
            domain: Filter by domain
            limit: Max results
            min_score: Minimum similarity score
        """
        try:
            embedding = await get_embedding(query)

            filter_conditions = []
            if doc_type:
                filter_conditions.append({"key": "type", "match": {"value": doc_type}})
            if domain:
                filter_conditions.append({"key": "domain", "match": {"value": domain}})

            body = {
                "vector": embedding,
                "limit": limit,
                "with_payload": True,
                "score_threshold": min_score
            }
            if filter_conditions:
                body["filter"] = {"must": filter_conditions}

            result = await qdrant_request("/collections/documents/points/search", "POST", body)

            return [{
                "id": r.get("id"),
                "score": r.get("score"),
                "type": r.get("payload", {}).get("type"),
                "title": r.get("payload", {}).get("title"),
                "content": r.get("payload", {}).get("content", "")[:500],
                "domain": r.get("payload", {}).get("domain"),
                "neo4j_id": r.get("payload", {}).get("neo4j_id"),
                "tags": r.get("payload", {}).get("tags", [])
            } for r in result.get("result", [])]
        except Exception as e:
            logger.error(f"vector_search_documents failed: {e}")
            return [{"error": str(e)}]

    @mcp.tool()
    async def vector_add_document(
        title: str,
        content: str,
        doc_type: str = "documentation",
        domain: Optional[str] = None,
        tags: Optional[List[str]] = None,
        neo4j_id: Optional[str] = None,
        parent_id: Optional[str] = None,
        source: str = "manual"
    ) -> dict:
        """Add a document to the documents collection.

        Args:
            title: Document title
            content: Full text content
            doc_type: Type (solution, artifact, runbook_step, documentation)
            domain: Domain classification
            tags: Freeform tags
            neo4j_id: Link to Neo4j node if applicable
            parent_id: Parent document/runbook ID
            source: Source (runbook, solution, manual, learned)
        """
        try:
            import hashlib

            # Create embedding from title + content
            embed_text = f"{title}\n"
            if domain:
                embed_text += f"Domain: {domain}\n"
            if tags:
                embed_text += f"Tags: {', '.join(tags)}\n"
            embed_text += content[:10000]  # Truncate for embedding

            embedding = await get_embedding(embed_text)
            doc_id = str(uuid4())
            content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

            await qdrant_request("/collections/documents/points", "PUT", {
                "points": [{
                    "id": doc_id,
                    "vector": embedding,
                    "payload": {
                        "title": title,
                        "content": content[:50000],  # Store truncated
                        "content_hash": content_hash,
                        "type": doc_type,
                        "domain": domain,
                        "tags": tags or [],
                        "neo4j_id": neo4j_id,
                        "parent_id": parent_id,
                        "source": source,
                        "created_at": datetime.utcnow().isoformat(),
                        "indexed_at": datetime.utcnow().isoformat()
                    }
                }]
            })

            return {"success": True, "id": doc_id, "content_hash": content_hash}
        except Exception as e:
            logger.error(f"vector_add_document failed: {e}")
            return {"error": str(e)}

    # =========================================================================
    # Project 02: Dual-Indexing CRUD Functions
    # Atomic writes to Neo4j + Qdrant with rollback handling
    # =========================================================================

    @mcp.tool()
    async def create_problem_with_dual_index(
        description: str,
        domain: str,
        runbook_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        source: str = "agent"
    ) -> dict:
        """Create a Problem in Neo4j and dual-index in Qdrant.

        Atomic operation: creates in Neo4j first, then indexes in Qdrant.
        If Qdrant fails, rolls back the Neo4j creation.

        Args:
            description: Problem description (used for embedding)
            domain: Problem domain (infra, security, obs, dns, network, data)
            runbook_id: Optional runbook ID to link via SOLVES relationship
            tags: Optional tags for categorization
            source: Source of the problem (agent, manual, migration)

        Returns:
            Dict with problem_id, neo4j_id, qdrant_indexed status
        """
        from uuid import uuid4
        import hashlib

        problem_id = str(uuid4())
        content_hash = hashlib.sha256(description.encode()).hexdigest()[:16]

        try:
            # Step 1: Create Problem node in Neo4j
            neo4j_result = await _neo4j_query("""
                CREATE (p:Problem {
                    id: $problem_id,
                    description: $description,
                    domain: $domain,
                    tags: $tags,
                    source: $source,
                    content_hash: $content_hash,
                    created_at: datetime(),
                    last_referenced: datetime(),
                    weight: 1.0
                })
                RETURN p.id as id
            """, {
                "problem_id": problem_id,
                "description": description,
                "domain": domain,
                "tags": tags or [],
                "source": source,
                "content_hash": content_hash
            })

            if neo4j_result.get("errors"):
                return {"error": f"Neo4j creation failed: {neo4j_result['errors']}"}

            # Step 2: Generate embedding and index in Qdrant
            try:
                embedding = await get_embedding(description)

                await qdrant_request("/collections/knowledge_nodes/points", "PUT", {
                    "points": [{
                        "id": problem_id,
                        "vector": embedding,
                        "payload": {
                            "type": "problem",
                            "neo4j_id": problem_id,
                            "domain": domain,
                            "description": description[:1000],
                            "tags": tags or [],
                            "source": source,
                            "content_hash": content_hash,
                            "indexed_at": datetime.utcnow().isoformat()
                        }
                    }]
                })

            except Exception as qdrant_error:
                # Rollback: Delete from Neo4j
                logger.error(f"Qdrant indexing failed, rolling back Neo4j: {qdrant_error}")
                await _neo4j_query("MATCH (p:Problem {id: $id}) DELETE p", {"id": problem_id})
                return {"error": f"Dual-index failed (rolled back): {qdrant_error}"}

            # Step 3: Create SOLVES relationship if runbook_id provided
            if runbook_id:
                await _neo4j_query("""
                    MATCH (r:Runbook {id: $runbook_id})
                    MATCH (p:Problem {id: $problem_id})
                    MERGE (r)-[:SOLVES]->(p)
                """, {"runbook_id": runbook_id, "problem_id": problem_id})

            logger.info(f"Created Problem with dual-index: {problem_id}")
            return {
                "success": True,
                "problem_id": problem_id,
                "neo4j_id": problem_id,
                "qdrant_indexed": True,
                "content_hash": content_hash
            }

        except Exception as e:
            logger.error(f"create_problem_with_dual_index failed: {e}")
            return {"error": str(e)}

    @mcp.tool()
    async def update_problem_with_reindex(
        problem_id: str,
        description: Optional[str] = None,
        domain: Optional[str] = None,
        tags: Optional[List[str]] = None
    ) -> dict:
        """Update a Problem in Neo4j and re-index in Qdrant if content changed.

        Only re-embeds if description changes (detected via content_hash).

        Args:
            problem_id: The Problem's ID
            description: New description (triggers re-embedding)
            domain: New domain
            tags: New tags

        Returns:
            Dict with update status and whether re-indexing occurred
        """
        import hashlib

        try:
            # Get current state
            current = await _neo4j_query("""
                MATCH (p:Problem {id: $id})
                RETURN p.description as description, p.content_hash as content_hash
            """, {"id": problem_id})

            records = _parse_neo4j_results(current)
            if not records:
                return {"error": "Problem not found"}

            old_hash = records[0].get("content_hash", "")
            needs_reindex = False

            # Build update SET clause dynamically
            updates = []
            params = {"id": problem_id}

            if description is not None:
                new_hash = hashlib.sha256(description.encode()).hexdigest()[:16]
                if new_hash != old_hash:
                    needs_reindex = True
                    updates.append("p.description = $description")
                    updates.append("p.content_hash = $content_hash")
                    params["description"] = description
                    params["content_hash"] = new_hash

            if domain is not None:
                updates.append("p.domain = $domain")
                params["domain"] = domain

            if tags is not None:
                updates.append("p.tags = $tags")
                params["tags"] = tags

            if not updates:
                return {"success": True, "message": "No changes to apply"}

            updates.append("p.last_referenced = datetime()")

            # Update Neo4j
            await _neo4j_query(f"""
                MATCH (p:Problem {{id: $id}})
                SET {', '.join(updates)}
            """, params)

            # Re-index in Qdrant if content changed
            if needs_reindex:
                embedding = await get_embedding(description)

                # Get existing payload to merge
                existing = await qdrant_request(
                    f"/collections/knowledge_nodes/points/{problem_id}"
                )
                old_payload = existing.get("result", {}).get("payload", {})

                await qdrant_request("/collections/knowledge_nodes/points", "PUT", {
                    "points": [{
                        "id": problem_id,
                        "vector": embedding,
                        "payload": {
                            **old_payload,
                            "description": description[:1000],
                            "domain": domain or old_payload.get("domain"),
                            "tags": tags or old_payload.get("tags", []),
                            "content_hash": params.get("content_hash"),
                            "indexed_at": datetime.utcnow().isoformat()
                        }
                    }]
                })

            logger.info(f"Updated Problem {problem_id}, reindexed: {needs_reindex}")
            return {
                "success": True,
                "problem_id": problem_id,
                "reindexed": needs_reindex
            }

        except Exception as e:
            logger.error(f"update_problem_with_reindex failed: {e}")
            return {"error": str(e)}

    @mcp.tool()
    async def delete_problem_with_dual_index(problem_id: str) -> dict:
        """Delete a Problem from both Neo4j and Qdrant.

        Deletes from Qdrant first (less critical), then Neo4j.
        Also removes any SOLVES relationships.

        Args:
            problem_id: The Problem's ID

        Returns:
            Dict with deletion status for both stores
        """
        qdrant_deleted = False
        neo4j_deleted = False

        try:
            # Step 1: Delete from Qdrant (less critical, do first)
            try:
                await qdrant_request(
                    f"/collections/knowledge_nodes/points/delete",
                    "POST",
                    {"points": [problem_id]}
                )
                qdrant_deleted = True
            except Exception as e:
                logger.warning(f"Qdrant deletion failed (continuing): {e}")

            # Step 2: Delete from Neo4j (including relationships)
            result = await _neo4j_query("""
                MATCH (p:Problem {id: $id})
                OPTIONAL MATCH (p)-[r]-()
                DELETE r, p
                RETURN count(p) as deleted
            """, {"id": problem_id})

            records = _parse_neo4j_results(result)
            neo4j_deleted = records and records[0].get("deleted", 0) > 0

            if not neo4j_deleted and not qdrant_deleted:
                return {"error": "Problem not found in either store"}

            logger.info(f"Deleted Problem {problem_id}: neo4j={neo4j_deleted}, qdrant={qdrant_deleted}")
            return {
                "success": True,
                "problem_id": problem_id,
                "neo4j_deleted": neo4j_deleted,
                "qdrant_deleted": qdrant_deleted
            }

        except Exception as e:
            logger.error(f"delete_problem_with_dual_index failed: {e}")
            return {"error": str(e)}

    @mcp.tool()
    async def search_all(query: str, limit: int = 3) -> dict:
        """Search across all collections (runbooks, docs, entities, decisions)."""
        try:
            results = {}
            for collection in ["runbooks", "documentation", "entities", "decisions"]:
                try:
                    search_results = await search_collection(collection, query, limit, 0.5)
                    results[collection] = [{
                        "id": r.get("id"),
                        "score": r.get("score"),
                        "title": r.get("payload", {}).get("title") or r.get("payload", {}).get("hostname"),
                        "preview": str(r.get("payload", {}))[:200]
                    } for r in search_results]
                except:
                    results[collection] = []
            return results
        except Exception as e:
            return {"error": str(e)}
