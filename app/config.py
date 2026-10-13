"""All settings come from environment variables (12-factor style).
Secrets (API keys) are NEVER hard-coded - pass them via .env locally,
GitHub Secrets in CI, or your cloud host's secret manager in production."""
import os


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


class Settings:
    def __init__(self) -> None:
        self.app_version = os.getenv("APP_VERSION", "0.1.0")

        # LLM: "anthropic" (hosted), "vllm" (self-hosted, OpenAI-compatible), or "mock" (tests/CI)
        self.llm_provider = os.getenv("LLM_PROVIDER", "mock")
        self.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self.llm_model = os.getenv("LLM_MODEL", "claude-haiku-5-5")
        self.vllm_base_url = os.getenv("VLLM_BASE_URL", "http://vllm:8000/v1")
        self.llm_timeout_s = float(os.getenv("LLM_TIMEOUT_S", "20"))
        self.max_tokens = int(os.getenv("MAX_TOKENS", "512"))

        # Cost tracking - set these from the provider's current pricing page (USD per 1M tokens)
        self.input_price_per_mtok = float(os.getenv("INPUT_PRICE_PER_MTOK", "1.0"))
        self.output_price_per_mtok = float(os.getenv("OUTPUT_PRICE_PER_MTOK", "5.0"))

        # Circuit breaker
        self.cb_fail_max = int(os.getenv("CB_FAIL_MAX", "3"))
        self.cb_reset_s = float(os.getenv("CB_RESET_S", "30"))

        # Semantic cache
        self.redis_url = os.getenv("REDIS_URL", "")  # empty -> in-memory store
        self.embedder = os.getenv("EMBEDDER", "fake")  # "fastembed" in Docker, "fake" in tests
        self.embed_model = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
        self.cache_threshold = float(os.getenv("CACHE_THRESHOLD", "0.90"))
        self.cache_ttl_s = int(os.getenv("CACHE_TTL_S", "86400"))
        # Bump CACHE_VERSION whenever the model or system prompt changes -> instant invalidation
        self.cache_version = os.getenv("CACHE_VERSION", "v1")

        # Security, rate limits, quotas
        self.api_keys = set(_list("API_KEYS", "dev-key"))
        self.admin_key = os.getenv("ADMIN_KEY", "admin-dev-key")
        self.rate_limit_per_min = int(os.getenv("RATE_LIMIT_PER_MIN", "30"))
        self.daily_quota = int(os.getenv("DAILY_QUOTA", "500"))

        # Governance: prompts are NOT logged unless explicitly enabled
        self.log_prompts = os.getenv("LOG_PROMPTS", "false").lower() == "true"


settings = Settings()
