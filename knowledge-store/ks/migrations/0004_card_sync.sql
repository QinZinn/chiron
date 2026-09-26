-- 0004_card_sync.sql — tracks pushing nodes to Mnemosyne as cards.
--
-- KEEPING STATE IS REQUIRED, not excess caution: without this table a node with a
-- transient failure (network, service down) cannot be told apart from "never
-- sent", which loses data silently.

CREATE TYPE ks.card_sync_status AS ENUM ('pending', 'sent', 'failed', 'skipped');

CREATE TABLE ks.card_sync_log (
  node_id      UUID PRIMARY KEY REFERENCES ks.nodes(id),
  status       ks.card_sync_status NOT NULL DEFAULT 'pending',
  attempts     INTEGER NOT NULL DEFAULT 0,
  attempted_at TIMESTAMPTZ,
  last_error   TEXT
);

CREATE INDEX ON ks.card_sync_log (status);

-- `attempts` is NOT in the schema Agent A proposed. Added because the brief asks for
-- "LIMITED retries" for provider_error — without counting attempts there is no way
-- to limit them across timer runs. ks.transcripts already uses
-- exactly this pattern for extraction.
COMMENT ON COLUMN ks.card_sync_log.attempts IS
  'Times POST /cards/from_node was called for this node. Used to limit provider_error retries.';

-- last_error stores reason + message VERBATIM, NOT shortened: the
-- reason="truncated" branch has never been verified against the real API (Mnemosyne
-- confirms its fake provider sits behind the trait boundary, so real truncation
-- cannot be forced). The first time the job meets truncated in the wild, this log row
-- is the only evidence for checking the behaviour matches the design.
COMMENT ON COLUMN ks.card_sync_log.last_error IS
  'Reason + message from Mnemosyne, verbatim. NOT shortened.';
