# MACRV — Multi-Agent Code Review System

An automated code review pipeline powered by Claude agents. Reviews pull requests, classifies findings by severity, blocks critical bugs, notifies developers, and generates audit reports.

## Architecture

```
GitHub PR → FastAPI Webhook → Redis/Celery → Orchestrator (Opus-5-5)
                                                    ↓
                                        File Review Workers (Sonnet-4-6) [parallel]
                                                    ↓
                                        Security Scanner (Semgrep + truffleHog)
                                                    ↓
                                        Classifier (Haiku-5-5)
                                                    ↓
                              ┌─────────────────────────────────────────┐
                              │ PR Comments  │ Approve/Block │ Email    │
                              │ Jira Ticket  │ Slack Notify  │ Report   │
                              └─────────────────────────────────────────┘
```

## Finding Categories

| Category | Icon | Merge Impact |
|---|---|---|
| Critical | 🔴 | Blocks merge — `REQUEST_CHANGES` |
| Suggestive | 🟡 | Comment only — author's discretion |
| Optimisation | 🟢 | Inline comment — optional |

## Build Phases

- [x] **Phase 1** — Skeleton: webhook, queue, DB models, Docker, tests
- [ ] **Phase 2** — Agents: orchestrator, file workers, classifier
- [ ] **Phase 3** — GitHub integration: inline comments, approve/block
- [ ] **Phase 4** — Security scanner: Semgrep, truffleHog, dep audit
- [ ] **Phase 5** — Notifications: email, Slack, Jira/Linear
- [ ] **Phase 6** — Reports + eval harness (Weave)

## Quick Start

```bash
# 1. Clone and configure
git clone https://github.com/VaibhavBansal26/multi-agent-code-review
cd multi-agent-code-review
cp .env.example .env
# Edit .env — add ANTHROPIC_API_KEY, GITHUB_TOKEN, GITHUB_WEBHOOK_SECRET

# 2. Start all services
docker compose up -d

# 3. Run database migrations
docker compose exec api alembic upgrade head

# 4. Expose webhook (dev)
ngrok http 8000
# Set GitHub App webhook URL to: https://<ngrok-url>/webhook/github

# 5. Check health
curl http://localhost:8000/health
```

## Development

```bash
pip install -r requirements.txt
pytest tests/ -v
```

## Configuration

See `.env.example` for all available settings.

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Required — Claude API key |
| `GITHUB_WEBHOOK_SECRET` | Required — GitHub App webhook secret |
| `GITHUB_TOKEN` | Required — GitHub PAT or App token |
| `DATABASE_URL` | PostgreSQL async connection string |
| `CELERY_BROKER_URL` | Redis broker URL |
| `SENDGRID_API_KEY` | Optional — email notifications |
| `JIRA_URL` / `LINEAR_API_KEY` | Optional — ticket provider |

## Integrating with Your Codebase

1. Install the [MACRV GitHub App](https://github.com/apps/) on your repository
2. Or point any GitHub repo's webhook at your MACRV instance (`/webhook/github`)
3. Optionally add a `.macrv.yml` to your repo root to customise thresholds

```yaml
# .macrv.yml
severity_thresholds:
  block_on: [critical]
  notify_on: [critical, suggestive]
ignore_paths:
  - "*.md"
  - "docs/**"
  - "migrations/**"
models:
  orchestrator: claude-opus-5-5
  workers: claude-sonnet-4-6
  classifier: claude-haiku-5-5
```

## License

MIT
