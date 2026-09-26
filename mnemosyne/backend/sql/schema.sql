-- =============================================================================
-- Mnemosyne Database Schema
-- PostgreSQL DDL for the Mnemosyne personalized learning platform.
-- Assumes a PostgreSQL 14+ database with pgcrypto extension for UUID generation.
-- =============================================================================

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ---------------------------------------------------------------------------
-- Table: users
-- Core user accounts. Each user has a unique email and optional learning style
-- preference that helps the AI adapt pedagogical approach.
-- ---------------------------------------------------------------------------
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           TEXT NOT NULL UNIQUE,
    learning_style  TEXT,  -- e.g., 'text', 'kinesthetic', 'visual', 'auditory'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_users_email ON users (email);

-- ---------------------------------------------------------------------------
-- Table: study_sets
-- A collection of flashcards grouped by topic or subject area. Each set is
-- owned by exactly one user and can optionally be tagged with a topic for
-- organizational purposes.
-- ---------------------------------------------------------------------------
CREATE TABLE study_sets (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    topic       TEXT,  -- optional subject classification
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_study_sets_user_id ON study_sets (user_id);
CREATE INDEX idx_study_sets_topic ON study_sets (topic);

-- ---------------------------------------------------------------------------
-- Table: cards
-- Individual flashcards belonging to a study set. Each card contains a
-- question (prompt) and an answer (the target knowledge to be recalled).
--
-- `source` records provenance, added by migration 0005: 'manual' for a card
-- typed in through POST /cards, 'topic' for one generated from free text by
-- POST /study_sets/{set_id}/generate_cards, and 'knowledge_store' for one
-- generated from a concept node by POST /cards/from_node. As with
-- quiz_questions, source_node_id deliberately has NO foreign key — the
-- Knowledge Store is a separate database on the same cluster and Postgres
-- cannot enforce referential integrity across databases.
-- ---------------------------------------------------------------------------
CREATE TABLE cards (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    set_id          UUID NOT NULL REFERENCES study_sets(id) ON DELETE CASCADE,
    question        TEXT NOT NULL,
    answer          TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'manual'
                        CHECK (source IN ('manual', 'topic', 'knowledge_store')),
    source_node_id  UUID,  -- ks.nodes(id) when source='knowledge_store'; no FK, different database
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT cards_node_id_iff_knowledge_store
        CHECK ((source = 'knowledge_store') = (source_node_id IS NOT NULL)),

    -- One card per concept per study set — the idempotency key for
    -- POST /cards/from_node. NULLs are distinct in Postgres, so manual and
    -- topic-generated cards (source_node_id IS NULL) are unconstrained.
    CONSTRAINT cards_set_node_unique UNIQUE (set_id, source_node_id)
);

CREATE INDEX idx_cards_set_id ON cards (set_id);

-- ---------------------------------------------------------------------------
-- Table: learning_events
-- Records every review attempt a user makes on a card. This is the primary
-- data table for the FSRS spaced repetition algorithm. Each event captures
-- the user's response, correctness, and the resulting scheduler state
-- (stability, difficulty, interval, next review date).
--
-- FSRS state columns (stability, difficulty) were added by migration
-- 0001_add_fsrs_fields.sql following ADR 0001. The legacy ease_factor column
-- is retained but unused post-FSRS-adoption; see migration 0001 for details.
-- ---------------------------------------------------------------------------
CREATE TABLE learning_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    card_id         UUID NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    response        TEXT,  -- the user's free-form answer, if provided
    is_correct      BOOLEAN NOT NULL,
    ease_factor     FLOAT NOT NULL DEFAULT 2.5,  -- LEGACY/UNUSED post-FSRS (see ADR 0001 + migration 0001); FSRS uses stability + difficulty instead
    stability       FLOAT,  -- FSRS: days until recall drops from 100% to 90% (nullable for pre-FSRS rows)
    difficulty       FLOAT,  -- FSRS: inherent card hardness 1-10, mean-reverting (nullable for pre-FSRS rows)
    interval        INTEGER NOT NULL DEFAULT 0,  -- days until next review
    next_review_at  TIMESTAMPTZ NOT NULL,  -- when this card should next be reviewed
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_learning_events_card_id ON learning_events (card_id);
CREATE INDEX idx_learning_events_user_id ON learning_events (user_id);
CREATE INDEX idx_learning_events_next_review_at ON learning_events (next_review_at);
CREATE INDEX idx_learning_events_user_card ON learning_events (user_id, card_id);

-- ---------------------------------------------------------------------------
-- Table: ai_interactions
-- Logs all exchanges between the user and the DeepSeek AI tutor. Used for
-- cost tracking, quality monitoring, and improving the AI's pedagogical
-- effectiveness over time. The interaction_type enum classifies the
-- pedagogical purpose of each exchange.
-- ---------------------------------------------------------------------------

-- Define the interaction type enum
CREATE TYPE ai_interaction_type AS ENUM (
    'question_generation',
    'socratic_dialogue',
    'feynman_evaluation',
    'quiz_generation',
    'card_from_node_generation',
    'ask_answer',
    'solve_steps',
    'blurting_evaluation'
);

CREATE TABLE ai_interactions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    interaction_type    ai_interaction_type NOT NULL,
    input_text          TEXT NOT NULL,   -- the user's message to the AI
    output_text         TEXT NOT NULL,   -- the AI's response
    tokens_used         INTEGER NOT NULL DEFAULT 0,  -- LLM token count for cost tracking
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_ai_interactions_user_id ON ai_interactions (user_id);
CREATE INDEX idx_ai_interactions_type ON ai_interactions (interaction_type);
CREATE INDEX idx_ai_interactions_created_at ON ai_interactions (created_at);

-- ---------------------------------------------------------------------------
-- Table: socratic_sessions
-- One row per Socratic dialogue session. A session is scoped to a study_set
-- (the student is exploring/being questioned on that set's topic as a whole,
-- not a single card). Added by migration 0002_add_socratic_tables.sql.
-- ---------------------------------------------------------------------------
CREATE TABLE socratic_sessions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    set_id      UUID NOT NULL REFERENCES study_sets(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Set by POST /socratic/{id}/end. "When" answers "whether" too, so there
    -- is no separate boolean to fall out of step with it.
    ended_at    TIMESTAMPTZ
);

CREATE INDEX idx_socratic_sessions_user_id ON socratic_sessions (user_id);
CREATE INDEX idx_socratic_sessions_set_id ON socratic_sessions (set_id);

-- ---------------------------------------------------------------------------
-- Table: socratic_messages
-- Individual turns within a session, ordered by created_at. 'role' distinguishes
-- the student's messages from the AI tutor's. 'flagged_misconception' is
-- populated ONLY on assistant-role rows where the AI detected a specific
-- misconception in the student's preceding message; NULL otherwise (including
-- on all user-role rows, and on assistant-role rows where no misconception was
-- detected). Added by migration 0002_add_socratic_tables.sql.
-- ---------------------------------------------------------------------------
CREATE TABLE socratic_messages (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id              UUID NOT NULL REFERENCES socratic_sessions(id) ON DELETE CASCADE,
    role                    TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content                 TEXT NOT NULL,
    flagged_misconception   TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_socratic_messages_session_id ON socratic_messages (session_id);
CREATE INDEX idx_socratic_messages_session_created ON socratic_messages (session_id, created_at);

-- ---------------------------------------------------------------------------
-- Table: feynman_evaluations
-- One row per Feynman Technique submission: a student writes a free-text
-- explanation of a study_set's topic in their own words, and the AI evaluates
-- it on three dimensions (clarity, completeness, correctness), each scored
-- 1-10, plus free-text feedback and improvement suggestions. Scoped to a
-- study_set (the student explains the topic as a whole), not a single card.
-- Added by migration 0003_add_feynman_evaluations.sql.
--
-- Storing structured scores (not just a log entry) is intentional: this
-- supports tracking a user's explanation quality over time for the same
-- study_set, which is a planned evaluation metric (see docs/research.md §5).
-- ---------------------------------------------------------------------------
CREATE TABLE feynman_evaluations (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id              UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    set_id               UUID NOT NULL REFERENCES study_sets(id) ON DELETE CASCADE,
    explanation_text     TEXT NOT NULL,          -- the student's own-words explanation
    clarity_score        INTEGER NOT NULL CHECK (clarity_score BETWEEN 1 AND 10),
    completeness_score   INTEGER NOT NULL CHECK (completeness_score BETWEEN 1 AND 10),
    correctness_score    INTEGER NOT NULL CHECK (correctness_score BETWEEN 1 AND 10),
    feedback             TEXT NOT NULL,          -- overall AI feedback paragraph
    suggestions          TEXT NOT NULL,          -- specific improvement suggestions
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_feynman_evaluations_user_id ON feynman_evaluations (user_id);
CREATE INDEX idx_feynman_evaluations_set_id ON feynman_evaluations (set_id);
CREATE INDEX idx_feynman_evaluations_user_set ON feynman_evaluations (user_id, set_id, created_at);

-- ---------------------------------------------------------------------------
-- Table: quiz_questions
-- Multiple-choice questions generated by an LLM. Added by migration
-- 0004_add_quiz_tables.sql.
--
-- Quiz is the one methodology whose grading needs no model: the answer is an
-- index, so checking it is a comparison. Socratic is a dialogue and Feynman is
-- free text scored by the LLM; a quiz answer is objectively checkable, and
-- grading must not cost a token or vary between runs.
--
-- `source` records provenance: 'topic' for questions generated from free text
-- the user supplied, 'knowledge_store' for questions generated from a concept
-- node already in the Chiron Knowledge Store. There is deliberately no foreign
-- key on source_node_id — the Knowledge Store is a separate database
-- (chiron_ks) on the same cluster, and Postgres cannot enforce referential
-- integrity across databases. The column is a traceability breadcrumb.
-- ---------------------------------------------------------------------------
CREATE TABLE quiz_questions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    set_id          UUID NOT NULL REFERENCES study_sets(id) ON DELETE CASCADE,
    question        TEXT NOT NULL,
    choices         JSONB NOT NULL,   -- array of strings, one per option
    correct_index   INTEGER NOT NULL, -- 0-based index into choices
    source          TEXT NOT NULL CHECK (source IN ('topic', 'knowledge_store')),
    source_node_id  UUID,             -- ks.nodes(id) when source='knowledge_store'; no FK, different database
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT quiz_questions_choices_is_array
        CHECK (jsonb_typeof(choices) = 'array'),
    CONSTRAINT quiz_questions_choices_min_length
        CHECK (jsonb_array_length(choices) >= 2),
    CONSTRAINT quiz_questions_correct_index_in_range
        CHECK (correct_index >= 0 AND correct_index < jsonb_array_length(choices)),
    CONSTRAINT quiz_questions_node_id_iff_knowledge_store
        CHECK ((source = 'knowledge_store') = (source_node_id IS NOT NULL))
);

CREATE INDEX idx_quiz_questions_set_id ON quiz_questions (set_id);
CREATE INDEX idx_quiz_questions_source ON quiz_questions (source);

-- ---------------------------------------------------------------------------
-- Table: quiz_attempts
-- One row per submitted answer. `is_correct` is stored rather than derived on
-- read: it is computed once at answer time from the question as it existed
-- then, so later edits to a question cannot retroactively rewrite a learner's
-- history. Added by migration 0004_add_quiz_tables.sql.
-- ---------------------------------------------------------------------------
CREATE TABLE quiz_attempts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    question_id     UUID NOT NULL REFERENCES quiz_questions(id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    selected_index  INTEGER NOT NULL CHECK (selected_index >= 0),
    is_correct      BOOLEAN NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_quiz_attempts_question_id ON quiz_attempts (question_id);
CREATE INDEX idx_quiz_attempts_user_id ON quiz_attempts (user_id);
CREATE INDEX idx_quiz_attempts_user_created ON quiz_attempts (user_id, created_at);

-- ---------------------------------------------------------------------------
-- Table: todo_items
-- The learner's review todo list. A study set whose cards keep being failed
-- gets one 'weak_card' item (see backend/src/weak_cards.rs); the learner can
-- also add 'manual' items. Nothing schedules them — the learner ticks them off.
-- At most one OPEN weak-card item per set: the partial unique index is what
-- stops two weak reviews landing at once from opening two. Added by migration
-- 0010_replace_weak_card_tasks_with_todos.sql, which replaced
-- weak_card_tasks.
-- ---------------------------------------------------------------------------
CREATE TABLE todo_items (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id            UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    study_set_id       UUID REFERENCES study_sets(id) ON DELETE CASCADE,
    title              TEXT NOT NULL CHECK (btrim(title) <> ''),
    source             TEXT NOT NULL CHECK (source IN ('weak_card', 'manual')),
    done               BOOLEAN NOT NULL DEFAULT false,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    done_at            TIMESTAMPTZ,
    last_weak_card_at  TIMESTAMPTZ,          -- weak-card items: bumped by every weak review in the set
    CHECK (done = (done_at IS NOT NULL)),
    CHECK (source <> 'weak_card' OR (study_set_id IS NOT NULL AND last_weak_card_at IS NOT NULL))
);

CREATE UNIQUE INDEX todo_items_open_weak_per_set
    ON todo_items (study_set_id)
    WHERE done = false AND source = 'weak_card';
CREATE INDEX idx_todo_items_user_done ON todo_items (user_id, done, created_at DESC);

-- ---------------------------------------------------------------------------
-- Table: todo_item_cards
-- Which cards a weak-card todo item lists — the evidence the Weak spots screen
-- shows. Append-only while the item is open: a card that recovers stays
-- listed until the learner ticks the item off. Added by migration 0010.
-- ---------------------------------------------------------------------------
CREATE TABLE todo_item_cards (
    todo_id   UUID NOT NULL REFERENCES todo_items(id) ON DELETE CASCADE,
    card_id   UUID NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    added_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (todo_id, card_id)
);

CREATE INDEX idx_todo_item_cards_card_id ON todo_item_cards (card_id);

-- ---------------------------------------------------------------------------
-- Table: user_tokens
-- API tokens, one or more per learner. Endpoints derive `user_id` from the
-- token instead of believing a `user_id` field in the request. Only the
-- SHA-256 hash is stored — the token itself is shown once, when minted by the
-- `mint-token` CLI command, and cannot be recovered afterwards. SHA-256 rather
-- than argon2 because these are 32-byte CSPRNG outputs, not passwords: there
-- is no dictionary to attack, and a work factor would be paid on every request.
-- ---------------------------------------------------------------------------
CREATE TABLE user_tokens (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash    TEXT NOT NULL UNIQUE,  -- hex SHA-256; UNIQUE doubles as the lookup index
    label         TEXT,                  -- "laptop", "KS card-sync" — which token to revoke
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at  TIMESTAMPTZ            -- bumped by the auth lookup itself
);

CREATE INDEX idx_user_tokens_user_id ON user_tokens (user_id);

-- ---------------------------------------------------------------------------
-- Tables: chat_sessions, chat_messages
-- "Ask" (direct answers) and "Solve" (worked step by step) — the two
-- modes that are the opposite of Socratic, which refuses to answer. One pair
-- of tables with a `mode` column, because the storage is identical; kept apart
-- from socratic_sessions because the lifecycle is not (no study-set
-- requirement, no teaching-method turn cap, no transcript hand-off to KS).
-- ---------------------------------------------------------------------------
CREATE TYPE chat_mode AS ENUM ('ask', 'solve');

CREATE TABLE chat_sessions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mode        chat_mode NOT NULL,
    set_id      UUID REFERENCES study_sets(id) ON DELETE SET NULL,  -- optional context
    title       TEXT NOT NULL,        -- opening message, trimmed; what the sidebar lists
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()  -- bumped per message: "recently used"
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

-- ---------------------------------------------------------------------------
-- Tables: blurting_attempts, blurting_attempt_cards
-- Blurting (brain dump): the learner writes what they remember of a study set
-- without looking; the AI judges each card remembered / missing / wrong. See
-- backend/src/handlers/blurting.rs. Added by migration 0011_add_blurting.sql.
-- ---------------------------------------------------------------------------
CREATE TABLE blurting_attempts (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    set_id            UUID NOT NULL REFERENCES study_sets(id) ON DELETE CASCADE,
    recall_text       TEXT NOT NULL,          -- what the learner wrote, unaided
    feedback          TEXT NOT NULL,          -- short overall comment, in English
    -- Cards the AI was shown. The prompt caps card context, so a large set is
    -- judged on its first cards only; recorded so the result never implies more.
    cards_considered  INTEGER NOT NULL CHECK (cards_considered >= 0),
    cards_total       INTEGER NOT NULL CHECK (cards_total >= cards_considered),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_blurting_attempts_user_set ON blurting_attempts (user_id, set_id, created_at);

CREATE TABLE blurting_attempt_cards (
    attempt_id  UUID NOT NULL REFERENCES blurting_attempts(id) ON DELETE CASCADE,
    card_id     UUID NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    verdict     TEXT NOT NULL CHECK (verdict IN ('remembered', 'missing', 'wrong')),
    -- For 'wrong': what the learner got wrong. Empty otherwise.
    note        TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (attempt_id, card_id)
);

CREATE INDEX idx_blurting_attempt_cards_card_id ON blurting_attempt_cards (card_id);
