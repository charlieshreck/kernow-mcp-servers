#!/usr/bin/env python3
"""Knowledge MCP - Consolidated semantic search, graph queries, wiki, and task management."""

import os
import logging

from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse
from starlette.requests import Request
import uvicorn

from knowledge_mcp.tools import qdrant, neo4j, vikunja, outline, silverbullet, retrieval
from knowledge_mcp.tools.qdrant import qdrant_request
from knowledge_mcp.tools.silverbullet import (
    do_sync_outline_to_silverbullet,
    do_sync_silverbullet_to_outline,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create MCP server
mcp = FastMCP(
    name="knowledge-mcp",
    instructions="""MCP server for comprehensive knowledge base operations.
    Collections: runbooks, documentation, entities, decisions, agent_events.
    Use for semantic search, entity lookup, runbook management, and decision tracking.""",
    stateless_http=True
)

# Register all tool modules
qdrant.register_tools(mcp)
neo4j.register_tools(mcp)
vikunja.register_tools(mcp)
outline.register_tools(mcp)
silverbullet.register_tools(mcp)
retrieval.register_tools(mcp)


# =============================================================================
# Health Check Endpoints
# =============================================================================

async def health(request):
    """Basic health check."""
    return JSONResponse({"status": "healthy", "service": "knowledge-mcp"})


async def ready(request):
    """Readiness check with component status."""
    qdrant_status = await qdrant.get_status()
    neo4j_status = await neo4j.get_status()
    vikunja_status = await vikunja.get_status()
    outline_status = await outline.get_status()
    silverbullet_status = await silverbullet.get_status()
    retrieval_status = await retrieval.get_status()

    components = {
        "qdrant": qdrant_status.get("status"),
        "neo4j": neo4j_status.get("status"),
        "vikunja": vikunja_status.get("status"),
        "outline": outline_status.get("status"),
        "silverbullet": silverbullet_status.get("status"),
        "retrieval": retrieval_status.get("status"),
    }

    all_healthy = all(s == "healthy" for s in components.values())

    return JSONResponse({
        "status": "ready" if all_healthy else "degraded",
        "components": components
    }, status_code=200 if all_healthy else 503)


# =============================================================================
# REST API Endpoints (used by fumadocs)
# =============================================================================

async def api_list_runbooks(request: Request):
    """REST endpoint for listing runbooks."""
    limit = int(request.query_params.get("limit", "100"))
    try:
        result = await qdrant_request("/collections/runbooks/points/scroll", "POST", {
            "limit": limit,
            "with_payload": True
        })
        points = result.get("result", {}).get("points", [])
        runbooks = [{
            "id": str(p.get("id")),
            "title": p.get("payload", {}).get("title", ""),
            "trigger_pattern": p.get("payload", {}).get("trigger_pattern", ""),
            "solution": p.get("payload", {}).get("solution", "")[:500],
            "automation_level": p.get("payload", {}).get("automation_level", "manual"),
            "path": p.get("payload", {}).get("path", ""),
            "domain": p.get("payload", {}).get("domain", ""),
        } for p in points]
        return JSONResponse({"status": "ok", "runbooks": runbooks, "count": len(runbooks)})
    except Exception as e:
        logger.error(f"List runbooks error: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


async def api_get_runbook(request: Request):
    """REST endpoint for getting a single runbook by ID."""
    runbook_id = request.path_params["runbook_id"]
    try:
        result = await qdrant_request(f"/collections/runbooks/points/{runbook_id}")
        point = result.get("result", {})
        payload = point.get("payload", {})
        return JSONResponse({"status": "ok", "runbook": payload})
    except Exception as e:
        logger.error(f"Get runbook error: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


# =============================================================================
# Webhook Endpoints
# =============================================================================

async def outline_webhook(request: Request):
    """Handle Outline webhook events for real-time sync.

    Triggers sync when collections are created, updated, or deleted.
    Configure in Outline: Settings → Webhooks → Add webhook
    URL: http://knowledge-mcp.ai-platform.svc.cluster.local:8000/webhooks/outline
    """
    try:
        data = await request.json()
        event = data.get("event", "")
        payload = data.get("payload", {})

        logger.info(f"Outline webhook received: {event}")

        # Sync on collection events
        if event in ["collections.create", "collections.update", "collections.delete"]:
            collection_name = payload.get("model", {}).get("name", "unknown")
            logger.info(f"Syncing after collection event: {event} - {collection_name}")
            result = await do_sync_outline_to_silverbullet()
            logger.info(f"Sync result: {result}")
            return JSONResponse({"status": "synced", "result": result})

        # Acknowledge but don't sync for other events
        return JSONResponse({"status": "acknowledged", "event": event})

    except Exception as e:
        logger.error(f"Outline webhook error: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


async def silverbullet_webhook(request: Request):
    """Handle Silver Bullet sync trigger.

    Since SB doesn't have native webhooks, this can be called:
    - By a CronJob for periodic sync
    - Manually when needed

    URL: http://knowledge-mcp.ai-platform.svc.cluster.local:8000/webhooks/silverbullet
    """
    try:
        result = await do_sync_silverbullet_to_outline()
        logger.info(f"SB→Outline sync result: {result}")
        return JSONResponse({"status": "synced", "result": result})
    except Exception as e:
        logger.error(f"Silver Bullet sync error: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


async def reconcile_webhook(request: Request):
    """Trigger Qdrant↔Neo4j dual-index reconciliation.

    Called by:
    - Weekly CronJob (knowledge-reconcile)
    - Manual trigger when needed

    Query params:
    - dry_run=true: Detect issues without fixing
    - fix_orphans=false: Skip deleting orphans
    - fix_missing=false: Skip re-indexing missing
    - fix_stale=false: Skip re-indexing stale

    URL: http://knowledge-mcp.ai-platform.svc.cluster.local:8000/webhooks/reconcile
    """
    from knowledge_mcp.jobs.reconcile import reconcile_dual_index

    try:
        # Parse query params
        params = request.query_params
        dry_run = params.get("dry_run", "false").lower() == "true"
        fix_orphans = params.get("fix_orphans", "true").lower() != "false"
        fix_missing = params.get("fix_missing", "true").lower() != "false"
        fix_stale = params.get("fix_stale", "true").lower() != "false"

        logger.info(f"Starting reconciliation: dry_run={dry_run}")

        result = await reconcile_dual_index(
            dry_run=dry_run,
            fix_orphans=fix_orphans,
            fix_missing=fix_missing,
            fix_stale=fix_stale
        )

        return JSONResponse({
            "status": "completed",
            "started_at": result.started_at,
            "completed_at": result.completed_at,
            "duration_seconds": result.duration_seconds,
            "qdrant_total": result.qdrant_total,
            "neo4j_problems": result.neo4j_problems,
            "neo4j_runbooks": result.neo4j_runbooks,
            "issues": {
                "orphaned_qdrant": len(result.orphaned_qdrant),
                "missing_qdrant": len(result.missing_qdrant),
                "hash_mismatches": len(result.hash_mismatches),
            },
            "actions": {
                "deleted_orphans": result.deleted_orphans,
                "reindexed_missing": result.reindexed_missing,
                "reindexed_stale": result.reindexed_stale,
            },
            "errors": result.errors,
        })

    except Exception as e:
        logger.error(f"Reconciliation error: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    port = int(os.environ.get("PORT", "8000"))
    logger.info(f"Starting Knowledge MCP on port {port}")

    # Create combined Starlette app with health routes, webhooks, and MCP
    rest_routes = [
        Route("/health", health, methods=["GET"]),
        Route("/ready", ready, methods=["GET"]),
        Route("/api/runbooks/{runbook_id}", api_get_runbook, methods=["GET"]),
        Route("/api/runbooks", api_list_runbooks, methods=["GET"]),
        Route("/webhooks/outline", outline_webhook, methods=["POST"]),
        Route("/webhooks/silverbullet", silverbullet_webhook, methods=["POST", "GET"]),
        Route("/webhooks/reconcile", reconcile_webhook, methods=["POST", "GET"]),
    ]

    mcp_app = mcp.http_app()
    app = Starlette(
        routes=rest_routes + [Mount("/", app=mcp_app)],
        lifespan=mcp_app.lifespan
    )

    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
