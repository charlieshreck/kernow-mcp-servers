# Knowledge MCP Server
# Consolidated MCP for semantic search, graph queries, wiki, and task management

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy domain-specific code (using repo root context)
COPY domains/knowledge/pyproject.toml .
COPY domains/knowledge/README.md .
COPY domains/knowledge/src ./src

# Install dependencies
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
CMD ["python", "-m", "knowledge_mcp.server"]
