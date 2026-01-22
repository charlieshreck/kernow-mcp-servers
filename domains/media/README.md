# Media MCP

Consolidated MCP server for media management in the Kernow homelab.

## Consolidates

| Previous MCP | Port | Tools |
|--------------|------|-------|
| plex-mcp | 31096 | 12 |
| arr-suite-mcp | 31091 | 30 |

**Total tools**: ~42

## Components

### Plex (`plex_*`)
- Server status, version, claim status
- Library management (list, refresh, empty trash)
- Active sessions and transcode monitoring
- GPU utilization via nvidia-smi

### Sonarr (`sonarr_*`)
- TV series management
- Search and add series by TVDB ID
- Download queue monitoring
- Trigger episode searches

### Radarr (`radarr_*`)
- Movie management
- Search and add movies by TMDB ID
- Download queue monitoring
- Trigger movie searches

### Prowlarr (`prowlarr_*`)
- Indexer management and health
- Cross-indexer search
- Test indexer connections

### Overseerr (`overseerr_*`)
- Media request management
- Approve/decline requests
- Trending content discovery

### Tautulli (`tautulli_*`)
- Plex activity monitoring
- Watch history and statistics
- Library statistics

### Transmission (`transmission_*`)
- Torrent management
- Add/pause/resume/remove torrents
- Download progress tracking

### SABnzbd (`sabnzbd_*`)
- Usenet download queue
- History tracking
- Pause/resume queue

## Environment Variables

### Plex
- `PLEX_URL` - Plex server URL (default: http://10.10.0.50:32400)
- `PLEX_TOKEN` - Plex authentication token
- `PLEX_HOST` - Plex host for SSH (GPU monitoring)

### Arr Suite
- `SONARR_URL`, `SONARR_API_KEY`
- `RADARR_URL`, `RADARR_API_KEY`
- `PROWLARR_URL`, `PROWLARR_API_KEY`
- `OVERSEERR_URL`, `OVERSEERR_API_KEY`
- `TAUTULLI_URL`, `TAUTULLI_API_KEY`
- `TRANSMISSION_URL`, `TRANSMISSION_USER`, `TRANSMISSION_PASS`
- `SABNZBD_URL`, `SABNZBD_API_KEY`

## Endpoints

- `GET /health` - Health check with component status
- `GET /ready` - Readiness probe
- `POST /mcp/` - MCP JSON-RPC endpoint
- `GET /sse` - MCP SSE endpoint

## Running Locally

```bash
cd domains/media
pip install -e ../../shared -e .
python -m media_mcp.server
```
