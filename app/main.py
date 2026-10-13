import hashlib
import json
import logging
import time

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app
from pydantic import BaseModel, Field

from .cache import build_cache
from .config import settings
from .llm import LLMService
from .metrics import APP_INFO, CACHE, LATENCY, RATE_LIMITED, REQUESTS
from .ratelimit import FixedWindowLimiter

logging.basicConfig(level=logging.INFO, format="%(message)s")
audit = logging.getLogger("audit")

app = FastAPI(title="Production LLM API", version=settings.app_version)
app.mount("/metrics", make_asgi_app())

cache = build_cache(settings)
llm = LLMService()
per_minute = FixedWindowLimiter(settings.rate_limit_per_min, 60, settings.redis_url, "rl:min")
per_day = FixedWindowLimiter(settings.daily_quota, 86400, settings.redis_url, "rl:day")
APP_INFO.labels(settings.app_version, settings.llm_model, settings.llm_provider).set(1)


def key_id(api_key: str) -> str:
    """Audit logs store a hash of the key, never the key itself."""
    return hashlib.sha256(api_key.encode()).hexdigest()[:10]


# ---------- middleware: metrics + audit trail ----------
@app.middleware("http")
async def observe(request: Request, call_next):
    start = time.perf_counter()
    path = request.url.path
    try:
        response = await call_next(request)
        status = response.status_code
    except Exception:
        status = 500
        response = JSONResponse({"detail": "internal error"}, status_code=500)
    elapsed = time.perf_counter() - start
    if not path.startswith("/metrics"):
        REQUESTS.labels(path, str(status)).inc()
        LATENCY.labels(path).observe(elapsed)
        key = request.headers.get("x-api-key")
        audit.info(json.dumps({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "path": path, "method": request.method, "status": status,
            "latency_ms": round(elapsed * 1000, 1),
            "key_id": key_id(key) if key else None,
            "version": settings.app_version,
        }))
    return response


# ---------- auth + limits ----------
def require_api_key(x_api_key: str | None = Header(default=None)) -> str:
    if not x_api_key or x_api_key not in settings.api_keys:
        raise HTTPException(401, "invalid or missing X-API-Key")
    kid = key_id(x_api_key)
    if not per_minute.allow(kid):
        RATE_LIMITED.labels("per_minute").inc()
        raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": "60"})
    if not per_day.allow(kid):
        RATE_LIMITED.labels("daily_quota").inc()
        raise HTTPException(429, "daily quota exceeded")
    return x_api_key


def require_admin(x_admin_key: str | None = Header(default=None)) -> None:
    if x_admin_key != settings.admin_key:
        raise HTTPException(403, "admin only")


# ---------- schemas ----------
class ChatRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)


class ChatResponse(BaseModel):
    answer: str
    cached: bool
    similarity: float | None
    source: str
    model: str
    version: str
    latency_ms: float


# ---------- routes ----------
@app.get("/health")
def health():
    """Liveness: the process is up."""
    return {"status": "ok", "version": settings.app_version}


@app.get("/ready")
def ready():
    """Readiness: dependencies reachable. Load balancers should route only to ready pods."""
    ok = cache.store.ping()
    if not ok:
        raise HTTPException(503, "cache backend unavailable")
    return {"status": "ready", "breaker": llm.breaker.state}


@app.post("/v1/chat", response_model=ChatResponse)
def chat(body: ChatRequest, api_key: str = Depends(require_api_key)):
    start = time.perf_counter()
    try:
        cached, score = cache.get(body.prompt)
    except Exception:  # cache down -> degrade gracefully, don't fail the request
        cached, score = None, 0.0
    if cached is not None:
        CACHE.labels("hit").inc()
        answer, source = cached, "cache"
    else:
        CACHE.labels("miss").inc()
        answer, source = llm.generate(body.prompt)
        if source != "fallback":  # never cache error messages
            try:
                cache.set(body.prompt, answer)
            except Exception:
                pass
    if settings.log_prompts:
        audit.info(json.dumps({"key_id": key_id(api_key), "prompt": body.prompt}))
    return ChatResponse(
        answer=answer, cached=cached is not None,
        similarity=round(score, 4) if score else None, source=source,
        model=settings.llm_model, version=settings.app_version,
        latency_ms=round((time.perf_counter() - start) * 1000, 1),
    )


@app.delete("/v1/cache", dependencies=[Depends(require_admin)])
def clear_cache():
    """Manual cache invalidation (e.g. after a bad answer got cached)."""
    return {"deleted": cache.store.clear()}
