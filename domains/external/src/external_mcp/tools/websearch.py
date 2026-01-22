"""Web search tools using SearXNG."""

import os
import re
import logging
from typing import List, Optional
from datetime import datetime

import httpx
from fastmcp import FastMCP
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Configuration
SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://searxng.ai-platform.svc.cluster.local:8080")


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str
    engine: Optional[str] = None
    published_date: Optional[str] = None


class PageContent(BaseModel):
    url: str
    title: str
    content: str
    content_type: str
    word_count: int
    fetched_at: str


class NewsResult(BaseModel):
    title: str
    url: str
    snippet: str
    source: Optional[str] = None
    published_date: Optional[str] = None


class ImageResult(BaseModel):
    title: str
    url: str
    thumbnail_url: Optional[str] = None
    source: Optional[str] = None


async def _searxng_search(
    query: str,
    categories: str = "general",
    engines: Optional[str] = None,
    num_results: int = 10,
    time_range: Optional[str] = None
) -> List[dict]:
    """Execute search against SearXNG."""
    params = {"q": query, "format": "json", "categories": categories}
    if engines:
        params["engines"] = engines
    if time_range:
        params["time_range"] = time_range

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(f"{SEARXNG_URL}/search", params=params)
            response.raise_for_status()
            data = response.json()
            return data.get("results", [])[:num_results]
        except Exception as e:
            logger.error(f"SearXNG search failed: {e}")
            return []


async def _fetch_page_content(url: str, max_length: int = 50000) -> dict:
    """Fetch and convert web page to markdown."""
    try:
        from bs4 import BeautifulSoup
        import html2text

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            headers = {"User-Agent": "Mozilla/5.0 (compatible; AgenticBot/1.0)"}
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")

            if "text/html" in content_type:
                soup = BeautifulSoup(response.text, "html.parser")
                for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                    tag.decompose()
                title = soup.title.string if soup.title else ""
                h = html2text.HTML2Text()
                h.ignore_links = False
                h.ignore_images = True
                h.body_width = 0
                content = h.handle(str(soup))
                if len(content) > max_length:
                    content = content[:max_length] + "\n\n[Content truncated...]"
                return {
                    "title": title.strip() if title else "",
                    "content": content.strip(),
                    "content_type": "markdown",
                    "word_count": len(content.split())
                }
            else:
                text = response.text[:max_length]
                return {
                    "title": "",
                    "content": text,
                    "content_type": "text",
                    "word_count": len(text.split())
                }
    except Exception as e:
        logger.error(f"Failed to fetch {url}: {e}")
        return {"title": "", "content": f"Error: {str(e)}", "content_type": "error", "word_count": 0}


def _is_safe_url(url: str) -> bool:
    """Check if URL is safe to fetch (not internal network)."""
    internal_patterns = [
        r"^https?://10\.",
        r"^https?://192\.168\.",
        r"^https?://172\.(1[6-9]|2[0-9]|3[0-1])\.",
        r"^https?://127\.",
        r"^https?://localhost",
        r"\.svc\.cluster\.local",
        r"\.internal"
    ]
    for pattern in internal_patterns:
        if re.search(pattern, url, re.IGNORECASE):
            return False
    return url.startswith("http://") or url.startswith("https://")


def register_tools(mcp: FastMCP):
    """Register web search tools with the MCP server."""

    @mcp.tool(name="websearch_search")
    async def websearch_search(
        query: str,
        num_results: int = 10,
        engines: Optional[str] = None,
        time_range: Optional[str] = None
    ) -> List[SearchResult]:
        """Search the web using SearXNG (aggregates Google, Bing, DuckDuckGo, etc.)."""
        num_results = min(num_results, 50)
        results = await _searxng_search(query, "general", engines, num_results, time_range)
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("content", ""),
                engine=r.get("engine"),
                published_date=r.get("publishedDate")
            )
            for r in results
        ]

    @mcp.tool(name="websearch_get_page_content")
    async def websearch_get_page_content(url: str, max_length: int = 50000) -> PageContent:
        """Fetch and extract text content from a web page (converted to markdown)."""
        if not _is_safe_url(url):
            return PageContent(
                url=url,
                title="",
                content="Error: URL blocked (internal network)",
                content_type="error",
                word_count=0,
                fetched_at=datetime.utcnow().isoformat()
            )
        result = await _fetch_page_content(url, max_length)
        return PageContent(
            url=url,
            title=result["title"],
            content=result["content"],
            content_type=result["content_type"],
            word_count=result["word_count"],
            fetched_at=datetime.utcnow().isoformat()
        )

    @mcp.tool(name="websearch_search_news")
    async def websearch_search_news(
        query: str,
        num_results: int = 10,
        time_range: str = "week"
    ) -> List[NewsResult]:
        """Search for recent news articles."""
        num_results = min(num_results, 50)
        results = await _searxng_search(query, "news", None, num_results, time_range)
        return [
            NewsResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("content", ""),
                source=r.get("engine"),
                published_date=r.get("publishedDate")
            )
            for r in results
        ]

    @mcp.tool(name="websearch_search_images")
    async def websearch_search_images(query: str, num_results: int = 10) -> List[ImageResult]:
        """Search for images."""
        num_results = min(num_results, 50)
        results = await _searxng_search(query, "images", None, num_results, None)
        return [
            ImageResult(
                title=r.get("title", ""),
                url=r.get("img_src", r.get("url", "")),
                thumbnail_url=r.get("thumbnail_src"),
                source=r.get("engine")
            )
            for r in results
        ]

    @mcp.tool(name="websearch_search_and_fetch")
    async def websearch_search_and_fetch(query: str, num_results: int = 3) -> dict:
        """Search and automatically fetch content from top results."""
        num_results = min(num_results, 5)
        search_results = await _searxng_search(query, "general", None, num_results, None)
        fetched = []
        for r in search_results:
            url = r.get("url", "")
            if url and _is_safe_url(url):
                content = await _fetch_page_content(url, 20000)
                fetched.append({
                    "title": r.get("title", ""),
                    "url": url,
                    "snippet": r.get("content", ""),
                    "full_content": content["content"],
                    "word_count": content["word_count"]
                })
        return {"query": query, "results_fetched": len(fetched), "results": fetched}
