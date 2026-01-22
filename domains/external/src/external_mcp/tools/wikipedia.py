"""Wikipedia knowledge retrieval tools."""

import re
import logging
import urllib.parse
from datetime import datetime

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
WIKIPEDIA_API = "https://en.wikipedia.org/api/rest_v1"
WIKIPEDIA_ACTION_API = "https://en.wikipedia.org/w/api.php"


async def _wiki_rest(endpoint: str) -> dict:
    """Call Wikipedia REST API."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        headers = {"User-Agent": "KernowHomelabMCP/1.0"}
        response = await client.get(f"{WIKIPEDIA_API}{endpoint}", headers=headers)
        response.raise_for_status()
        return response.json()


async def _wiki_action(params: dict) -> dict:
    """Call Wikipedia Action API."""
    params["format"] = "json"
    async with httpx.AsyncClient(timeout=30.0) as client:
        headers = {"User-Agent": "KernowHomelabMCP/1.0"}
        response = await client.get(WIKIPEDIA_ACTION_API, params=params, headers=headers)
        response.raise_for_status()
        return response.json()


def _handle_error(e: Exception) -> str:
    """Format error message."""
    if isinstance(e, httpx.HTTPStatusError):
        return f"Error: Wikipedia API returned {e.response.status_code}"
    return f"Error: {type(e).__name__}: {str(e)}"


def register_tools(mcp: FastMCP):
    """Register Wikipedia tools with the MCP server."""

    # Search
    @mcp.tool(name="wikipedia_search", annotations={"readOnlyHint": True})
    async def wikipedia_search(query: str, limit: int = 10) -> str:
        """Search Wikipedia for articles matching a query."""
        try:
            params = {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": min(limit, 20),
                "srprop": "snippet|titlesnippet"
            }
            result = await _wiki_action(params)
            items = result.get("query", {}).get("search", [])

            lines = [f"# Wikipedia Search: {query}", "", f"Found {len(items)} results", ""]
            for item in items:
                title = item.get("title", "")
                snippet = item.get("snippet", "")
                snippet = snippet.replace('<span class="searchmatch">', '**').replace('</span>', '**')
                snippet = re.sub(r'<[^>]+>', '', snippet)
                lines.append(f"## {title}")
                lines.append(f"{snippet[:200]}...")
                lines.append("")
            return "\n".join(lines)
        except Exception as e:
            return _handle_error(e)

    # Article retrieval
    @mcp.tool(name="wikipedia_summary", annotations={"readOnlyHint": True})
    async def wikipedia_summary(title: str) -> str:
        """Get a summary of a Wikipedia article."""
        try:
            encoded_title = urllib.parse.quote(title.replace(" ", "_"))
            result = await _wiki_rest(f"/page/summary/{encoded_title}")

            extract = result.get("extract", "No summary available.")
            page_title = result.get("title", title)
            description = result.get("description", "")
            url = result.get("content_urls", {}).get("desktop", {}).get("page", "")

            return (
                f"# {page_title}\n\n"
                f"*{description}*\n\n"
                f"{extract}\n\n"
                f"**Read more:** {url}"
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return f"Article not found: {title}. Try searching with wikipedia_search first."
            return _handle_error(e)
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="wikipedia_article", annotations={"readOnlyHint": True})
    async def wikipedia_article(title: str, max_length: int = 5000) -> str:
        """Get the full text content of a Wikipedia article."""
        try:
            encoded_title = urllib.parse.quote(title.replace(" ", "_"))

            async with httpx.AsyncClient(timeout=30.0) as client:
                headers = {"User-Agent": "KernowHomelabMCP/1.0"}
                response = await client.get(
                    f"{WIKIPEDIA_API}/page/mobile-html/{encoded_title}",
                    headers=headers
                )
                response.raise_for_status()
                html = response.text

            # Extract text from HTML
            html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
            html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)
            text = re.sub(r'<[^>]+>', ' ', html)
            text = re.sub(r'\s+', ' ', text).strip()

            if len(text) > max_length:
                text = text[:max_length] + "...\n\n[Content truncated]"

            return f"# {title}\n\n{text}"
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return f"Article not found: {title}. Try searching with wikipedia_search first."
            return _handle_error(e)
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="wikipedia_sections", annotations={"readOnlyHint": True})
    async def wikipedia_sections(title: str) -> str:
        """Get the table of contents (sections) of a Wikipedia article."""
        try:
            params = {
                "action": "parse",
                "page": title,
                "prop": "sections"
            }
            result = await _wiki_action(params)
            sections = result.get("parse", {}).get("sections", [])

            lines = [f"# Sections: {title}", ""]
            for s in sections:
                level = int(s.get("toclevel", 1))
                indent = "  " * (level - 1)
                number = s.get("number", "")
                name = s.get("line", "")
                lines.append(f"{indent}- {number} {name}")

            return "\n".join(lines) if len(lines) > 2 else "No sections found."
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="wikipedia_section_content", annotations={"readOnlyHint": True})
    async def wikipedia_section_content(title: str, section: int) -> str:
        """Get the content of a specific section of a Wikipedia article."""
        try:
            params = {
                "action": "parse",
                "page": title,
                "prop": "text",
                "section": str(section)
            }
            result = await _wiki_action(params)
            html = result.get("parse", {}).get("text", {}).get("*", "")

            html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
            html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)
            text = re.sub(r'<[^>]+>', ' ', html)
            text = re.sub(r'\s+', ' ', text).strip()

            section_title = result.get("parse", {}).get("title", title)
            return f"# {section_title} - Section {section}\n\n{text[:5000]}"
        except Exception as e:
            return _handle_error(e)

    # Related content
    @mcp.tool(name="wikipedia_links", annotations={"readOnlyHint": True})
    async def wikipedia_links(title: str, limit: int = 20) -> str:
        """Get links from a Wikipedia article to other articles."""
        try:
            params = {
                "action": "query",
                "titles": title,
                "prop": "links",
                "pllimit": min(limit, 50),
                "plnamespace": "0"
            }
            result = await _wiki_action(params)
            pages = result.get("query", {}).get("pages", {})

            lines = [f"# Links from: {title}", ""]
            for page_id, page_data in pages.items():
                links = page_data.get("links", [])
                for link in links:
                    lines.append(f"- {link.get('title', '')}")

            return "\n".join(lines) if len(lines) > 2 else "No links found."
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="wikipedia_related", annotations={"readOnlyHint": True})
    async def wikipedia_related(title: str) -> str:
        """Get related articles for a Wikipedia article."""
        try:
            encoded_title = urllib.parse.quote(title.replace(" ", "_"))
            result = await _wiki_rest(f"/page/related/{encoded_title}")

            pages = result.get("pages", [])
            lines = [f"# Related to: {title}", ""]
            for p in pages[:15]:
                p_title = p.get("title", "")
                desc = p.get("description", "")[:80]
                lines.append(f"- **{p_title}** - {desc}")

            return "\n".join(lines) if len(lines) > 2 else "No related articles found."
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="wikipedia_categories", annotations={"readOnlyHint": True})
    async def wikipedia_categories(title: str) -> str:
        """Get categories of a Wikipedia article."""
        try:
            params = {
                "action": "query",
                "titles": title,
                "prop": "categories",
                "cllimit": "50"
            }
            result = await _wiki_action(params)
            pages = result.get("query", {}).get("pages", {})

            lines = [f"# Categories: {title}", ""]
            for page_id, page_data in pages.items():
                categories = page_data.get("categories", [])
                for cat in categories:
                    cat_title = cat.get("title", "").replace("Category:", "")
                    lines.append(f"- {cat_title}")

            return "\n".join(lines) if len(lines) > 2 else "No categories found."
        except Exception as e:
            return _handle_error(e)

    # Special pages
    @mcp.tool(name="wikipedia_random", annotations={"readOnlyHint": True})
    async def wikipedia_random() -> str:
        """Get a random Wikipedia article summary."""
        try:
            result = await _wiki_rest("/page/random/summary")
            return await wikipedia_summary(result.get("title", ""))
        except Exception as e:
            return _handle_error(e)

    @mcp.tool(name="wikipedia_on_this_day", annotations={"readOnlyHint": True})
    async def wikipedia_on_this_day() -> str:
        """Get notable events that happened on this day in history."""
        try:
            today = datetime.now()
            result = await _wiki_rest(f"/feed/onthisday/events/{today.month}/{today.day}")

            events = result.get("events", [])[:10]
            lines = [f"# On This Day: {today.strftime('%B %d')}", ""]
            for e in events:
                year = e.get("year", "")
                text = e.get("text", "")[:150]
                lines.append(f"- **{year}**: {text}")

            return "\n".join(lines)
        except Exception as e:
            return _handle_error(e)
