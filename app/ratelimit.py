"""Fixed-window rate limiter + daily quota.
Uses Redis when available so limits are shared across replicas (horizontal scaling);
falls back to in-process memory for tests."""
import time


class FixedWindowLimiter:
    def __init__(self, limit: int, window_s: int, redis_url: str = "", prefix: str = "rl"):
        self.limit, self.window_s, self.prefix = limit, window_s, prefix
        self.mem: dict[str, int] = {}
        self.r = None
        if redis_url:
            import redis

            self.r = redis.Redis.from_url(redis_url)

    def allow(self, key: str) -> bool:
        bucket = f"{self.prefix}:{key}:{int(time.time() // self.window_s)}"
        if self.r is not None:
            pipe = self.r.pipeline()
            pipe.incr(bucket)
            pipe.expire(bucket, self.window_s)
            count = pipe.execute()[0]
        else:
            self.mem[bucket] = self.mem.get(bucket, 0) + 1
            count = self.mem[bucket]
        return count <= self.limit
