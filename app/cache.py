"""Semantic cache: embed the prompt, find the most similar cached prompt,
return its answer if cosine similarity >= threshold.

Same idea as GPTCache, written by hand so you can see every moving part.
Invalidation: TTL per entry + CACHE_VERSION namespace + admin DELETE endpoint.
"""
import hashlib
import logging
import re
import time

import numpy as np

log = logging.getLogger("cache")

_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def numbers_in(text: str) -> list[str]:
    """Embeddings barely distinguish "test 1" from "test 3", so cached answers are only
    reused when both prompts contain exactly the same numbers (a guard against false hits)."""
    return sorted(_NUMBER.findall(text))


# ---------- embedders ----------
class FakeEmbedder:
    """Hashed bag-of-words. No downloads -> perfect for unit tests and CI."""

    dim = 256

    def embed(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        for tok in text.lower().split():
            tok = tok.strip("?.!,;:'\"")
            if tok:
                v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % self.dim] += 1
        n = np.linalg.norm(v)
        return v / n if n else v


class FastEmbedder:
    """Real sentence embeddings via fastembed (ONNX, CPU-only, ~100 MB model)."""

    def __init__(self, model_name: str):
        from fastembed import TextEmbedding

        self.model = TextEmbedding(model_name=model_name)

    def embed(self, text: str) -> np.ndarray:
        v = np.asarray(next(iter(self.model.embed([text]))), dtype=np.float32)
        return v / np.linalg.norm(v)


# ---------- storage backends ----------
class MemoryStore:
    def __init__(self) -> None:
        self.data: dict[str, tuple[str, str, np.ndarray, float]] = {}

    def add(self, eid, prompt, response, emb, ttl):
        self.data[eid] = (prompt, response, emb, time.time() + ttl)

    def all(self):
        now = time.time()
        for eid in [k for k, v in self.data.items() if v[3] < now]:
            del self.data[eid]
        return [(p, r, e) for p, r, e, _ in self.data.values()]

    def clear(self) -> int:
        n = len(self.data)
        self.data.clear()
        return n

    def ping(self) -> bool:
        return True


class RedisStore:
    def __init__(self, url: str, namespace: str):
        import redis

        self.r = redis.Redis.from_url(url)
        self.ns = namespace
        self.ids_key = f"{namespace}:ids"

    def add(self, eid, prompt, response, emb, ttl):
        key = f"{self.ns}:{eid}"
        pipe = self.r.pipeline()
        pipe.hset(key, mapping={"prompt": prompt, "response": response, "emb": emb.tobytes()})
        pipe.expire(key, ttl)
        pipe.sadd(self.ids_key, eid)
        pipe.execute()

    def all(self):
        out = []
        for raw in self.r.smembers(self.ids_key):
            eid = raw.decode()
            d = self.r.hgetall(f"{self.ns}:{eid}")
            if not d:  # expired by TTL -> drop from index
                self.r.srem(self.ids_key, eid)
                continue
            out.append((d[b"prompt"].decode(), d[b"response"].decode(),
                        np.frombuffer(d[b"emb"], dtype=np.float32)))
        return out

    def clear(self) -> int:
        ids = [i.decode() for i in self.r.smembers(self.ids_key)]
        if ids:
            self.r.delete(*[f"{self.ns}:{i}" for i in ids])
        self.r.delete(self.ids_key)
        return len(ids)

    def ping(self) -> bool:
        try:
            return bool(self.r.ping())
        except Exception:  # noqa: BLE001
            return False


# ---------- the cache ----------
class SemanticCache:
    def __init__(self, store, embedder, threshold: float, ttl_s: int):
        self.store, self.embedder = store, embedder
        self.threshold, self.ttl_s = threshold, ttl_s

    @property
    def enabled(self) -> bool:
        return self.embedder is not None

    def get(self, prompt: str) -> tuple[str | None, float]:
        if not self.enabled:
            return None, 0.0
        q = self.embedder.embed(prompt)
        q_nums = numbers_in(prompt)
        best_resp, best_score = None, 0.0
        # Brute-force cosine search. Fine for thousands of entries; use a vector index
        # (Redis Stack / Qdrant) beyond that.
        for cached_prompt, resp, emb in self.store.all():
            if emb.shape != q.shape or numbers_in(cached_prompt) != q_nums:
                continue
            score = float(np.dot(q, emb))
            if score > best_score:
                best_resp, best_score = resp, score
        if best_score >= self.threshold:
            return best_resp, best_score
        return None, best_score

    def set(self, prompt: str, response: str) -> None:
        if not self.enabled:
            return
        eid = hashlib.sha256(prompt.encode()).hexdigest()[:16]
        self.store.add(eid, prompt, response, self.embedder.embed(prompt), self.ttl_s)


def build_embedder(settings):
    """Returns an embedder, or None if it can't load. A broken cache must never stop the
    API from starting: without an embedder we skip caching and every request goes to the LLM."""
    if settings.embedder != "fastembed":
        return FakeEmbedder()
    try:
        return FastEmbedder(settings.embed_model)
    except Exception as e:  # noqa: BLE001
        log.error("Embedding model failed to load (%s) - semantic cache DISABLED", e)
        return None


def build_cache(settings) -> SemanticCache:
    ns = f"semcache:{settings.cache_version}:{settings.llm_model}"
    store = RedisStore(settings.redis_url, ns) if settings.redis_url else MemoryStore()
    return SemanticCache(store, build_embedder(settings), settings.cache_threshold, settings.cache_ttl_s)
