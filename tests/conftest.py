import os

# Must be set BEFORE the app is imported: no real LLM, no Redis, no model download.
os.environ.update({
    "LLM_PROVIDER": "mock",
    "EMBEDDER": "fake",
    "REDIS_URL": "",
    "API_KEYS": "test-key,rl-key",
    "ADMIN_KEY": "admin",
    "RATE_LIMIT_PER_MIN": "20",
    "CACHE_THRESHOLD": "0.85",
})
