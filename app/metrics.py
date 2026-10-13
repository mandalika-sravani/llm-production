"""Prometheus metrics. Grafana reads these via Prometheus."""
from prometheus_client import Counter, Gauge, Histogram

REQUESTS = Counter("api_requests_total", "HTTP requests", ["endpoint", "status"])
LATENCY = Histogram(
    "api_request_latency_seconds", "Request latency", ["endpoint"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 3, 5, 10, 20),
)
CACHE = Counter("semantic_cache_lookups_total", "Cache lookups", ["result"])  # hit / miss
LLM_CALLS = Counter("llm_calls_total", "LLM calls", ["provider", "outcome"])  # success / error / fallback
LLM_TOKENS = Counter("llm_tokens_total", "Tokens used", ["direction"])  # input / output
LLM_COST = Counter("llm_cost_usd_total", "Estimated LLM spend in USD")
BREAKER_OPEN = Gauge("circuit_breaker_open", "1 if the LLM circuit breaker is open")
RATE_LIMITED = Counter("rate_limited_total", "Requests rejected by rate limit / quota", ["kind"])
APP_INFO = Gauge("app_info", "Build info", ["version", "model", "provider"])
