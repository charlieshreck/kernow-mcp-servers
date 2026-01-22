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


def register_tools(mcp: FastMCP):
    """Register Qdrant tools with the MCP server."""

    @mcp.tool()
    async def list_collections() -> List[dict]:
        """List all available Qdrant collections with point counts."""
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
