from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="GEONEWS_", extra="ignore")

    database_url: str = "postgresql+asyncpg://geonews:geonews@localhost:5432/geonews"
    redis_url: str = "redis://localhost:6379/0"

    # Models. Orchestrator/briefing default to Opus; subagents and classification use Haiku.
    orchestrator_model: str = "claude-opus-5-5"
    subagent_model: str = "claude-haiku-5-5"
    briefing_model: str = "claude-opus-5-5"
    judge_model: str = "claude-opus-5-5"
    classifier_backend: str = "claude"  # "claude" | "lora"
    lora_endpoint: str = "http://localhost:8001/v1/classify"

    # Embeddings: "fastembed" (real, local ONNX) or "hash" (deterministic, offline tests).
    embedding_backend: str = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384

    # Agent guardrails
    agent_max_steps: int = 8
    agent_max_verify_rounds: int = 1
    agent_token_budget: int = 120_000
    user_daily_cost_cap_usd: float = 1.00

    # Ingestion
    feeds_file: str = "feeds.txt"
    gdelt_enabled: bool = True
    gdelt_query: str = '("city council" OR county OR mayor OR "road closure") sourcelang:english'
    story_window_hours: int = 72
    dedupe_threshold: float = 0.6
    nominatim_url: str = "https://nominatim.openstreetmap.org/search"
    nominatim_user_agent: str = "geonews-news-agent/0.1 (contact: set GEONEWS_NOMINATIM_USER_AGENT)"

    # Delivery
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "geonews@localhost"
    telegram_bot_token: str = ""
    web_base_url: str = "http://localhost:3000"

    # Observability
    otel_endpoint: str = ""  # e.g. https://cloud.langfuse.com/api/public/otel
    otel_headers: str = ""  # "Authorization=Basic xxx"

    cors_origins: str = "http://localhost:3000"
    admin_token: str = "change-me"


@lru_cache
def get_settings() -> Settings:
    return Settings()
