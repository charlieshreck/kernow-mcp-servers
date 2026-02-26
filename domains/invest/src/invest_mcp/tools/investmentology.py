"""Investmentology investment advisory tools."""

import os
import logging

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Configuration
INVEST_API_URL = os.environ.get("INVEST_API_URL", "http://haute-banque.kernow.io")
INVEST_API_TOKEN = os.environ.get("INVEST_API_TOKEN", "")


async def _get(path: str, params: dict = None) -> dict:
    """Make authenticated GET request to Investmentology API."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        headers = {"Authorization": f"Bearer {INVEST_API_TOKEN}"} if INVEST_API_TOKEN else {}
        url = f"{INVEST_API_URL}/api/invest{path}"
        resp = await client.get(url, headers=headers, params=params)
        resp.raise_for_status()
        return resp.json()


async def _post(path: str, data: dict = None) -> dict:
    """Make authenticated POST request to Investmentology API."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        headers = {"Authorization": f"Bearer {INVEST_API_TOKEN}"} if INVEST_API_TOKEN else {}
        url = f"{INVEST_API_URL}/api/invest{path}"
        resp = await client.post(url, headers=headers, json=data)
        resp.raise_for_status()
        return resp.json()


async def get_health() -> dict:
    """Get system health for health checks."""
    try:
        return await _get("/system/health")
    except Exception as e:
        return {"error": str(e)}


def register_tools(mcp: FastMCP):
    """Register Investmentology tools with the MCP server."""

    @mcp.tool()
    async def invest_get_portfolio() -> dict:
        """Get current portfolio positions, P&L, and allocation."""
        try:
            return await _get("/portfolio")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def invest_get_portfolio_alerts() -> dict:
        """Get active portfolio alerts (stop-loss triggers, rebalancing needed, etc.)."""
        try:
            return await _get("/portfolio/alerts")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def invest_get_portfolio_balance() -> dict:
        """Get portfolio cash balance and buying power."""
        try:
            return await _get("/portfolio/balance")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def invest_get_portfolio_briefing() -> dict:
        """Get daily portfolio briefing with market context and action items."""
        try:
            return await _get("/portfolio/briefing")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def invest_get_portfolio_timeline() -> dict:
        """Get portfolio performance timeline for charting."""
        try:
            return await _get("/portfolio/timeline")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def invest_get_watchlist() -> dict:
        """Get current watchlist of stocks being monitored for entry."""
        try:
            return await _get("/watchlist")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def invest_get_stock_analysis(ticker: str) -> dict:
        """Get comprehensive analysis for a specific stock including fundamentals, technicals, and agent scores.

        Args:
            ticker: Stock ticker symbol (e.g., AAPL, MSFT, GOOGL)
        """
        try:
            return await _get(f"/stock/{ticker}")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def invest_get_stock_signals(ticker: str) -> dict:
        """Get trading signals for a specific stock.

        Args:
            ticker: Stock ticker symbol (e.g., AAPL, MSFT, GOOGL)
        """
        try:
            return await _get(f"/stock/{ticker}/signals")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def invest_get_recommendations() -> dict:
        """Get current buy/sell/hold recommendations from the analysis pipeline."""
        try:
            return await _get("/recommendations")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def invest_get_decisions(limit: int = 20) -> dict:
        """Get recent investment decisions with rationale and outcomes.

        Args:
            limit: Maximum number of decisions to return (default 20)
        """
        try:
            return await _get("/decisions", params={"limit": limit})
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def invest_get_quant_gate_latest() -> dict:
        """Get latest quantitative screening results (Magic Formula rankings)."""
        try:
            return await _get("/quant-gate/latest")
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    async def invest_get_system_health() -> dict:
        """Get Investmentology system health status."""
        try:
            return await _get("/system/health")
        except Exception as e:
            return {"error": str(e)}
