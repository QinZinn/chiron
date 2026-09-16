-- =============================================================================
-- Migration 0008: "Hỏi bài" and "Giải bài" conversations
-- =============================================================================
-- Two modes the frontend has always shown and never had a backend for:
--
--   ask    quick Q&A — the tutor answers directly, unlike the Socratic mode
--          whose whole point is refusing to
--   solve  work a problem step by step, then stay open for questions about
--          any one of those steps
--
-- They share one pair of tables with a `mode` column rather than getting a
-- table each: the storage is identical (an ordered transcript belonging to a
-- learner), and splitting it would mean writing every listing query twice.
--
-- Kept separate from socratic_sessions on purpose, though. A Socratic session
-- is tied to a study set, is capped at a turn count that reflects a teaching
-- method, and ships its transcript to the Knowledge Store on /end. None of
-- that is true here, and folding two different lifecycles into one table is
-- how a schema starts lying.
--
-- Apply with:
--   psql -h 127.0.0.1 -p 5432 -U postgres -d mnemosyne -f <this file>
-- =============================================================================

CREATE TYPE chat_mode AS ENUM ('ask', 'solve');

CREATE TABLE chat_sessions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mode        chat_mode NOT NULL,
    -- Optional: a study set whose cards are fed to the model as context. The
    -- learner may equally ask about something they have no cards for.
    set_id      UUID REFERENCES study_sets(id) ON DELETE SET NULL,
    -- First message, trimmed — what the sidebar shows. Stored rather than
    -- derived so listing sessions does not need to read their transcripts.
    title       TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Bumped on every message, so "recent" means recently used, not recently
    -- started.
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_chat_sessions_user_recent ON chat_sessions (user_id, updated_at DESC);
CREATE INDEX idx_chat_sessions_set_id ON chat_sessions (set_id);

CREATE TABLE chat_messages (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id  UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_chat_messages_session ON chat_messages (session_id, created_at);

-- Cost tracking for the two new modes, alongside the existing kinds.
ALTER TYPE ai_interaction_type ADD VALUE IF NOT EXISTS 'ask_answer';
ALTER TYPE ai_interaction_type ADD VALUE IF NOT EXISTS 'solve_steps';
