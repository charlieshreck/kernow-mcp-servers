# External MCP Server
# Consolidated external APIs MCP for Kernow homelab
# Uses Playwright base image for browser automation support

FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

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
COPY domains/external/pyproject.toml .
COPY domains/external/README.md .
COPY domains/external/src ./src

# Install dependencies
# Note: Installing packages directly, not editable mode for container builds
RUN pip install --no-cache-dir /app/shared
RUN pip install --no-cache-dir .

# Install Playwright browsers
RUN playwright install chromium --with-deps

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run as non-root (playwright image has pwuser)
USER pwuser

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
