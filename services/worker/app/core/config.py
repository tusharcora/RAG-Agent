from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "local"
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://backbone:backbone@localhost:5432/backbone"
    redis_url: str = "redis://localhost:6379/0"
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"

    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_service_name: str = "worker"

    # How many events this worker consumes concurrently. Bump for I/O-bound
    # tasks (embedding calls, LLM calls); keep low for CPU-heavy work.
    prefetch_count: int = 10

    # Retry policy before a message is routed to the dead-letter exchange
    max_retries: int = 3

    dedupe_ttl_seconds: int = 60 * 60 * 24

    # --- RAG agent ---
    voyage_api_key: str = ""
    embedding_model: str = "voyage-3-lite"
    # Caps concurrent in-flight Voyage embedding requests specifically —
    # prefetch_count above already bounds overall in-flight message
    # concurrency, but that gates the whole per-document pipeline (Notion/
    # Jira fetch, chunking, DB writes), not just the Voyage call. A bursty
    # sync (many documents landing at once) can still fire prefetch_count
    # concurrent embed_texts() calls even with prefetch_count left generous
    # for throughput elsewhere, which is exactly what produced the
    # "Voyage rate limited" dead-letters seen in production. This semaphore
    # paces proactively instead of relying solely on embed_texts()'s
    # reactive 429-then-retry in app/integrations/voyage.py.
    voyage_max_concurrent_requests: int = 3
    embedding_dimensions: int = 512
    notion_api_version: str = "2022-06-28"

    # Needed here (not just in the api service) because the worker refreshes
    # expired Jira access tokens itself before each Jira API call.
    jira_client_id: str = ""
    jira_client_secret: str = ""


settings = Settings()
