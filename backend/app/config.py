from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://dezon:dezon@localhost:5433/dezon"
    redis_url: str = "redis://localhost:6379/0"

    llm_provider: str = "mock"  # mock | anthropic
    llm_api_key: str = ""
    llm_model: str = "claude-sonnet-5-5"

    embedding_provider: str = "hash"  # hash | openai
    embedding_model: str = "text-embedding-3-small"
    embedding_api_key: str = ""
    embedding_dim: int = 1536

    dezon_api_base_url: str = ""
    dezon_api_token: str = ""
    dezon_site_url: str = "https://dezon.vn"

    stale_days: int = 180
    allowed_origins: str = "https://dezon.vn,http://localhost:5173,http://localhost:8080"
    admin_token: str = "change-me"
    chat_rate_limit_per_min: int = 20
    max_tool_rounds: int = 6

    s3_endpoint: str = ""
    s3_bucket: str = ""
    s3_key: str = ""
    s3_secret: str = ""

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
