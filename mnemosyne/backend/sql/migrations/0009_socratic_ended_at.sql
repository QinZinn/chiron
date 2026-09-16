-- =============================================================================
-- Migration 0009: remember when a Socratic session was closed
-- =============================================================================
-- `POST /socratic/{id}/end` ships the transcript to the Knowledge Store and
-- returns — it records nothing locally, so Mnemosyne itself has never known
-- which sessions are over. The browser kept that flag in localStorage, which
-- meant a second browser (or a cleared cache) saw every past session as still
-- running, and the new `GET /socratic` listing had nothing truthful to report.
--
-- Nullable rather than a boolean: "when" answers "whether" as well, and a
-- timestamp cannot drift out of sync with itself.
--
-- Apply with:
--   psql -h 127.0.0.1 -p 5432 -U postgres -d mnemosyne -f <this file>
-- =============================================================================

ALTER TABLE socratic_sessions ADD COLUMN ended_at TIMESTAMPTZ;
