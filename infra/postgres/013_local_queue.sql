-- Desktop mode (QUEUE_BACKEND=postgres) uses event_log itself as the job
-- queue instead of RabbitMQ; see services/worker/app/local_queue.py.
-- Harmless in server mode. Also applied at API startup by app/core/schema.py.

ALTER TABLE event_log ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE event_log ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ NOT NULL DEFAULT now();
CREATE INDEX IF NOT EXISTS idx_event_log_queue ON event_log (status, available_at);
