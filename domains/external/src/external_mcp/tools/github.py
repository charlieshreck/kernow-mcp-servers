"""GitHub API tools for repository operations."""

import os
import base64
import logging
from typing import Optional, List, Dict, Any

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_API_URL = "https://api.github.com"


async def _gh_api(endpoint: str, method: str = "GET", data: dict = None) -> Dict[str, Any]:
    """Make request to GitHub API."""
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        url = f"{GITHUB_API_URL}{endpoint}"
        if method == "GET":
            response = await client.get(url, headers=headers)
        elif method == "POST":
            response = await client.post(url, headers=headers, json=data)
        elif method == "PATCH":
            response = await client.patch(url, headers=headers, json=data)
        elif method == "DELETE":
            response = await client.delete(url, headers=headers)
        response.raise_for_status()
        return response.json() if response.text else {}


def _handle_error(e: Exception) -> str:
    """Format error message."""
    if isinstance(e, httpx.HTTPStatusError):
        status = e.response.status_code
        if status == 401:
            return "Error: Invalid GitHub token."
        elif status == 403:
            return "Error: Rate limited or insufficient permissions."
        elif status == 404:
            return "Error: Resource not found."
        return f"Error: GitHub API returned {status}: {e.response.text[:200]}"
    return f"Error: {type(e).__name__}: {str(e)}"


def register_tools(mcp: FastMCP):
    """Register GitHub tools with the MCP server."""

    # Repository operations
    @mcp.tool(name="github_get_repo", annotations={"readOnlyHint": True})
    async def github_get_repo(owner: str, repo: str) -> str:
        """Get repository details including stars, forks, and description."""
        try:
            r = await _gh_api(f"/repos/{owner}/{repo}")
            return (
                f"# {r.get('full_name')}\n\n"
                f"**Description:** {r.get('description', 'N/A')}\n"
                f"**Stars:** {r.get('stargazers_count', 0):,}\n"
                f"**Forks:** {r.get('forks_count', 0):,}\n"
                f"**Language:** {r.get('language', 'N/A')}\n"
                f"**Default Branch:** {r.get('default_branch')}\n"
                f"**Open Issues:** {r.get('open_issues_count', 0)}\n"
                f"**URL:** {r.get('html_url')}"
            )
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="github_list_repos", annotations={"readOnlyHint": True})
    async def github_list_repos(owner: str, type: str = "owner", per_page: int = 10) -> str:
        """List repositories for a user or organization."""
        try:
            repos = await _gh_api(f"/users/{owner}/repos?type={type}&per_page={per_page}&sort=updated")
            lines = [f"# Repositories for {owner}", "", f"Found {len(repos)} repos", ""]
            for r in repos[:per_page]:
                stars = r.get('stargazers_count', 0)
                lines.append(f"- **{r.get('name')}** ({stars}) - {r.get('description', '')[:60]}")
            return "\n".join(lines)
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="github_get_file", annotations={"readOnlyHint": True})
    async def github_get_file(owner: str, repo: str, path: str, ref: str = "main") -> str:
        """Get contents of a file from a repository."""
        try:
            r = await _gh_api(f"/repos/{owner}/{repo}/contents/{path}?ref={ref}")
            if r.get('type') != 'file':
                return f"Error: {path} is not a file"
            content = base64.b64decode(r.get('content', '')).decode('utf-8')
            return f"# {path}\n\n```\n{content[:10000]}\n```"
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="github_list_branches", annotations={"readOnlyHint": True})
    async def github_list_branches(owner: str, repo: str, per_page: int = 20) -> str:
        """List branches in a repository."""
        try:
            branches = await _gh_api(f"/repos/{owner}/{repo}/branches?per_page={per_page}")
            lines = [f"# Branches in {owner}/{repo}", ""]
            for b in branches:
                protected = "[protected]" if b.get('protected') else ""
                lines.append(f"- `{b.get('name')}` {protected}")
            return "\n".join(lines)
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="github_get_commits", annotations={"readOnlyHint": True})
    async def github_get_commits(owner: str, repo: str, sha: str = "main", per_page: int = 10) -> str:
        """Get recent commits from a branch."""
        try:
            commits = await _gh_api(f"/repos/{owner}/{repo}/commits?sha={sha}&per_page={per_page}")
            lines = [f"# Recent Commits ({sha})", ""]
            for c in commits:
                sha_short = c.get('sha', '')[:7]
                msg = c.get('commit', {}).get('message', '').split('\n')[0][:60]
                author = c.get('commit', {}).get('author', {}).get('name', 'Unknown')
                lines.append(f"- `{sha_short}` {msg} ({author})")
            return "\n".join(lines)
        except Exception as e:
            return _handle_error(e)

    # Issues
    @mcp.tool(name="github_list_issues", annotations={"readOnlyHint": True})
    async def github_list_issues(owner: str, repo: str, state: str = "open", per_page: int = 10) -> str:
        """List issues in a repository."""
        try:
            issues = await _gh_api(f"/repos/{owner}/{repo}/issues?state={state}&per_page={per_page}")
            lines = [f"# Issues ({state}) - {owner}/{repo}", ""]
            for i in issues:
                if 'pull_request' in i:
                    continue  # Skip PRs
                labels = ', '.join([l.get('name') for l in i.get('labels', [])])
                lines.append(f"- #{i.get('number')} **{i.get('title')[:50]}** [{labels}]")
            return "\n".join(lines) if len(lines) > 2 else "No issues found."
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="github_get_issue", annotations={"readOnlyHint": True})
    async def github_get_issue(owner: str, repo: str, issue_number: int) -> str:
        """Get details of a specific issue."""
        try:
            i = await _gh_api(f"/repos/{owner}/{repo}/issues/{issue_number}")
            labels = ', '.join([l.get('name') for l in i.get('labels', [])])
            return (
                f"# Issue #{i.get('number')}: {i.get('title')}\n\n"
                f"**State:** {i.get('state')}\n"
                f"**Author:** {i.get('user', {}).get('login')}\n"
                f"**Labels:** {labels or 'None'}\n"
                f"**Comments:** {i.get('comments', 0)}\n\n"
                f"---\n\n{i.get('body', 'No description')[:2000]}"
            )
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="github_create_issue", annotations={"destructiveHint": False})
    async def github_create_issue(
        owner: str,
        repo: str,
        title: str,
        body: str = "",
        labels: List[str] = None
    ) -> str:
        """Create a new issue in a repository."""
        try:
            data = {"title": title, "body": body}
            if labels:
                data["labels"] = labels
            i = await _gh_api(f"/repos/{owner}/{repo}/issues", "POST", data)
            return f"# Issue Created\n\n- Number: #{i.get('number')}\n- URL: {i.get('html_url')}"
        except Exception as e:
            return _handle_error(e)

    # Pull requests
    @mcp.tool(name="github_list_prs", annotations={"readOnlyHint": True})
    async def github_list_prs(owner: str, repo: str, state: str = "open", per_page: int = 10) -> str:
        """List pull requests in a repository."""
        try:
            prs = await _gh_api(f"/repos/{owner}/{repo}/pulls?state={state}&per_page={per_page}")
            lines = [f"# Pull Requests ({state}) - {owner}/{repo}", ""]
            for p in prs:
                draft = "[draft]" if p.get('draft') else ""
                head = p.get('head', {}).get('ref', '')
                base = p.get('base', {}).get('ref', '')
                lines.append(f"- {draft}#{p.get('number')} **{p.get('title')[:50]}** ({head} -> {base})")
            return "\n".join(lines) if len(lines) > 2 else "No PRs found."
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="github_get_pr", annotations={"readOnlyHint": True})
    async def github_get_pr(owner: str, repo: str, pr_number: int) -> str:
        """Get details of a specific pull request."""
        try:
            p = await _gh_api(f"/repos/{owner}/{repo}/pulls/{pr_number}")
            merged = '(merged)' if p.get('merged') else ''
            return (
                f"# PR #{p.get('number')}: {p.get('title')}\n\n"
                f"**State:** {p.get('state')} {merged}\n"
                f"**Author:** {p.get('user', {}).get('login')}\n"
                f"**Branch:** {p.get('head', {}).get('ref')} -> {p.get('base', {}).get('ref')}\n"
                f"**Commits:** {p.get('commits', 0)} | **Changed Files:** {p.get('changed_files', 0)}\n"
                f"**+{p.get('additions', 0)} / -{p.get('deletions', 0)}**\n\n"
                f"---\n\n{p.get('body', 'No description')[:2000]}"
            )
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="github_get_pr_diff", annotations={"readOnlyHint": True})
    async def github_get_pr_diff(owner: str, repo: str, pr_number: int) -> str:
        """Get the diff of a pull request."""
        try:
            headers = {
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.diff",
                "X-GitHub-Api-Version": "2022-11-28"
            }
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{GITHUB_API_URL}/repos/{owner}/{repo}/pulls/{pr_number}",
                    headers=headers
                )
                response.raise_for_status()
                diff = response.text[:15000]  # Limit size
                return f"# PR #{pr_number} Diff\n\n```diff\n{diff}\n```"
        except Exception as e:
            return _handle_error(e)

    # Search
    @mcp.tool(name="github_search_code", annotations={"readOnlyHint": True})
    async def github_search_code(query: str, per_page: int = 10) -> str:
        """Search for code across GitHub repositories."""
        try:
            result = await _gh_api(f"/search/code?q={query}&per_page={per_page}")
            items = result.get('items', [])
            lines = [
                f"# Code Search: {query}",
                "",
                f"Found {result.get('total_count', 0)} results",
                ""
            ]
            for i in items[:per_page]:
                repo = i.get('repository', {}).get('full_name', '')
                path = i.get('path', '')
                lines.append(f"- `{repo}` -> `{path}`")
            return "\n".join(lines)
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="github_search_repos", annotations={"readOnlyHint": True})
    async def github_search_repos(query: str, sort: str = "stars", per_page: int = 10) -> str:
        """Search for repositories on GitHub."""
        try:
            result = await _gh_api(f"/search/repositories?q={query}&sort={sort}&per_page={per_page}")
            items = result.get('items', [])
            lines = [
                f"# Repository Search: {query}",
                "",
                f"Found {result.get('total_count', 0)} results",
                ""
            ]
            for r in items[:per_page]:
                stars = r.get('stargazers_count', 0)
                lines.append(f"- **{r.get('full_name')}** ({stars:,}) - {r.get('description', '')[:50]}")
            return "\n".join(lines)
        except Exception as e:
            return _handle_error(e)

    # Workflows
    @mcp.tool(name="github_list_workflows", annotations={"readOnlyHint": True})
    async def github_list_workflows(owner: str, repo: str) -> str:
        """List GitHub Actions workflows in a repository."""
        try:
            result = await _gh_api(f"/repos/{owner}/{repo}/actions/workflows")
            workflows = result.get('workflows', [])
            lines = [f"# Workflows - {owner}/{repo}", ""]
            for w in workflows:
                state = "[active]" if w.get('state') == 'active' else "[inactive]"
                lines.append(f"- {state} **{w.get('name')}** (`{w.get('path')}`)")
            return "\n".join(lines)
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="github_list_workflow_runs", annotations={"readOnlyHint": True})
    async def github_list_workflow_runs(owner: str, repo: str, per_page: int = 10) -> str:
        """List recent workflow runs in a repository."""
        try:
            result = await _gh_api(f"/repos/{owner}/{repo}/actions/runs?per_page={per_page}")
            runs = result.get('workflow_runs', [])
            lines = [f"# Recent Workflow Runs - {owner}/{repo}", ""]
            for r in runs:
                status_icons = {"completed": "[done]", "in_progress": "[running]", "queued": "[queued]"}
                status_icon = status_icons.get(r.get('status'), "[?]")
                conclusion = r.get('conclusion', '') or r.get('status', '')
                lines.append(f"- {status_icon} **{r.get('name')}** ({conclusion}) - {r.get('head_branch')}")
            return "\n".join(lines)
        except Exception as e:
            return _handle_error(e)
