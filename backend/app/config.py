from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Đọc .env ở gốc repo (khi chạy từ backend/) và .env cạnh tiến trình; biến môi trường thật luôn ưu tiên.
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    database_url: str = "postgresql+psycopg://dezon:dezon@localhost:5433/dezon"
    redis_url: str = "redis://localhost:6379/0"

    # auto: có API key OpenAI -> openai, không có -> mock (demo chạy được không cần key)
    llm_provider: str = "auto"  # auto | mock | openai | anthropic
    llm_api_key: str = Field(default="", validation_alias=AliasChoices("LLM_API_KEY", "OPENAI_API_KEY"))
    llm_model: str = Field(default="gpt-4.1-mini", validation_alias=AliasChoices("LLM_MODEL", "MODEL_NAME"))
    llm_base_url: str = ""  # tùy chọn: endpoint tương thích OpenAI khác

    # auto: có API key -> openai, không có -> hash (cục bộ, xác định)
    embedding_provider: str = "auto"  # auto | hash | openai
    embedding_model: str = "text-embedding-3-small"
    embedding_api_key: str = Field(default="",
                                   validation_alias=AliasChoices("EMBEDDING_API_KEY", "OPENAI_API_KEY"))
    embedding_dim: int = 1536
    # Endpoint /embeddings tương thích OpenAI khác (vd Azure/OpenRouter/proxy nội bộ); rỗng -> api.openai.com/v1
    embedding_api_base: str = ""

    dezon_api_base_url: str = ""
    dezon_api_token: str = ""
    dezon_site_url: str = "https://dezon.vn"

    app_env: str = "development"  # development | production
    import_dir: str = "data/imports"  # thư mục duy nhất admin được nhập file theo đường dẫn (K3)

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

    @property
    def admin_token_is_default(self) -> bool:
        return self.admin_token.strip() in ("", "change-me")

    @property
    def resolved_llm_provider(self) -> str:
        if self.llm_provider != "auto":
            return self.llm_provider
        return "openai" if self.llm_api_key else "mock"

    @property
    def resolved_embedding_provider(self) -> str:
        if self.embedding_provider != "auto":
            return self.embedding_provider
        return "openai" if self.embedding_api_key else "hash"


@lru_cache
def get_settings() -> Settings:
    return Settings()
