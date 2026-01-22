"""Reddit browsing and search tools."""

import logging
import urllib.parse
from datetime import datetime
from typing import Optional

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
REDDIT_BASE = "https://www.reddit.com"


async def _reddit_get(endpoint: str) -> dict:
    """Fetch from Reddit JSON API."""
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        headers = {"User-Agent": "KernowHomelabMCP/1.0"}
        url = f"{REDDIT_BASE}{endpoint}.json"
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()


def _handle_error(e: Exception) -> str:
    """Format error message."""
    if isinstance(e, httpx.HTTPStatusError):
        status = e.response.status_code
        if status == 404:
            return "Error: Subreddit or post not found."
        elif status == 403:
            return "Error: Subreddit is private or quarantined."
        elif status == 429:
            return "Error: Rate limited. Try again later."
        return f"Error: Reddit API returned {status}"
    return f"Error: {type(e).__name__}: {str(e)}"


def _format_post(post: dict, include_body: bool = False) -> str:
    """Format a Reddit post for display."""
    data = post.get("data", {})
    title = data.get("title", "No title")
    author = data.get("author", "[deleted]")
    score = data.get("score", 0)
    comments = data.get("num_comments", 0)
    subreddit = data.get("subreddit_name_prefixed", "")
    selftext = data.get("selftext", "")
    permalink = data.get("permalink", "")
    created = data.get("created_utc", 0)

    if created:
        created_str = datetime.utcfromtimestamp(created).strftime("%Y-%m-%d %H:%M")
    else:
        created_str = "Unknown"

    flair = data.get("link_flair_text", "")
    flair_str = f"[{flair}] " if flair else ""

    lines = [
        f"### {flair_str}{title}",
        f"**{subreddit}** | u/{author} | +{score:,} | {comments:,} comments | {created_str}",
    ]

    if include_body and selftext:
        body = selftext[:2000]
        if len(selftext) > 2000:
            body += "...\n\n[Content truncated]"
        lines.append("")
        lines.append(body)

    if not include_body:
        lines.append(f"[View](https://reddit.com{permalink})")

    return "\n".join(lines)


def register_tools(mcp: FastMCP):
    """Register Reddit tools with the MCP server."""

    # Browsing
    @mcp.tool(name="reddit_hot", annotations={"readOnlyHint": True})
    async def reddit_hot(subreddit: str = "all", limit: int = 10) -> str:
        """Get hot posts from a subreddit (or r/all)."""
        try:
            result = await _reddit_get(f"/r/{subreddit}/hot?limit={min(limit, 25)}")
            posts = result.get("data", {}).get("children", [])

            lines = [f"# Hot Posts: r/{subreddit}", ""]
            for post in posts:
                lines.append(_format_post(post))
                lines.append("")

            return "\n".join(lines) if posts else "No posts found."
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="reddit_new", annotations={"readOnlyHint": True})
    async def reddit_new(subreddit: str = "all", limit: int = 10) -> str:
        """Get newest posts from a subreddit."""
        try:
            result = await _reddit_get(f"/r/{subreddit}/new?limit={min(limit, 25)}")
            posts = result.get("data", {}).get("children", [])

            lines = [f"# New Posts: r/{subreddit}", ""]
            for post in posts:
                lines.append(_format_post(post))
                lines.append("")

            return "\n".join(lines) if posts else "No posts found."
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="reddit_top", annotations={"readOnlyHint": True})
    async def reddit_top(subreddit: str = "all", time: str = "day", limit: int = 10) -> str:
        """Get top posts from a subreddit. Time: hour, day, week, month, year, all."""
        try:
            result = await _reddit_get(f"/r/{subreddit}/top?t={time}&limit={min(limit, 25)}")
            posts = result.get("data", {}).get("children", [])

            lines = [f"# Top Posts ({time}): r/{subreddit}", ""]
            for post in posts:
                lines.append(_format_post(post))
                lines.append("")

            return "\n".join(lines) if posts else "No posts found."
        except Exception as e:
            return _handle_error(e)

    # Search
    @mcp.tool(name="reddit_search", annotations={"readOnlyHint": True})
    async def reddit_search(
        query: str,
        subreddit: str = "all",
        sort: str = "relevance",
        limit: int = 10
    ) -> str:
        """Search Reddit posts. Sort: relevance, hot, top, new, comments."""
        try:
            encoded_query = urllib.parse.quote(query)
            result = await _reddit_get(
                f"/r/{subreddit}/search?q={encoded_query}&sort={sort}&limit={min(limit, 25)}&restrict_sr=1"
            )
            posts = result.get("data", {}).get("children", [])

            lines = [f"# Search: '{query}' in r/{subreddit}", ""]
            for post in posts:
                lines.append(_format_post(post))
                lines.append("")

            return "\n".join(lines) if posts else "No results found."
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="reddit_search_subreddits", annotations={"readOnlyHint": True})
    async def reddit_search_subreddits(query: str, limit: int = 10) -> str:
        """Search for subreddits by name or topic."""
        try:
            encoded_query = urllib.parse.quote(query)
            result = await _reddit_get(f"/subreddits/search?q={encoded_query}&limit={min(limit, 25)}")
            subreddits = result.get("data", {}).get("children", [])

            lines = [f"# Subreddit Search: '{query}'", ""]
            for sr in subreddits:
                data = sr.get("data", {})
                name = data.get("display_name_prefixed", "")
                subscribers = data.get("subscribers", 0)
                desc = data.get("public_description", "")[:100]
                nsfw = "[NSFW] " if data.get("over18") else ""
                lines.append(f"- {nsfw}**{name}** ({subscribers:,} members)")
                if desc:
                    lines.append(f"  {desc}")

            return "\n".join(lines) if subreddits else "No subreddits found."
        except Exception as e:
            return _handle_error(e)

    # Post details
    @mcp.tool(name="reddit_post", annotations={"readOnlyHint": True})
    async def reddit_post(subreddit: str, post_id: str) -> str:
        """Get a specific post with its content."""
        try:
            result = await _reddit_get(f"/r/{subreddit}/comments/{post_id}")
            if not result or len(result) < 1:
                return "Post not found."

            post = result[0].get("data", {}).get("children", [{}])[0]
            return _format_post(post, include_body=True)
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="reddit_comments", annotations={"readOnlyHint": True})
    async def reddit_comments(subreddit: str, post_id: str, limit: int = 20) -> str:
        """Get comments from a Reddit post."""
        try:
            result = await _reddit_get(f"/r/{subreddit}/comments/{post_id}?limit={min(limit, 50)}")
            if not result or len(result) < 2:
                return "Comments not found."

            post = result[0].get("data", {}).get("children", [{}])[0]
            comments = result[1].get("data", {}).get("children", [])

            lines = [_format_post(post, include_body=True), "", "---", "", "## Comments", ""]

            def format_comment(comment, depth=0):
                """Recursively format comments."""
                data = comment.get("data", {})
                if data.get("body") is None:
                    return []

                author = data.get("author", "[deleted]")
                score = data.get("score", 0)
                body = data.get("body", "")[:500]
                indent = "  " * depth

                result_lines = [f"{indent}**u/{author}** (+{score})", f"{indent}{body}", ""]

                replies = data.get("replies")
                if isinstance(replies, dict):
                    reply_children = replies.get("data", {}).get("children", [])
                    for reply in reply_children[:3]:
                        result_lines.extend(format_comment(reply, depth + 1))

                return result_lines

            for comment in comments[:limit]:
                lines.extend(format_comment(comment))

            return "\n".join(lines)
        except Exception as e:
            return _handle_error(e)

    # Subreddit info
    @mcp.tool(name="reddit_subreddit_info", annotations={"readOnlyHint": True})
    async def reddit_subreddit_info(subreddit: str) -> str:
        """Get information about a subreddit."""
        try:
            result = await _reddit_get(f"/r/{subreddit}/about")
            data = result.get("data", {})

            name = data.get("display_name_prefixed", subreddit)
            subscribers = data.get("subscribers", 0)
            active = data.get("active_user_count", 0)
            description = data.get("public_description", "No description")
            created = data.get("created_utc", 0)
            nsfw = "[NSFW]" if data.get("over18") else ""

            if created:
                created_str = datetime.utcfromtimestamp(created).strftime("%Y-%m-%d")
            else:
                created_str = "Unknown"

            return (
                f"# {name} {nsfw}\n\n"
                f"**Subscribers:** {subscribers:,}\n"
                f"**Active Users:** {active:,}\n"
                f"**Created:** {created_str}\n\n"
                f"---\n\n{description}"
            )
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="reddit_subreddit_rules", annotations={"readOnlyHint": True})
    async def reddit_subreddit_rules(subreddit: str) -> str:
        """Get the rules of a subreddit."""
        try:
            result = await _reddit_get(f"/r/{subreddit}/about/rules")
            rules = result.get("rules", [])

            lines = [f"# Rules: r/{subreddit}", ""]
            for i, rule in enumerate(rules, 1):
                name = rule.get("short_name", "")
                desc = rule.get("description", "")[:200]
                lines.append(f"**{i}. {name}**")
                if desc:
                    lines.append(desc)
                lines.append("")

            return "\n".join(lines) if rules else "No rules found."
        except Exception as e:
            return _handle_error(e)

    # User info
    @mcp.tool(name="reddit_user_info", annotations={"readOnlyHint": True})
    async def reddit_user_info(username: str) -> str:
        """Get information about a Reddit user."""
        try:
            result = await _reddit_get(f"/user/{username}/about")
            data = result.get("data", {})

            name = data.get("name", username)
            karma_post = data.get("link_karma", 0)
            karma_comment = data.get("comment_karma", 0)
            created = data.get("created_utc", 0)

            if created:
                created_str = datetime.utcfromtimestamp(created).strftime("%Y-%m-%d")
            else:
                created_str = "Unknown"

            return (
                f"# u/{name}\n\n"
                f"**Post Karma:** {karma_post:,}\n"
                f"**Comment Karma:** {karma_comment:,}\n"
                f"**Account Created:** {created_str}"
            )
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="reddit_user_posts", annotations={"readOnlyHint": True})
    async def reddit_user_posts(username: str, limit: int = 10) -> str:
        """Get recent posts by a user."""
        try:
            result = await _reddit_get(f"/user/{username}/submitted?limit={min(limit, 25)}")
            posts = result.get("data", {}).get("children", [])

            lines = [f"# Posts by u/{username}", ""]
            for post in posts:
                lines.append(_format_post(post))
                lines.append("")

            return "\n".join(lines) if posts else "No posts found."
        except Exception as e:
            return _handle_error(e)
