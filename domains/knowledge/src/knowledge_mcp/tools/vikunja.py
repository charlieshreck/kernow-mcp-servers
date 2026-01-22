"""Vikunja task management tools."""

import os
import logging
from typing import List, Optional
from datetime import datetime

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
VIKUNJA_URL = os.environ.get("VIKUNJA_URL", "http://vikunja.vikunja.svc.cluster.local:8080")
VIKUNJA_TOKEN = os.environ.get("VIKUNJA_TOKEN", "")


async def vikunja_api(endpoint: str, method: str = "GET", data: dict = None) -> dict:
    """Make authenticated API call to Vikunja."""
    headers = {"Authorization": f"Bearer {VIKUNJA_TOKEN}"}
    url = f"{VIKUNJA_URL}/api/v1{endpoint}"

    async with httpx.AsyncClient(timeout=30.0) as client:
        if method == "GET":
            resp = await client.get(url, headers=headers)
        elif method == "POST":
            resp = await client.post(url, headers=headers, json=data)
        elif method == "PUT":
            resp = await client.put(url, headers=headers, json=data)
        elif method == "DELETE":
            resp = await client.delete(url, headers=headers)
        else:
            raise ValueError(f"Unsupported method: {method}")

        resp.raise_for_status()
        return resp.json() if resp.text else {}


async def get_status() -> dict:
    """Get Vikunja status for health checks."""
    try:
        projects = await vikunja_api("/projects")
        return {"status": "healthy", "projects": len(projects)}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


def register_tools(mcp: FastMCP):
    """Register Vikunja tools with the MCP server."""

    # =========================================================================
    # Projects
    # =========================================================================

    @mcp.tool()
    async def list_projects() -> List[dict]:
        """List all Vikunja projects."""
        return await vikunja_api("/projects")

    @mcp.tool()
    async def create_project(title: str, description: str = "") -> dict:
        """Create a new project/list."""
        return await vikunja_api("/projects", "PUT", {
            "title": title,
            "description": description
        })

    @mcp.tool()
    async def get_project(project_id: int) -> dict:
        """Get project details including tasks."""
        return await vikunja_api(f"/projects/{project_id}")

    # =========================================================================
    # Tasks
    # =========================================================================

    @mcp.tool()
    async def list_tasks(project_id: int) -> List[dict]:
        """List all tasks in a project."""
        return await vikunja_api(f"/projects/{project_id}/tasks")

    @mcp.tool()
    async def create_task(
        project_id: int,
        title: str,
        description: str = "",
        priority: int = 0,
        bucket_id: int = None
    ) -> dict:
        """Create a new task in a project."""
        data = {
            "title": title,
            "description": description,
            "priority": priority
        }
        if bucket_id:
            data["bucket_id"] = bucket_id
        return await vikunja_api(f"/projects/{project_id}/tasks", "PUT", data)

    @mcp.tool()
    async def update_task(
        task_id: int,
        title: str = None,
        description: str = None,
        done: bool = None,
        priority: int = None,
        bucket_id: int = None
    ) -> dict:
        """Update an existing task."""
        data = {}
        if title is not None:
            data["title"] = title
        if description is not None:
            data["description"] = description
        if done is not None:
            data["done"] = done
        if priority is not None:
            data["priority"] = priority
        if bucket_id is not None:
            data["bucket_id"] = bucket_id
        return await vikunja_api(f"/tasks/{task_id}", "PUT", data)

    @mcp.tool()
    async def complete_task(task_id: int) -> dict:
        """Mark a task as complete."""
        return await vikunja_api(f"/tasks/{task_id}", "PUT", {"done": True})

    # =========================================================================
    # Buckets (Kanban columns)
    # =========================================================================

    @mcp.tool()
    async def list_buckets(project_id: int) -> List[dict]:
        """List kanban buckets/columns in a project."""
        return await vikunja_api(f"/projects/{project_id}/buckets")

    @mcp.tool()
    async def create_bucket(project_id: int, title: str) -> dict:
        """Create a new kanban bucket/column."""
        return await vikunja_api(f"/projects/{project_id}/buckets", "PUT", {
            "title": title
        })

    @mcp.tool()
    async def move_task_to_bucket(task_id: int, bucket_id: int) -> dict:
        """Move a task to a different kanban bucket."""
        return await vikunja_api(f"/tasks/{task_id}", "PUT", {"bucket_id": bucket_id})

    # =========================================================================
    # Ideas & Quick Add
    # =========================================================================

    @mcp.tool()
    async def add_idea(idea: str, project_name: str = "Ideas") -> dict:
        """Quickly add an idea to the Ideas project."""
        # Find or create Ideas project
        projects = await vikunja_api("/projects")
        ideas_project = next((p for p in projects if p["title"] == project_name), None)

        if not ideas_project:
            ideas_project = await vikunja_api("/projects", "PUT", {
                "title": project_name,
                "description": "Quick ideas and thoughts captured by Claude"
            })

        # Create task with idea
        return await vikunja_api(f"/projects/{ideas_project['id']}/tasks", "PUT", {
            "title": idea,
            "description": f"Captured: {datetime.utcnow().isoformat()}"
        })

    @mcp.tool()
    async def list_ideas(project_name: str = "Ideas") -> List[dict]:
        """List all ideas from the Ideas project."""
        projects = await vikunja_api("/projects")
        ideas_project = next((p for p in projects if p["title"] == project_name), None)
        if not ideas_project:
            return []
        return await vikunja_api(f"/projects/{ideas_project['id']}/tasks")

    # =========================================================================
    # Plan Mode Integration
    # =========================================================================

    @mcp.tool()
    async def create_plan_board(
        plan_name: str,
        steps: List[str],
        buckets: List[str] = None
    ) -> dict:
        """Create a kanban board for a plan with steps as tasks.

        Args:
            plan_name: Name of the plan/project
            steps: List of implementation steps
            buckets: Optional bucket names (default: Todo, In Progress, Done)
        """
        if not buckets:
            buckets = ["Todo", "In Progress", "Done"]

        # Create project
        project = await vikunja_api("/projects", "PUT", {
            "title": f"Plan: {plan_name}",
            "description": f"Created by Claude at {datetime.utcnow().isoformat()}"
        })

        # Create buckets
        bucket_ids = {}
        for bucket_name in buckets:
            bucket = await vikunja_api(f"/projects/{project['id']}/buckets", "PUT", {
                "title": bucket_name
            })
            bucket_ids[bucket_name] = bucket["id"]

        # Create tasks in first bucket
        tasks = []
        for i, step in enumerate(steps):
            task = await vikunja_api(f"/projects/{project['id']}/tasks", "PUT", {
                "title": step,
                "bucket_id": bucket_ids[buckets[0]],
                "position": i
            })
            tasks.append(task)

        return {
            "project": project,
            "buckets": bucket_ids,
            "tasks": tasks,
            "url": f"{VIKUNJA_URL}/projects/{project['id']}"
        }

    @mcp.tool()
    async def update_plan_progress(
        project_id: int,
        task_id: int,
        status: str
    ) -> dict:
        """Update a plan task's status by moving to appropriate bucket.

        Args:
            project_id: The plan's project ID
            task_id: The task to update
            status: One of: 'todo', 'in_progress', 'done'
        """
        buckets = await vikunja_api(f"/projects/{project_id}/buckets")
        status_map = {
            "todo": "Todo",
            "in_progress": "In Progress",
            "done": "Done"
        }

        target_bucket = next(
            (b for b in buckets if b["title"] == status_map.get(status)),
            None
        )

        if not target_bucket:
            return {"error": f"Bucket for status '{status}' not found"}

        return await vikunja_api(f"/tasks/{task_id}", "PUT", {
            "bucket_id": target_bucket["id"],
            "done": status == "done"
        })
