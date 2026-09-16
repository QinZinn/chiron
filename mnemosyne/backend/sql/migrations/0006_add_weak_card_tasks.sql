-- =============================================================================
-- Migration 0006: Track the Todoist review tasks filed for weak cards
-- =============================================================================
-- When a learner keeps failing a card (2 or more "again" in its last 5
-- reviews), POST /review files an @ontap task in Todoist for the card's study
-- set, and Horae schedules it. These tables remember which task belongs to
-- which set, and which cards it lists. See backend/src/weak_cards.rs.
--
-- One OPEN task per study set: weak cards found later join the set's open
-- task rather than opening another. A task closes itself once 5 days pass
-- with no weak review in its set, and a later weak card then opens a new one,
-- so a set accumulates closed rows over time but never two open ones.
--
-- This is a plain SQL authoring artifact. Apply it with:
--   psql -h 127.0.0.1 -p 5432 -U postgres -d mnemosyne -f <this file>
-- =============================================================================

CREATE TABLE weak_card_tasks (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    study_set_id       UUID NOT NULL REFERENCES study_sets(id) ON DELETE CASCADE,
    todoist_task_id    TEXT NOT NULL,       -- id Todoist returned at creation; needed to update/close it
    opened_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_weak_card_at  TIMESTAMPTZ NOT NULL DEFAULT now(),  -- bumped by every weak review in the set; the auto-close reads it
    closed_at          TIMESTAMPTZ,          -- NULL = open
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- At most one open task per set. Partial, so closed history rows for the same
-- set are allowed. Todoist has no server-side idempotency key, so this index
-- is what stands between a set and a duplicate task.
CREATE UNIQUE INDEX weak_card_tasks_open_per_set
    ON weak_card_tasks (study_set_id)
    WHERE closed_at IS NULL;

-- Covers the whole column (the partial index above does not), for the
-- ON DELETE CASCADE from study_sets.
CREATE INDEX idx_weak_card_tasks_study_set_id ON weak_card_tasks (study_set_id);

-- Which cards a task lists. Append-only for the life of the task: a card that
-- recovers stays listed until the task closes.
CREATE TABLE weak_card_task_cards (
    task_row_id  UUID NOT NULL REFERENCES weak_card_tasks(id) ON DELETE CASCADE,
    card_id      UUID NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    added_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (task_row_id, card_id)
);

-- For the ON DELETE CASCADE from cards; the primary key leads with task_row_id.
CREATE INDEX idx_weak_card_task_cards_card_id ON weak_card_task_cards (card_id);
