# Home MCP Server
# Consolidated smart home, network, and IoT management

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
COPY domains/home/pyproject.toml .
COPY domains/home/src ./src

# Install dependencies
RUN pip install --no-cache-dir /app/shared
RUN pip install --no-cache-dir .

# Create data directory for Tasmota device registry
RUN mkdir -p /data

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run as non-root
RUN useradd -m -u 1000 mcp && chown -R mcp:mcp /data
USER mcp

# Expose port
EXPOSE 8000

# Environment defaults
ENV PORT=8000
ENV HOST=0.0.0.0

# Home Assistant defaults
ENV HA_URL=http://homeassistant.local:8123

# UniFi defaults
ENV UNIFI_HOST=https://10.10.0.154:11443
ENV UNIFI_SITE=default

# AdGuard defaults
ENV ADGUARD_HOST=http://10.10.0.1:3000
ENV ADGUARD_USER=admin

# Homepage defaults
ENV HOMEPAGE_HOST=http://homepage.default.svc:3000

# Tasmota defaults
ENV TASMOTA_DEVICES_FILE=/data/tasmota-devices.json

# Run the server
CMD ["python", "-m", "home_mcp.server"]
