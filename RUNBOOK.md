# Runbook - Production LLM API

## Service Level Objectives (SLOs)
| SLI | Objective (30-day window) | Alert |
|---|---|---|
| Availability: non-5xx responses on `/v1/chat` | 99% | `HighErrorRate` (>1% for 5m) |
| Latency: p95 of `/v1/chat` | < 5 s | `HighLatencyP95` (10m) |
| Cost: LLM spend | < $1 / hour | `CostSpike` |

Error budget: 1% of requests per 30 days may fail. If it's used up, freeze feature releases and work on reliability.

## Incident playbooks

### 1. `CircuitBreakerOpen` - LLM provider failing
Symptoms: answers say "temporarily unavailable"; `llm_calls_total{outcome="error"}` rising.
1. Check the provider's status page.
2. `docker compose logs api | grep "LLM call failed"` - `AuthenticationError` means the API key is wrong/rotated -> update the secret and restart. `RateLimitError` -> lower traffic or raise provider limits. Timeouts -> provider outage, wait.
3. Cached answers keep working meanwhile. Breaker retries automatically after `CB_RESET_S`.

### 2. `HighLatencyP95`
1. Grafana: is the cache hit ratio dropping? (more LLM calls = slower)
2. Is one replica unhealthy? `docker compose ps`. Scale out: `docker compose up -d --scale api=3`.
3. Lower `MAX_TOKENS` if responses are very long.

### 3. Bad answer got cached
`curl -X DELETE localhost:8080/v1/cache -H "X-Admin-Key: $ADMIN_KEY"`.
For a model or prompt change, bump `CACHE_VERSION` instead (old entries simply stop matching and expire by TTL).

### 5. 502 Bad Gateway from nginx (seen in testing)
Cause: no healthy api behind nginx - api crash-looping, or nginx holding a stale container IP.
1. `docker compose ps` - is `api` `(healthy)` and is its "Up" time increasing?
2. If it keeps restarting: `docker compose logs api --tail 50`.
3. Fixed at the root: nginx re-resolves `api` via Docker DNS every 10s (`resolver 127.0.0.11`), and only starts once the api is healthy.

### 6. API crash-loops on startup: "Could not load model ... from any source" (seen in testing)
Cause: the embedding model was downloaded at runtime; with no network at boot, startup failed.
Fixed at the root: the model is baked into the image at build time and runtime runs with `HF_HUB_OFFLINE=1`.
Safety net: if the model still can't load, the API starts with the semantic cache **disabled**
(log: "semantic cache DISABLED") and serves every request from the LLM. Cache hit ratio drops to 0 on the dashboard.

### 4. `CostSpike`
1. Check `rate_limited_total` and the audit log for one `key_id` sending a flood.
2. Remove that key from `API_KEYS` or lower `DAILY_QUOTA`, then restart.

## Deploy, canary and rollback
- Every push to `main` builds `ghcr.io/<you>/<repo>:<git-sha>` - the SHA is the version.
- **Canary:** run the new image as one extra replica next to the old ones (or use your host's traffic-splitting). Watch the dashboard for 15 min: error rate, p95, cost, filtered by `app_info{version=...}`.
- **Rollback:** redeploy the previous SHA tag. No data migration needed - Redis only holds cache.

## Disaster recovery
| Component | State | Recovery |
|---|---|---|
| API containers | stateless | redeploy image from GHCR |
| Redis | cache + rate-limit counters only | lose it safely; starts empty (RPO: n/a) |
| Config | in git + secrets manager | re-apply |
| Dashboards/alerts | provisioned from git | `docker compose up` recreates them |
Target RTO: 15 minutes (time to redeploy from a clean machine).

## Data governance
- Prompts are **not** logged by default (`LOG_PROMPTS=false`). Audit logs record time, path, status, latency, version and a **hash** of the API key.
- Cached answers expire after `CACHE_TTL_S` (default 24 h) - that is the data-retention period for user content.
- Access control: client keys (`X-API-Key`) for chat; a separate admin key for cache deletion. `/metrics` is blocked at the load balancer.
