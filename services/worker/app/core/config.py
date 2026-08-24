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
    # Jira fetch, chunking, DB writes), not just the Voyage call. Kept as a
    # secondary safety net; the actual rate-limit fix is
    # voyage_requests_per_minute below — a concurrency cap alone doesn't
    # prevent bursting past a per-minute quota, it only bounds how many
    # requests are in flight at once regardless of how tightly clustered in
    # time they are.
    voyage_max_concurrent_requests: int = 3
    # Voyage's real limit for this key, confirmed from the API's own
    # X-Api-Warning response header: accounts with no payment method on file
    # (see https://dashboard.voyageai.com/) are capped at 3 RPM / 10K TPM —
    # deliberately restrictive, not a bug on Voyage's side. Adding a payment
    # method (free token allotment still applies) raises this considerably;
    # bump this setting to match once that's done. Until then, every
    # embed_texts() call is proactively paced to this rate rather than fired
    # immediately and retried after a 429 — Voyage's own Retry-After header
    # was observed reporting ~1s, far shorter than the ~20s spacing a 3 RPM
    # budget actually needs, so trusting it for backoff timing was the reason
    # the previous concurrency-only fix still exhausted its retry budget and
    # dead-lettered.
    voyage_requests_per_minute: int = 3
    embedding_dimensions: int = 512
    notion_api_version: str = "2022-06-28"

    # Needed here (not just in the api service) because the worker refreshes
    # expired Jira access tokens itself before each Jira API call.
    jira_client_id: str = ""
    jira_client_secret: str = ""


settings = Settings()
