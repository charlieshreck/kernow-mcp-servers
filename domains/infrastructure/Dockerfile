# Infrastructure MCP Server
# Consolidated MCP for Kubernetes, Proxmox, TrueNAS, Cloudflare, OPNsense, and secrets management

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies including kubectl
ARG KUBECTL_VERSION=v1.35.0
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && curl -fL --output kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
    && install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl \
    && rm kubectl \
    && rm -rf /var/lib/apt/lists/*

# Copy shared utilities first (for Docker layer caching)
COPY shared/kernow_mcp_common /app/shared/kernow_mcp_common
COPY shared/pyproject.toml /app/shared/
COPY shared/README.md /app/shared/

# Copy domain-specific code (using repo root context)
COPY domains/infrastructure/pyproject.toml .
COPY domains/infrastructure/README.md .
COPY domains/infrastructure/src ./src

# Install dependencies
RUN pip install --no-cache-dir /app/shared
RUN pip install --no-cache-dir .

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run as non-root
RUN useradd -m -u 1000 mcp
USER mcp

# Expose port
EXPOSE 8000

# Environment defaults
ENV PORT=8000
ENV HOST=0.0.0.0

# Run the server
CMD ["python", "-m", "infrastructure_mcp.server"]
