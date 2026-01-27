# Observability MCP

Consolidated observability MCP server combining:
- **Keep**: Alert aggregation, deduplication, correlation
- **Coroot**: Service metrics, anomalies, dependencies
- **VictoriaMetrics**: PromQL queries, scrape targets
- **AlertManager**: Alerts, silences
- **Grafana**: Dashboards, annotations
- **Gatus**: Endpoint health monitoring

## Tools

### Keep (`keep_*`)
- `keep_list_alerts` - List alerts with filters
- `keep_get_alert` - Get alert details
- `keep_acknowledge_alert` - Acknowledge an alert
- `keep_resolve_alert` - Resolve an alert
- `keep_list_incidents` - List correlated incidents
- `keep_get_incident` - Get incident details
- `keep_acknowledge_incident` - Acknowledge an incident
- `keep_resolve_incident` - Resolve an incident
- `keep_health` - Check Keep connectivity

### Coroot (`coroot_*`)
- `coroot_get_service_metrics` - Get service CPU/memory/latency
- `coroot_get_recent_anomalies` - Get detected anomalies
- `coroot_get_service_dependencies` - Get service dependencies
- `coroot_get_alerts` - Get Coroot alerts
- `coroot_get_infrastructure_overview` - Get infrastructure overview

### Metrics (`query_*`)
- `query_metrics` - Execute PromQL range query
- `query_metrics_instant` - Execute instant PromQL query
- `get_scrape_targets` - List scrape targets
- `get_metric_names` - List available metrics
- `get_tsdb_stats` - Get TSDB statistics

### AlertManager (`*_alerts`, `*_silence*`)
- `list_alerts` - List AlertManager alerts
- `create_silence` - Create a silence
- `delete_silence` - Delete a silence
- `list_silences` - List active silences
- `get_alertmanager_status` - Get AM status

### Grafana (`grafana_*`)
- `grafana_list_dashboards` - List dashboards
- `grafana_get_dashboard_url` - Get dashboard URL
- `grafana_create_annotation` - Create annotation
- `grafana_list_datasources` - List datasources

### Gatus (`gatus_*`)
- `gatus_get_endpoint_status` - Get all endpoint health
- `gatus_get_failing_endpoints` - Get only failing endpoints

## Configuration

Environment variables:
```bash
# Keep
KEEP_URL=http://keep.keep.svc.cluster.local:8080
KEEP_API_KEY=xxx

# Coroot
COROOT_URL=http://coroot.monitoring.svc:8080
COROOT_PROJECT=default

# VictoriaMetrics
VICTORIA_METRICS_URL=http://victoriametrics.monit.kernow.io

# AlertManager
ALERTMANAGER_URL=http://alertmanager.monit.kernow.io

# Grafana
GRAFANA_URL=http://grafana.monit.kernow.io
GRAFANA_USER=admin
GRAFANA_PASSWORD=xxx

# Gatus
GATUS_URL=http://gatus.monit.kernow.io
```

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run locally
python -m observability_mcp.server

# Run tests
pytest
```

## Docker

```bash
# Build (from repo root)
docker build -f domains/observability/Dockerfile -t mcp-observability .

# Run
docker run -p 8000:8000 \
  -e KEEP_URL=http://keep:8080 \
  -e VICTORIA_METRICS_URL=http://vm:9090 \
  mcp-observability
```
