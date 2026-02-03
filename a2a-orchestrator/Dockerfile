FROM python:3.11-slim

WORKDIR /app

# Copy everything needed for install
COPY pyproject.toml .
COPY src/ src/

# Install the package
RUN pip install --no-cache-dir .

# Run as non-root
RUN useradd -m -u 1000 app
USER app

EXPOSE 8000

CMD ["uvicorn", "a2a_orchestrator.server:app", "--host", "0.0.0.0", "--port", "8000"]
