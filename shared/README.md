# Kernow MCP Common

Shared utilities for Kernow MCP servers.

## Features

- Base server creation helpers
- Infisical secrets integration
- Common patterns and utilities

## Usage

```python
from kernow_mcp_common import create_mcp_server, create_starlette_app
from kernow_mcp_common import get_secret, list_secrets

# Create MCP server
mcp = create_mcp_server(name="my-mcp", instructions="...")

# Get secrets
api_key = get_secret("/path/to/secret", "API_KEY")
```

## Installation

```bash
pip install -e /path/to/shared
```
