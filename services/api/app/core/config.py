from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "local"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://backbone:backbone@localhost:5432/backbone"
    redis_url: str = "redis://localhost:6379/0"
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"

    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_service_name: str = "api"

    cors_origins: list[str] = ["http://localhost:3000"]
    frontend_url: str = "http://localhost:3000"

    # Idempotency / dedupe
    event_dedupe_ttl_seconds: int = 60 * 60 * 24  # 24h

    # --- RAG agent ---
    google_api_key: str = ""  # Google AI Studio key, used for query generation (Gemini)
    voyage_api_key: str = ""

    notion_client_id: str = ""
    notion_client_secret: str = ""
    notion_redirect_uri: str = "http://localhost:8000/oauth/notion/callback"
    notion_api_version: str = "2022-06-28"

    jira_client_id: str = ""
    jira_client_secret: str = ""
    jira_redirect_uri: str = "http://localhost:8000/oauth/jira/callback"

    embedding_model: str = "voyage-3-lite"
    embedding_dimensions: int = 512

    query_model: str = "gemini-3.6-flash"
    query_top_k: int = 5

    session_history_ttl_seconds: int = 60 * 60  # 1h
    session_history_max_turns: int = 10

    oauth_state_ttl_seconds: int = 600  # 10min

    max_pages_per_sync: int = 200
    max_issues_per_sync: int = 200

    rate_limit_query_per_minute: int = 10
    rate_limit_sync_per_minute: int = 2

    # Empty = disabled (local dev default). Set to require X-API-Key on /sync/* and /query.
    api_shared_secret: str = ""


settings = Settings()
