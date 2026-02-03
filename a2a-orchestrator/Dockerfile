FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy source
COPY src/ src/

# Install the package
RUN pip install --no-cache-dir -e .

# Run as non-root
RUN useradd -m -u 1000 app
USER app

EXPOSE 8000

CMD ["uvicorn", "a2a_orchestrator.server:app", "--host", "0.0.0.0", "--port", "8000"]
