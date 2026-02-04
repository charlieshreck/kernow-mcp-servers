FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy all source files
COPY pyproject.toml README.md ./
COPY src/ src/

# Install dependencies and package
RUN pip install --no-cache-dir .

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run as non-root
RUN useradd -m -u 1000 app
USER app

EXPOSE 8000

CMD ["uvicorn", "a2a_orchestrator.server:app", "--host", "0.0.0.0", "--port", "8000"]
