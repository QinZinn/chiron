-- 0011: Blurting (brain dump).
--
-- The learner picks a study set and writes down everything they remember,
-- without looking. The AI compares it with that set's cards and says, card by
-- card, what was remembered, what was missed and what was got wrong. Modelled
-- on feynman_evaluations: one row per attempt, kept for history.
--
-- The per-card verdicts live in their own table with a real foreign key to
-- `cards`, so the database itself guarantees that every card_id recorded for
-- an attempt is a real card. The handler also refuses any card outside the
-- attempt's own study set before anything is written.

ALTER TYPE ai_interaction_type ADD VALUE IF NOT EXISTS 'blurting_evaluation';

CREATE TABLE blurting_attempts (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    set_id            UUID NOT NULL REFERENCES study_sets(id) ON DELETE CASCADE,
    recall_text       TEXT NOT NULL,          -- what the learner wrote, unaided
    feedback          TEXT NOT NULL,          -- short overall comment, Vietnamese
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
