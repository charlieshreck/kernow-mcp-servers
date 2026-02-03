# A2A Orchestrator - Parallel specialist agents for alert triage
# Build: v2 - Pydantic model fix
FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (for caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY src/a2a_orchestrator ./a2a_orchestrator

# Run as non-root
RUN useradd -m -u 1000 app
USER app

EXPOSE 8000

CMD ["uvicorn", "a2a_orchestrator.server:app", "--host", "0.0.0.0", "--port", "8000"]
