# MCP Servers

Consolidated Model Context Protocol servers for the Kernow homelab.

## Architecture

- Framework: FastMCP (Python)
- Shared library: `shared/kernow_mcp_common/` (auth, clients, utilities)
- 6 domain servers, each independently deployable

## Domains

| Domain | Source | Image | Key Integrations |
|--------|--------|-------|-----------------|
| observability | domains/observability/ | ghcr.io/charlieshreck/mcp-observability | Keep, Coroot, VictoriaMetrics, AlertManager, Grafana, Gatus, ntopng |
| infrastructure | domains/infrastructure/ | ghcr.io/charlieshreck/mcp-infrastructure | K8s, ArgoCD, Proxmox, TrueNAS, Cloudflare, OPNsense, Caddy, Infisical, Omada |
| knowledge | domains/knowledge/ | ghcr.io/charlieshreck/mcp-knowledge | Qdrant, Neo4j, Outline, SilverBullet, Vikunja |
| home | domains/home/ | ghcr.io/charlieshreck/mcp-home | Home Assistant, Tasmota, UniFi, AdGuard, Homepage |
| media | domains/media/ | ghcr.io/charlieshreck/mcp-media | Plex, *arr suite, Tautulli, Transmission, SABnzbd, Huntarr, Cleanuparr, Maintainerr |
| external | domains/external/ | ghcr.io/charlieshreck/mcp-external | SearXNG, GitHub, Reddit, Wikipedia, Playwright |

## Repository Structure

```
mcp-servers/
├── domains/
│   ├── <domain>/
│   │   ├── src/<domain>_mcp/
│   │   │   ├── server.py       # FastMCP app + tool registration
│   │   │   └── tools/          # One file per integration
│   │   ├── Dockerfile
│   │   └── pyproject.toml
├── shared/
│   └── kernow_mcp_common/      # Shared utilities (auth, HTTP clients)
├── kubernetes/
│   └── domains/                # K8s manifests (one YAML per domain)
└── README.md
```

## Development

- Each domain: `domains/<name>/src/<name>_mcp/`
- Tools: `tools/*.py`, Server: `server.py`
- Local dev: `cd domains/<name> && uv run python -m <name>_mcp.server`
- Add new tool: create `tools/new_tool.py`, register in `server.py`
- All tools must have docstrings (FastMCP uses them for tool descriptions)
- Use Pydantic models for tool params where inputs are complex objects

## Deployment

- All deploy to **agentic cluster** (ai-platform namespace) ONLY
- K8s manifests: `kubernetes/domains/<name>.yaml`
- Build: `docker build -f domains/<name>/Dockerfile -t ghcr.io/charlieshreck/mcp-<name>:latest .`
- CI builds and pushes images automatically on commit to main
- ArgoCD auto-syncs manifests from the kubernetes/ directory
- **NEVER deploy MCP servers to prod or monit clusters**

## Rules

- Use Infisical for all secrets (InfisicalSecret CRD in K8s manifests)
- Follow existing tool patterns — see any `tools/*.py` for reference
- Test tools via MCP Inspector or direct Python import before deploying
- After code changes: push to git, ArgoCD syncs, restart deployment if needed
- ConfigMap changes require manual `kubectl rollout restart` after ArgoCD sync
