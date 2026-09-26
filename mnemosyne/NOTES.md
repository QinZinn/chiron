# NOTES — An internal to-do list replacing Todoist, 2026-09-25

Code: `backend/src/weak_cards.rs`, `backend/src/handlers/todos.rs`, step 6 of
`backend/src/handlers/reviews.rs`, migration
`backend/sql/migrations/0010_replace_weak_card_tasks_with_todos.sql`.
The old design (`@ontap` Todoist tasks for Horae to schedule):
`docs/da-ngung-dung/weakpoint-todoist-2026-09-13.md`.

## Unchanged

The "weak card" rule: at least 40 % of the last 5 reviews were "again", and a card
with fewer than 5 reviews is not judged yet (`WEAK_CARD_WINDOW`,
`WEAK_CARD_ERROR_THRESHOLD`, `assess`). The last-5-reviews query is also unchanged.
Only the **write target** changed: the `todo_items` table instead of a Todoist call.

## Departures from the brief, and why

1. **An extra `todo_item_cards` table.** The brief only specified `todo_items`. But
   the Weak spots screen (`GET /weak_cards`) is built from the list of *which cards*
   made a set weak, and that list used to live in `weak_card_task_cards`. Dropping
   that table with no replacement would leave Weak spots without a data source.
   Migration 0010 copies both old tables across before the `DROP`.
2. **An extra `last_weak_card_at` column.** Every weak review in the set updates
   it, so the list shows the most recently weak set first and Weak spots can say
   "last weak card … ago". A CHECK makes the column mandatory for `weak_card` items.
3. **Parts 1 and 2 share one migration.** The brief wanted Todoist removed (part 1)
   and checked before the to-do list (part 2) was built. *Where* weak cards are
   written cannot be split: if part 1's migration dropped the old tables before the
   new one existed, weak-card detection would have nowhere to write. So part 1
   created the to-do table too and was checked first (a real review → a to-do item,
   no Todoist call). The `/todos` endpoints and the UI came after.
4. **`GET /todos` has no `user_id` parameter.** The brief said `GET /todos?user_id=`.
   Every learner route takes `user_id` from the bearer token; accepting it as a query
   parameter would let one token read another learner's to-dos. The
   `todos_are_scoped_to_the_token` test locks this in.
5. **No advisory lock needed.** The Todoist version had to lock per set because a
   network call sat between "no task yet" and the DB write. Now there is a single
   `INSERT … ON CONFLICT (study_set_id) WHERE done = false AND source = 'weak_card'`
   on the partial unique index `todo_items_open_weak_per_set` itself, so Postgres
   serialises two simultaneous reviews on its own. The test
   `weak_cards_concurrent_weak_cards_in_one_set_share_one_item` (two real
   connections, at the same time) yields 1 item with 2 cards.
6. **No auto-close after 5 days.** As the brief says: the learner ticks items off.
   A ticked item is not reopened; the next weak review in the set opens a new one
   (`weak_cards_a_ticked_off_item_is_replaced_by_the_next_weak_review`).
7. **The weak-card item title does not include the set name** ("Review weak cards";
   "Ôn lại các thẻ đang yếu" before the English switch, renamed by migration
   `0012`). The set name is joined in when the list is read, so renaming a set
   renames its item too.

## Tests

- Removed 9 DB tests that only checked Todoist behaviour: the fake client, updates,
  the sweep silently closing tasks, orphaned tasks, tasks completed in Todoist, a
  Todoist error rolling back. Also removed the e2e test
  `reviews_a_todoist_outage_does_not_break_the_review`, as there is no call left to fail.
- Added 5 DB tests in `weak_cards`, 3 in `handlers::todos`, and 1 e2e test
  `reviews_a_weak_card_files_one_todo_item`. That e2e test runs 6 reviews through
  the real handler and checks: exactly 1 item, the right card, the response matches the DB.
- Results on 2026-09-25 against the Compose Postgres: `cargo test --workspace`
  137 + 10 passed; `-- --ignored --skip live_` 27 passed.

## Threshold — still no real data

The two threshold queries in `docs/da-ngung-dung/weakpoint-todoist-2026-09-13.md`
(section "Validate ngưỡng") still apply to the 40 % / 5-review question. The DB
has no real study data yet, so they have not been run.


# NOTES — Blurting, 2026-09-26

Code: `backend/src/handlers/blurting.rs`, migration `0011_add_blurting.sql`;
the UI is a tab on the Teach back screen (`frontend/src/views/Blurting.tsx`).

## Decisions

1. **The model never sees `card_id`.** Each card in the prompt has a label `c1…cN`;
   the model returns labels and the server maps them back to real `card_id`s. The
   brief asked to "use real card_ids, never let the LLM invent concept names" and
   "unknown card_id → drop and log". Both still hold: every `card_id` stored or
   returned belongs to the right study set (by construction, and through the FK
   `blurting_attempt_cards.card_id → cards`); unknown labels (`c99`, `c0`, a UUID
   copied from somewhere) are dropped and logged as `[blurting] … dropped N label(s)`.
   Why not put UUIDs in the prompt: a 36-character string is exactly what a model
   copies wrong most often, and each card would cost about 20 more tokens.
2. **"Missed" is computed by the server,** not listed by the model: every card in the
   prompt that the model placed in neither "remembered" nor "wrong" is missed. So a
   card can never drop out of the result because the model forgot to mention it.
3. **A card that is both "remembered" and "wrong" counts as wrong:** the student
   wrote something false about it, and that is the part that needs to show.
4. **A 6000-character context cap, whole cards only,** like `/feynman_evaluate`.
   Larger sets are graded on their first cards only. The number of cards compared
   (`cards_considered`) is stored and shown, so the result never implies the whole
   set was graded.
5. **Truncation:** the provider returns `LLMError::Truncated` on
   `finish_reason=length`; the handler reports a 502 through `describe_llm_failure`
   and stores nothing. Test: `blurting_a_truncated_or_unparsable_reply_stores_nothing`.

## Real-world check (DeepSeek, 2026-09-26)

An 8-card set from the learner's Chemistry notes on "Fertilisers". The test text
had planted errors: it assigned Ca, Mg and S to the micronutrients, and said
organic fertiliser is "made from synthetic chemical fertiliser in a factory".
(This check ran before the English switch, with Vietnamese cards and feedback.)

- Result: 3 remembered (definition, macronutrients, deficiency), 1 missed (role),
  4 wrong (secondary nutrients, micronutrients, inorganic, organic). 23 s, 0 labels dropped.
- Against the answer key set in advance: 6/8 cards matched. Secondary nutrients and
  inorganic were graded "wrong" rather than "missed", because the learner had put
  those two cards' own content under a different card. The model counted one mix-up
  as wrong on both cards. All 4 "wrong" notes were factually correct.
