-- 0010: the weak-card Todoist task becomes an internal todo item.
--
-- Chiron no longer talks to Todoist (nor to Google Calendar or Horae). A study
-- set whose cards keep being failed now gets a row in `todo_items` instead of
-- a Todoist task, and the learner ticks it off in Chiron. Nothing schedules
-- these items: they are a list, the learner decides when.
--
-- `todo_item_cards` keeps which cards made a set weak — the same evidence
-- `weak_card_task_cards` held — because the Điểm yếu screen (GET /weak_cards)
-- is built from it. Existing rows are carried over before the old tables go,
-- so no weak-card history is lost.

CREATE TABLE todo_items (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id            UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    study_set_id       UUID REFERENCES study_sets(id) ON DELETE CASCADE,
    title              TEXT NOT NULL CHECK (btrim(title) <> ''),
    source             TEXT NOT NULL CHECK (source IN ('weak_card', 'manual')),
    done               BOOLEAN NOT NULL DEFAULT false,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    done_at            TIMESTAMPTZ,
    -- Weak-card items only: bumped by every weak review in the set, so the
    -- list can show the most recently struggling set first.
    last_weak_card_at  TIMESTAMPTZ,
    CHECK (done = (done_at IS NOT NULL)),
    CHECK (source <> 'weak_card' OR (study_set_id IS NOT NULL AND last_weak_card_at IS NOT NULL))
);

-- At most one open weak-card item per set: a second weak card joins the open
-- item instead of adding another. The same rule the Todoist task had, for the
-- same reason — one struggling set must not spam the list.
CREATE UNIQUE INDEX todo_items_open_weak_per_set
    ON todo_items (study_set_id)
    WHERE done = false AND source = 'weak_card';
CREATE INDEX idx_todo_items_user_done ON todo_items (user_id, done, created_at DESC);

CREATE TABLE todo_item_cards (
    todo_id   UUID NOT NULL REFERENCES todo_items(id) ON DELETE CASCADE,
    card_id   UUID NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    added_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (todo_id, card_id)
);
CREATE INDEX idx_todo_item_cards_card_id ON todo_item_cards (card_id);

INSERT INTO todo_items (id, user_id, study_set_id, title, source, done, created_at, done_at, last_weak_card_at)
SELECT t.id, s.user_id, t.study_set_id, 'Ôn lại các thẻ đang yếu', 'weak_card',
       t.closed_at IS NOT NULL, t.opened_at, t.closed_at, t.last_weak_card_at
FROM weak_card_tasks t
JOIN study_sets s ON s.id = t.study_set_id;

INSERT INTO todo_item_cards (todo_id, card_id, added_at)
SELECT task_row_id, card_id, added_at FROM weak_card_task_cards;

DROP TABLE weak_card_task_cards;
DROP TABLE weak_card_tasks;
