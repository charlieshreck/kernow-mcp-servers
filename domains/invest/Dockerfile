# Invest MCP Server
# Investment advisory data and analysis MCP for Kernow homelab

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy shared utilities first (for Docker layer caching)
COPY shared/kernow_mcp_common /app/shared/kernow_mcp_common
COPY shared/pyproject.toml /app/shared/
COPY shared/README.md /app/shared/

# Copy domain-specific code
COPY domains/invest/pyproject.toml .
COPY domains/invest/src ./src

# Install dependencies
RUN pip install --no-cache-dir /app/shared
RUN pip install --no-cache-dir .

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run as non-root
RUN useradd -m -u 1000 mcp
USER mcp

# Expose port
EXPOSE 8000

# Environment defaults
ENV PORT=8000
ENV HOST=0.0.0.0

# Investmentology API defaults
ENV INVEST_API_URL=http://haute-banque.kernow.io

# Run the server
CMD ["python", "-m", "invest_mcp.server"]
