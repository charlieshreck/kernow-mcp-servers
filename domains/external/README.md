# External MCP Server

Consolidated external APIs MCP for the Kernow homelab. Provides tools for web search, GitHub operations, Reddit browsing, Wikipedia knowledge retrieval, and browser automation.

## Replaces

This domain MCP consolidates the following individual MCPs:

| Previous MCP | Port | Tools |
|--------------|------|-------|
| web-search-mcp | 31093 | 5 tools |
| github-mcp | 31111 | 15 tools |
| reddit-mcp | 31104 | 12 tools |
| wikipedia-mcp | 31112 | 10 tools |
| browser-automation-mcp | 31094 | 15 tools |

**Total: 57 tools consolidated into 1 domain MCP**

## Tools

### Web Search (`websearch_*`)
- `websearch_search` - Search the web using SearXNG
- `websearch_get_page_content` - Fetch and convert page to markdown
- `websearch_search_news` - Search for news articles
- `websearch_search_images` - Search for images
- `websearch_search_and_fetch` - Search and auto-fetch top results

### GitHub (`github_*`)
- `github_get_repo` - Get repository details
- `github_list_repos` - List user/org repositories
- `github_get_file` - Get file contents
- `github_list_branches` - List branches
- `github_get_commits` - Get recent commits
- `github_list_issues` - List issues
- `github_get_issue` - Get issue details
- `github_create_issue` - Create new issue
- `github_list_prs` - List pull requests
- `github_get_pr` - Get PR details
- `github_get_pr_diff` - Get PR diff
- `github_search_code` - Search code
- `github_search_repos` - Search repositories
- `github_list_workflows` - List GitHub Actions workflows
- `github_list_workflow_runs` - List workflow runs

### Reddit (`reddit_*`)
- `reddit_hot` - Get hot posts
- `reddit_new` - Get new posts
- `reddit_top` - Get top posts (by time)
- `reddit_search` - Search posts
- `reddit_search_subreddits` - Search subreddits
- `reddit_post` - Get post details
- `reddit_comments` - Get post comments
- `reddit_subreddit_info` - Get subreddit info
- `reddit_subreddit_rules` - Get subreddit rules
- `reddit_user_info` - Get user info
- `reddit_user_posts` - Get user posts

### Wikipedia (`wikipedia_*`)
- `wikipedia_search` - Search articles
- `wikipedia_summary` - Get article summary
- `wikipedia_article` - Get full article
- `wikipedia_sections` - Get table of contents
- `wikipedia_section_content` - Get section content
- `wikipedia_links` - Get article links
- `wikipedia_related` - Get related articles
- `wikipedia_categories` - Get article categories
- `wikipedia_random` - Get random article
- `wikipedia_on_this_day` - Get historical events

### Browser Automation (`browser_*`)
- `browser_navigate` - Navigate to URL
- `browser_screenshot` - Take screenshot
- `browser_click` - Click element
- `browser_click_coordinates` - Click at coordinates
- `browser_type_text` - Type into element
- `browser_press_key` - Press keyboard key
- `browser_scroll` - Scroll page
- `browser_get_page_content` - Get page content
- `browser_evaluate_js` - Execute JavaScript
- `browser_wait_for_selector` - Wait for element
- `browser_fill_form` - Fill form fields
- `browser_get_element_text` - Get element text
- `browser_get_all_links` - Get all links
- `browser_go_back` - Navigate back
- `browser_reload_page` - Reload page

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | 8000 | Server port |
| `HOST` | 0.0.0.0 | Server host |
| `SEARXNG_URL` | `http://searxng.ai-platform.svc.cluster.local:8080` | SearXNG instance URL |
| `GITHUB_TOKEN` | (required) | GitHub API token |
| `BROWSER_TYPE` | chromium | Playwright browser type |
| `HEADLESS` | true | Run browser headless |
| `VIEWPORT_WIDTH` | 1920 | Browser viewport width |
| `VIEWPORT_HEIGHT` | 1080 | Browser viewport height |
| `DEFAULT_TIMEOUT` | 30000 | Browser default timeout (ms) |

### Secrets Required

- `GITHUB_TOKEN` - GitHub personal access token (for github_* tools)
  - Uses existing `mcp-github` K8s secret in ai-platform namespace

## Endpoints

- `GET /health` - Health check
- `GET /ready` - Readiness probe
- `POST /mcp/v1/messages` - MCP HTTP transport

## Development

```bash
# Install dependencies
pip install -e ".[dev]"

# Run locally
python -m external_mcp.server

# Run tests
pytest

# Lint
ruff check src/
```

## Docker Build

```bash
# Build from mcp-servers root
docker build -f domains/external/Dockerfile -t external-mcp:latest .

# Run
docker run -p 8000:8000 -e GITHUB_TOKEN=xxx external-mcp:latest
```

## Kubernetes Deployment

See `kubernetes/domains/external.yaml` for the complete deployment manifest including:
- ConfigMap with environment variables
- InfisicalSecret for GITHUB_TOKEN
- Deployment with probes and resources
- Service (NodePort)
- Ingress for DNS access

## Access

- Internal: `http://external-mcp.ai-platform.svc.cluster.local:8000`
- DNS: `http://external-mcp.agentic.kernow.io`
- NodePort: `http://10.20.0.40:31122`
