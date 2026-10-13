# Production LLM API

A small FastAPI service in front of Claude, built the way you'd ship it to production:

```
client -> nginx (load balancer) -> api replicas (FastAPI)
                                     |-- auth + rate limit + daily quota
                                     |-- semantic cache (Redis, embeddings)
                                     |-- LLM call: circuit breaker -> fallback
                                     '-- /metrics -> Prometheus -> Grafana + alerts
```

| Concept | Where |
|---|---|
| API layer / orchestration | `app/main.py` |
| Hosted API vs self-hosted (vLLM) | `app/llm.py` (`LLM_PROVIDER`), vLLM block in `docker-compose.yml` |
| Multi-stage build | `Dockerfile` |
| Multi-service environment | `docker-compose.yml` (api, nginx, redis, prometheus, grafana) |
| GPU passthrough | commented `vllm` service in `docker-compose.yml` |
| CI/CD | `.github/workflows/ci.yml` (lint -> tests -> build -> smoke test -> push) |
| Semantic caching + invalidation | `app/cache.py`, `DELETE /v1/cache`, `CACHE_VERSION`, TTL |
| Rate limiting & quotas | `app/ratelimit.py` |
| Circuit breaker & fallback | `app/llm.py` |
| Load balancing / horizontal scaling | `nginx/nginx.conf`, `--scale api=N` |
| Secrets management | `.env` (local), GitHub Secrets (CI), host secret store (prod) |
| Monitoring, alerts, cost dashboard | `monitoring/` |
| Security & governance, SLOs, runbooks, DR | `RUNBOOK.md` |

## 1. Run the tests (no API key needed)
```bash
python -m venv .venv && .venv\Scripts\activate      # Windows (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt -r requirements-dev.txt
ruff check . && pytest -q
```

## 2. Run the full stack
Needs Docker Desktop (with WSL 2 on Windows). Uses about 1.5 GB RAM.
```bash
cp .env.example .env        # then put your ANTHROPIC_API_KEY in .env
docker compose up -d --build
```
- API docs: http://localhost:8080/docs
- Prometheus (+ alerts tab): http://localhost:9090
- Grafana dashboard "LLM API - Production": http://localhost:3000 (admin / admin)

Try it - the second call should come back `"cached": true` and much faster:
```bash
curl -X POST localhost:8080/v1/chat -H "X-API-Key: dev-key" -H "Content-Type: application/json" -d "{\"prompt\":\"What is Docker used for?\"}"
curl -X POST localhost:8080/v1/chat -H "X-API-Key: dev-key" -H "Content-Type: application/json" -d "{\"prompt\":\"Why do people use Docker?\"}"
```
Tip: first startup downloads the embedding model (~100 MB), so give it a minute.

Then tune the cache threshold for the real model: `python scripts/tune_threshold.py`.

## 3. Demo the production features
- **Scaling:** `docker compose up -d --scale api=2` - Prometheus discovers both replicas.
- **Rate limit:** send 31+ requests in a minute -> HTTP 429.
- **Circuit breaker:** set a wrong `ANTHROPIC_API_KEY`, restart, send 3 requests -> fallback answers and the `CircuitBreakerOpen` alert fires.
- **Generate traffic for the dashboard:**
  ```bash
  for i in $(seq 1 50); do curl -s -X POST localhost:8080/v1/chat -H "X-API-Key: dev-key" -H "Content-Type: application/json" -d "{\"prompt\":\"question $((i % 10))\"}" > /dev/null; done
  ```

## 4. CI/CD
Push to GitHub. The workflow lints, tests (mock LLM, so no secrets are needed in CI), builds the image, smoke-tests the container and on `main` pushes it to GitHub Container Registry. Any container host (Render, Railway, Fly.io, a cloud VM) can then pull `ghcr.io/<you>/<repo>:<sha>`; put `ANTHROPIC_API_KEY`, `API_KEYS` and `ADMIN_KEY` in that host's secret settings.

## Stretch goals
- Swap the hand-written cache for GPTCache or Redis vector search and compare hit rates.
- Add OpenTelemetry tracing (reuse your observability module).
- Add Alertmanager to send alerts to email/Slack.
