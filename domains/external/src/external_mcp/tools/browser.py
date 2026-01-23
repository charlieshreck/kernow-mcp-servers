"""Browser automation tools using Playwright."""

import os
import base64
import logging
import asyncio
from typing import Optional, Dict, List
from datetime import datetime

from fastmcp import FastMCP
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Configuration
BROWSER_TYPE = os.environ.get("BROWSER_TYPE", "chromium")
HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"
DEFAULT_TIMEOUT = int(os.environ.get("DEFAULT_TIMEOUT", "30000"))
VIEWPORT_WIDTH = int(os.environ.get("VIEWPORT_WIDTH", "1920"))
VIEWPORT_HEIGHT = int(os.environ.get("VIEWPORT_HEIGHT", "1080"))
# Extra browser args (comma-separated), e.g., "--no-sandbox,--disable-dev-shm-usage"
BROWSER_ARGS = os.environ.get("BROWSER_ARGS", "--no-sandbox,--disable-dev-shm-usage,--disable-gpu")


class NavigateResult(BaseModel):
    url: str
    title: str
    status: int
    load_time_ms: int


class Screenshot(BaseModel):
    data: str
    width: int
    height: int
    timestamp: str


class ActionResult(BaseModel):
    success: bool
    message: str
    timestamp: str


class PageContent(BaseModel):
    url: str
    title: str
    html: Optional[str] = None
    text: Optional[str] = None
    word_count: int


class BrowserManager:
    """Singleton browser manager for connection pooling."""

    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self._lock = asyncio.Lock()

    async def ensure_browser(self):
        """Ensure browser is running, start if needed."""
        async with self._lock:
            if self.page is None or self.page.is_closed():
                await self._start_browser()
            return self.page

    async def _start_browser(self):
        """Start browser with configuration."""
        from playwright.async_api import async_playwright

        if self.playwright is None:
            self.playwright = await async_playwright().start()

        browser_launcher = getattr(self.playwright, BROWSER_TYPE)
        # Parse browser args from environment
        launch_args = [arg.strip() for arg in BROWSER_ARGS.split(",") if arg.strip()]
        # Add new headless mode flag for better container compatibility
        if HEADLESS and "--headless=new" not in launch_args:
            launch_args.append("--headless=new")
        self.browser = await browser_launcher.launch(headless=HEADLESS, args=launch_args)
        self.context = await self.browser.new_context(
            viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        self.page = await self.context.new_page()
        self.page.set_default_timeout(DEFAULT_TIMEOUT)
        logger.info(f"Browser started: {BROWSER_TYPE}, headless={HEADLESS}")

    async def close(self):
        """Close browser and cleanup."""
        async with self._lock:
            if self.page:
                await self.page.close()
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
            self.page = self.context = self.browser = self.playwright = None


# Global browser manager instance
browser_manager = BrowserManager()


def register_tools(mcp: FastMCP):
    """Register browser automation tools with the MCP server."""

    @mcp.tool(name="browser_navigate")
    async def browser_navigate(url: str, wait_until: str = "networkidle") -> NavigateResult:
        """Navigate browser to a URL."""
        page = await browser_manager.ensure_browser()
        start = datetime.utcnow()
        try:
            resp = await page.goto(url, wait_until=wait_until)
            return NavigateResult(
                url=page.url,
                title=await page.title(),
                status=resp.status if resp else 0,
                load_time_ms=int((datetime.utcnow() - start).total_seconds() * 1000)
            )
        except Exception as e:
            logger.error(f"Navigate failed: {e}")
            return NavigateResult(url=url, title="", status=0, load_time_ms=0)

    @mcp.tool(name="browser_screenshot")
    async def browser_screenshot(full_page: bool = False) -> Screenshot:
        """Take a screenshot of current page."""
        page = await browser_manager.ensure_browser()
        try:
            data = base64.b64encode(
                await page.screenshot(full_page=full_page, type="png")
            ).decode()
            vp = page.viewport_size
            return Screenshot(
                data=data,
                width=vp["width"] if vp else VIEWPORT_WIDTH,
                height=vp["height"] if vp else VIEWPORT_HEIGHT,
                timestamp=datetime.utcnow().isoformat()
            )
        except Exception as e:
            logger.error(f"Screenshot failed: {e}")
            return Screenshot(data="", width=0, height=0, timestamp=datetime.utcnow().isoformat())

    @mcp.tool(name="browser_click")
    async def browser_click(selector: str) -> ActionResult:
        """Click element by CSS selector."""
        page = await browser_manager.ensure_browser()
        try:
            await page.click(selector)
            return ActionResult(
                success=True,
                message=f"Clicked: {selector}",
                timestamp=datetime.utcnow().isoformat()
            )
        except Exception as e:
            return ActionResult(
                success=False,
                message=str(e),
                timestamp=datetime.utcnow().isoformat()
            )

    @mcp.tool(name="browser_click_coordinates")
    async def browser_click_coordinates(x: int, y: int) -> ActionResult:
        """Click at screen coordinates."""
        page = await browser_manager.ensure_browser()
        try:
            await page.mouse.click(x, y)
            return ActionResult(
                success=True,
                message=f"Clicked ({x},{y})",
                timestamp=datetime.utcnow().isoformat()
            )
        except Exception as e:
            return ActionResult(
                success=False,
                message=str(e),
                timestamp=datetime.utcnow().isoformat()
            )

    @mcp.tool(name="browser_type_text")
    async def browser_type_text(selector: str, text: str, clear_first: bool = True) -> ActionResult:
        """Type text into input element."""
        page = await browser_manager.ensure_browser()
        try:
            if clear_first:
                await page.fill(selector, text)
            else:
                await page.type(selector, text)
            return ActionResult(
                success=True,
                message=f"Typed into: {selector}",
                timestamp=datetime.utcnow().isoformat()
            )
        except Exception as e:
            return ActionResult(
                success=False,
                message=str(e),
                timestamp=datetime.utcnow().isoformat()
            )

    @mcp.tool(name="browser_press_key")
    async def browser_press_key(key: str) -> ActionResult:
        """Press keyboard key (Enter, Tab, Escape, Control+a, etc.)."""
        page = await browser_manager.ensure_browser()
        try:
            await page.keyboard.press(key)
            return ActionResult(
                success=True,
                message=f"Pressed: {key}",
                timestamp=datetime.utcnow().isoformat()
            )
        except Exception as e:
            return ActionResult(
                success=False,
                message=str(e),
                timestamp=datetime.utcnow().isoformat()
            )

    @mcp.tool(name="browser_scroll")
    async def browser_scroll(direction: str = "down", amount: int = 500) -> ActionResult:
        """Scroll page (up/down/left/right)."""
        page = await browser_manager.ensure_browser()
        try:
            dx, dy = 0, 0
            if direction == "down":
                dy = amount
            elif direction == "up":
                dy = -amount
            elif direction == "right":
                dx = amount
            elif direction == "left":
                dx = -amount
            await page.mouse.wheel(dx, dy)
            return ActionResult(
                success=True,
                message=f"Scrolled {direction}",
                timestamp=datetime.utcnow().isoformat()
            )
        except Exception as e:
            return ActionResult(
                success=False,
                message=str(e),
                timestamp=datetime.utcnow().isoformat()
            )

    @mcp.tool(name="browser_get_page_content")
    async def browser_get_page_content(include_html: bool = False) -> PageContent:
        """Get current page content."""
        page = await browser_manager.ensure_browser()
        try:
            text = await page.inner_text("body")
            html = await page.content() if include_html else None
            return PageContent(
                url=page.url,
                title=await page.title(),
                html=html,
                text=text,
                word_count=len(text.split())
            )
        except Exception as e:
            return PageContent(url="", title="", text=str(e), word_count=0)

    @mcp.tool(name="browser_evaluate_js")
    async def browser_evaluate_js(script: str) -> dict:
        """Execute JavaScript on page."""
        page = await browser_manager.ensure_browser()
        try:
            result = await page.evaluate(script)
            return {"success": True, "result": result}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool(name="browser_wait_for_selector")
    async def browser_wait_for_selector(
        selector: str,
        timeout: int = 30000,
        state: str = "visible"
    ) -> ActionResult:
        """Wait for element to appear."""
        page = await browser_manager.ensure_browser()
        try:
            await page.wait_for_selector(selector, timeout=timeout, state=state)
            return ActionResult(
                success=True,
                message=f"Found: {selector}",
                timestamp=datetime.utcnow().isoformat()
            )
        except Exception as e:
            return ActionResult(
                success=False,
                message=str(e),
                timestamp=datetime.utcnow().isoformat()
            )

    @mcp.tool(name="browser_fill_form")
    async def browser_fill_form(fields: Dict[str, str]) -> ActionResult:
        """Fill multiple form fields."""
        page = await browser_manager.ensure_browser()
        try:
            for selector, value in fields.items():
                await page.fill(selector, value)
            return ActionResult(
                success=True,
                message=f"Filled {len(fields)} fields",
                timestamp=datetime.utcnow().isoformat()
            )
        except Exception as e:
            return ActionResult(
                success=False,
                message=str(e),
                timestamp=datetime.utcnow().isoformat()
            )

    @mcp.tool(name="browser_get_element_text")
    async def browser_get_element_text(selector: str) -> str:
        """Get text content of element."""
        page = await browser_manager.ensure_browser()
        try:
            return await page.inner_text(selector)
        except Exception as e:
            return f"Error: {e}"

    @mcp.tool(name="browser_get_all_links")
    async def browser_get_all_links() -> List[Dict[str, str]]:
        """Get all links on page."""
        page = await browser_manager.ensure_browser()
        try:
            return await page.evaluate(
                "() => Array.from(document.querySelectorAll('a[href]')).map("
                "a => ({href: a.href, text: a.innerText.trim().substring(0,100)}))"
            )
        except Exception:
            return []

    @mcp.tool(name="browser_go_back")
    async def browser_go_back() -> ActionResult:
        """Navigate back in history."""
        page = await browser_manager.ensure_browser()
        try:
            await page.go_back()
            return ActionResult(
                success=True,
                message="Navigated back",
                timestamp=datetime.utcnow().isoformat()
            )
        except Exception as e:
            return ActionResult(
                success=False,
                message=str(e),
                timestamp=datetime.utcnow().isoformat()
            )

    @mcp.tool(name="browser_reload_page")
    async def browser_reload_page() -> ActionResult:
        """Reload current page."""
        page = await browser_manager.ensure_browser()
        try:
            await page.reload()
            return ActionResult(
                success=True,
                message="Reloaded",
                timestamp=datetime.utcnow().isoformat()
            )
        except Exception as e:
            return ActionResult(
                success=False,
                message=str(e),
                timestamp=datetime.utcnow().isoformat()
            )
