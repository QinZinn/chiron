-- 0012: English title for weak-card todo items.
--
-- The UI and prompts moved to English. Items opened before that still carry
-- the old Vietnamese title (written by migration 0010 and by the backend's
-- WEAK_TODO_TITLE until now). Rename them so an existing to-do list does not
-- mix both languages. Only untouched weak-card items are matched: a title a
-- learner has edited is left alone.

UPDATE todo_items
SET title = 'Review weak cards'
WHERE source = 'weak_card'
  AND title = 'Ôn lại các thẻ đang yếu';
