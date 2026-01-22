# External MCP Server
# Consolidated external APIs MCP for Kernow homelab

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (including lxml build deps and Playwright deps)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libxml2-dev \
    libxslt1-dev \
    gcc \
    python3-dev \
    # Playwright dependencies
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Copy shared utilities first (for Docker layer caching)
COPY shared/kernow_mcp_common /app/shared/kernow_mcp_common
COPY shared/pyproject.toml /app/shared/
COPY shared/README.md /app/shared/

# Copy domain-specific code
COPY domains/external/pyproject.toml .
COPY domains/external/README.md .
COPY domains/external/src ./src

# Install dependencies
RUN pip install --no-cache-dir /app/shared
RUN pip install --no-cache-dir .

# Install Playwright browsers
RUN playwright install chromium

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
ENV BROWSER_TYPE=chromium
ENV HEADLESS=true
ENV VIEWPORT_WIDTH=1920
ENV VIEWPORT_HEIGHT=1080
ENV DEFAULT_TIMEOUT=30000

# Run the server
CMD ["python", "-m", "external_mcp.server"]
