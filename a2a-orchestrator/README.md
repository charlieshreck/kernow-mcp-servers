# A2A Orchestrator

Parallel specialist agents (Gemini-powered) for alert triage in the Kernow homelab.

## Architecture

```
LangGraph Incident Flow
         │
         ▼
   A2A Orchestrator (/v1/investigate)
         │
    ┌────┴────┐
    │ Fan Out │ (parallel, 15s timeout)
    └────┬────┘
         │
   ┌─────┼─────┬─────┬─────┐
   ▼     ▼     ▼     ▼     ▼
DevOps Network Security SRE Database
   │     │     │     │     │
   └─────┴─────┴─────┴─────┘
         │
    ┌────┴────┐
    │Synthesis│ (weighted by domain authority)
    └────┬────┘
         │
         ▼
   Final Verdict
```

## Specialists

| Agent | Domain | MCP Tools Used |
|-------|--------|----------------|
| DevOps | K8s pods, OOM, crashloops | kubectl_get_pods, kubectl_logs, kubectl_get_events |
| Network | DNS, routing, connectivity | adguard_list_rewrites, kubectl_get_services |
| Security | Secrets, auth, certs | list_secrets, kubectl_get_events |
| SRE | Metrics, latency, anomalies | query_metrics_instant, coroot_get_recent_anomalies |
| Database | Qdrant, Neo4j, queries | search_entities, search_runbooks |

## API

### POST /v1/investigate

```json
{
  "request_id": "abc-123",
  "alert": {
    "name": "KubePodCrashLooping",
    "labels": {
      "namespace": "ai-platform",
      "pod": "knowledge-mcp-abc123"
    },
    "severity": "critical",
    "description": "Pod is crash looping"
  }
}
```

Response:
```json
{
  "request_id": "abc-123",
  "verdict": "ACTIONABLE",
  "confidence": 0.85,
  "findings": [...],
  "synthesis": "Pod OOM killed due to memory limit. Increase limits.",
  "suggested_action": "kubectl set resources deployment/knowledge-mcp --limits=memory=2Gi",
  "fallback_used": false,
  "latency_ms": 3200
}
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENROUTER_API_KEY` | OpenRouter API key for Gemini | Required |
| `SPECIALIST_MODEL` | Model for specialists | google/gemini-2.0-flash-001 |
| `SYNTHESIS_MODEL` | Model for synthesis | google/gemini-2.0-flash-001 |
| `QWEN_URL` | Fallback LLM endpoint | http://litellm:4000/v1/chat/completions |
| `QWEN_MODEL` | Fallback model name | qwen/qwen2.5-coder-14b |
| `A2A_API_TOKEN` | Token for MCP REST bridge | Required |
| `INFRASTRUCTURE_MCP_URL` | Infrastructure MCP endpoint | http://infrastructure-mcp:8000 |
| `OBSERVABILITY_MCP_URL` | Observability MCP endpoint | http://observability-mcp:8000 |
| `KNOWLEDGE_MCP_URL` | Knowledge MCP endpoint | http://knowledge-mcp:8000 |
| `HOME_MCP_URL` | Home MCP endpoint | http://home-mcp:8000 |

## Fallback Behavior

When Gemini quota is exhausted (429 rate limit):
1. Specialists return minimal findings with ERROR status
2. Synthesis falls back to rule-based logic
3. If all else fails, qwen provides heuristic assessment

## Development

```bash
# Install
pip install -e ".[dev]"

# Run locally
export OPENROUTER_API_KEY=...
export A2A_API_TOKEN=...
uvicorn a2a_orchestrator.server:app --reload

# Test
pytest
```

## Deployment

Deployed to agentic cluster in `ai-platform` namespace via ArgoCD.

See `kubernetes/applications/a2a/` for manifests.

