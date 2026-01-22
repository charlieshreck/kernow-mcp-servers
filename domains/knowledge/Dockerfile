FROM python:3.11-slim

WORKDIR /app

# Install dependencies first for caching
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy source code
COPY src/ src/

# Install the package
RUN pip install --no-cache-dir -e .

# Run as non-root user
RUN useradd -m -u 1000 mcp
USER mcp

EXPOSE 8000

CMD ["knowledge-mcp"]
