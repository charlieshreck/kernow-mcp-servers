# Media MCP Server
# Consolidated media management MCP for Kernow homelab

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (openssh-client for GPU monitoring via SSH)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Copy shared utilities first (for Docker layer caching)
COPY shared/kernow_mcp_common /app/shared/kernow_mcp_common
COPY shared/pyproject.toml /app/shared/
COPY shared/README.md /app/shared/

# Copy domain-specific code
COPY domains/media/pyproject.toml .
COPY domains/media/README.md .
COPY domains/media/src ./src

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

# Plex defaults
ENV PLEX_URL=http://10.10.0.50:32400
ENV PLEX_HOST=10.10.0.50

# Arr suite defaults
ENV SONARR_URL=https://sonarr.kernow.io
ENV RADARR_URL=https://radarr.kernow.io
ENV PROWLARR_URL=https://prowlarr.kernow.io
ENV OVERSEERR_URL=https://overseerr.kernow.io
ENV TAUTULLI_URL=https://tautulli.kernow.io
ENV TRANSMISSION_URL=https://transmission.kernow.io
ENV SABNZBD_URL=https://sabnzbd.kernow.io

# Run the server
CMD ["python", "-m", "media_mcp.server"]
