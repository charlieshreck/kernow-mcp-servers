# Kernow MCP Servers

Domain-consolidated MCP (Model Context Protocol) servers for the Kernow homelab.

## Architecture

This repository consolidates 25+ individual MCP servers into 6 domain-based servers:

| Domain | Components | Purpose |
|--------|-----------|---------|
| **observability** | Keep, Coroot, VictoriaMetrics, AlertManager, Grafana, Gatus, ntopng | Monitoring, alerting, observability |
| **infrastructure** | Kubernetes, ArgoCD, Proxmox, TrueNAS, Cloudflare, OPNsense, Caddy, Infisical, Omada | Infrastructure management |
| **knowledge** | Qdrant, Outline, Neo4j, SilverBullet, Vikunja | Knowledge base, documentation, graph |
| **home** | Home Assistant, Tasmota, UniFi, AdGuard, Homepage | Home automation, IoT, network |
| **media** | Plex, Sonarr, Radarr, Prowlarr, Overseerr, Tautulli, Transmission, SABnzbd, Huntarr, Cleanuparr, Maintainerr, Notifiarr, Recommendarr | Media management |
| **external** | Web Search, GitHub, Reddit, Wikipedia, Playwright browser | External API integrations |

## Benefits

- **Reduced resource usage**: ~800MB RAM vs ~3.5GB (25 individual pods)
- **Faster startup**: Pre-built Docker images vs pip install at runtime
- **Session stability**: All use `stateless_http=True` for Kubernetes compatibility
- **Easier maintenance**: 6 repositories vs 25 ConfigMaps

## Repository Structure

```
mcp-servers/
├── domains/
│   ├── observability/
│   │   ├── src/observability_mcp/
│   │   ├── Dockerfile
│   │   └── pyproject.toml
│   ├── infrastructure/
│   ├── knowledge/
│   ├── home/
│   ├── media/
│   └── external/
├── shared/
│   └── kernow_mcp_common/    # Shared utilities
├── kubernetes/
│   └── domains/              # K8s manifests
└── README.md
```

## Development

### Prerequisites

- Python 3.11+
- Docker
- kubectl (for deployment)

### Local Development

```bash
# Install dependencies for a domain
cd domains/observability
pip install -e ".[dev]"

# Run locally
python -m observability_mcp

# Run tests
pytest
```

### Building Docker Images

```bash
# Build a domain image
cd domains/observability
docker build -t ghcr.io/charlieshreck/mcp-observability:latest .

# Push to registry
docker push ghcr.io/charlieshreck/mcp-observability:latest
```

### Deployment

All domains deploy to the agentic cluster (10.20.0.0/24) via ArgoCD:

```bash
# Apply manifests (from prod cluster where ArgoCD runs)
kubectl apply -f kubernetes/domains/observability.yaml
```

## Configuration

Each domain reads configuration from environment variables and Infisical secrets.
See individual domain READMEs for specific configuration options.

## Migration from Individual MCPs

This repository replaces the ConfigMap-based MCPs in:
`/home/agentic_lab/kubernetes/applications/mcp-servers/`

Migration is done domain-by-domain:
1. Deploy new consolidated MCP
2. Update `.mcp.json` to point to new endpoint
3. Verify functionality
4. Remove old individual MCP ConfigMaps

## License

MIT
# Triggered rebuild Sun Feb  1 07:12:15 PM UTC 2026
