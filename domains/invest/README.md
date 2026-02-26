# Invest MCP

Investment advisory data and analysis MCP server for the Kernow homelab. Proxies requests to the Investmentology platform running on Haute Banque LXC.

## Tools (12)

| Tool | Description |
|------|-------------|
| `invest_get_portfolio` | Current portfolio positions, P&L, allocation |
| `invest_get_portfolio_alerts` | Active alerts (stop-loss, rebalancing) |
| `invest_get_portfolio_balance` | Cash balance and buying power |
| `invest_get_portfolio_briefing` | Daily briefing with market context |
| `invest_get_portfolio_timeline` | Performance timeline for charting |
| `invest_get_watchlist` | Stocks monitored for entry |
| `invest_get_stock_analysis` | Per-stock fundamentals, technicals, agent scores |
| `invest_get_stock_signals` | Trading signals for a stock |
| `invest_get_recommendations` | Buy/sell/hold recommendations |
| `invest_get_decisions` | Decision log with rationale and outcomes |
| `invest_get_quant_gate_latest` | Quantitative screening (Magic Formula) |
| `invest_get_system_health` | System health status |

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `INVEST_API_URL` | `http://haute-banque.kernow.io` | Investmentology API base URL |
| `INVEST_API_TOKEN` | (empty) | Bearer token for API authentication |
| `PORT` | `8000` | Server listen port |
| `HOST` | `0.0.0.0` | Server listen address |

## Development

```bash
cd domains/invest
uv run python -m invest_mcp.server
```

## Build

```bash
# From mcp-servers root
docker build -f domains/invest/Dockerfile -t ghcr.io/charlieshreck/mcp-invest:latest .
```
