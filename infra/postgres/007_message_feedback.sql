-- Thumbs up/down signal on assistant answers — the only feedback loop this
-- repo has on whether RAG citations/answers are actually good. Nullable with
-- no default: null means no feedback given yet, distinct from an explicit
-- 'up'/'down'. Feedback only makes sense on assistant turns, but there's no
-- CHECK tying feedback to role='assistant' — the API route is the only
-- writer and only ever targets assistant messages, so enforcing it in SQL
-- too would be redundant defense for a column nothing else writes to.
ALTER TABLE chat_messages ADD COLUMN feedback TEXT CHECK (feedback IN ('up', 'down'));
