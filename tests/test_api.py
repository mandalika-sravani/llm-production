from fastapi.testclient import TestClient

from app.llm import FALLBACK_MESSAGE, CircuitBreaker, LLMService
from app.main import app, cache

client = TestClient(app)
H = {"X-API-Key": "test-key"}


def setup_function():
    cache.store.clear()


def test_health_and_ready():
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/ready").status_code == 200


def test_requires_api_key():
    assert client.post("/v1/chat", json={"prompt": "hi"}).status_code == 401
    assert client.post("/v1/chat", json={"prompt": "hi"}, headers={"X-API-Key": "bad"}).status_code == 401


def test_validation():
    assert client.post("/v1/chat", json={"prompt": ""}, headers=H).status_code == 422


def test_semantic_cache_hit_on_paraphrase():
    first = client.post("/v1/chat", json={"prompt": "What is Docker used for?"}, headers=H).json()
    assert first["cached"] is False and first["source"] == "mock"

    same = client.post("/v1/chat", json={"prompt": "what is docker used for"}, headers=H).json()
    assert same["cached"] is True and same["answer"] == first["answer"]

    other = client.post("/v1/chat", json={"prompt": "Explain Kubernetes pods"}, headers=H).json()
    assert other["cached"] is False


def test_no_false_hit_when_only_numbers_differ():
    # Regression test: "scaling test 3" used to get the cached answer for "scaling test 1"
    client.post("/v1/chat", json={"prompt": "scaling test 1"}, headers=H)
    assert client.post("/v1/chat", json={"prompt": "scaling test 3"}, headers=H).json()["cached"] is False
    assert client.post("/v1/chat", json={"prompt": "Scaling test 1?"}, headers=H).json()["cached"] is True


def test_cache_invalidation_admin_only():
    client.post("/v1/chat", json={"prompt": "cache me"}, headers=H)
    assert client.delete("/v1/cache").status_code == 403
    assert client.delete("/v1/cache", headers={"X-Admin-Key": "admin"}).json()["deleted"] >= 1
    again = client.post("/v1/chat", json={"prompt": "cache me"}, headers=H).json()
    assert again["cached"] is False


def test_rate_limit():
    codes = [client.post("/v1/chat", json={"prompt": f"q {i}"},
                         headers={"X-API-Key": "rl-key"}).status_code for i in range(25)]
    assert 429 in codes and codes[0] == 200


def test_metrics_exposed():
    client.post("/v1/chat", json={"prompt": "metrics please"}, headers=H)
    body = client.get("/metrics/").text
    assert "semantic_cache_lookups_total" in body and "api_request_latency_seconds" in body


class Boom:
    name = "boom"

    def generate(self, prompt):
        raise TimeoutError("provider down")


def test_circuit_breaker_opens_and_falls_back():
    svc = LLMService(provider=Boom(), breaker=CircuitBreaker(fail_max=2, reset_s=60))
    for _ in range(2):
        assert svc.generate("x") == (FALLBACK_MESSAGE, "fallback")
    assert svc.breaker.state == "open"
    # while open, the provider isn't even called
    assert svc.generate("x")[1] == "fallback"


def test_breaker_half_open_recovers():
    br = CircuitBreaker(fail_max=1, reset_s=0)
    br.failure()
    assert br.state == "half_open" and br.allow()
    br.success()
    assert br.state == "closed"


def test_cache_disabled_when_embedder_cannot_load():
    """Graceful degradation: a broken embedding model must not crash the API."""
    from app.cache import MemoryStore, SemanticCache

    class Settings:
        embedder = "fastembed"
        embed_model = "does-not-exist/model"

    from app.cache import build_embedder
    assert build_embedder(Settings()) is None

    disabled = SemanticCache(MemoryStore(), None, 0.9, 60)
    disabled.set("hello", "world")  # no-op, no crash
    assert disabled.get("hello") == (None, 0.0)
