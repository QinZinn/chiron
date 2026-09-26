-- 0002_edges_instrumentation.sql — measuring duplicate detection and edge review.
-- The log is written INSIDE the business function itself, in the same transaction. Not a side
-- table the caller has to remember.

CREATE TYPE ks.ingest_decision   AS ENUM ('created', 'merged');
CREATE TYPE ks.suggestion_outcome AS ENUM ('ok', 'no_candidates', 'llm_error', 'parse_error');
CREATE TYPE ks.edge_decision     AS ENUM ('approved', 'rejected', 'edited', 'manual_add');

-- Every draft that goes through ingest_concepts leaves exactly one row here, even when
-- it creates a new node. Logging only merges is one-sided and cannot measure false negatives.
CREATE TABLE ks.ingest_log (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  node_id       UUID NOT NULL REFERENCES ks.nodes(id),
  draft_title   TEXT NOT NULL,
  draft_subject TEXT NOT NULL,
  source_module ks.source_module NOT NULL,
  decision      ks.ingest_decision NOT NULL,
  threshold     REAL NOT NULL,
  top_score     REAL NULL,            -- NULL = no candidate above the floor
  candidates    JSONB NOT NULL,       -- [{"node_id":..., "title":..., "score":...}]
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON ks.ingest_log (created_at);

-- The candidate set of each suggest run: measures what full-text surfaces.
CREATE TABLE ks.edge_suggestion_run (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  node_id            UUID NOT NULL REFERENCES ks.nodes(id),
  candidate_node_ids UUID[] NOT NULL,
  suggested_count    INTEGER NOT NULL DEFAULT 0,
  outcome            ks.suggestion_outcome NOT NULL,
  provider           TEXT NULL,
  model              TEXT NULL,
  error              TEXT NULL,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON ks.edge_suggestion_run (node_id);

-- The user's decision on each edge.
-- was_in_candidate_set is THE MOST IMPORTANT METRIC: edges the user adds by hand
-- that full-text could NOT suggest. It is the only evidence for a future pgvector decision.
CREATE TABLE ks.edge_decision_log (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  edge_id                UUID NULL REFERENCES ks.edges(id),
  from_node_id           UUID NOT NULL REFERENCES ks.nodes(id),
  to_node_id             UUID NOT NULL REFERENCES ks.nodes(id),
  relation_type          ks.relation_type NOT NULL,
  previous_relation_type ks.relation_type NULL,   -- only for decision='edited'
  decision               ks.edge_decision NOT NULL,
  suggested_by           ks.suggested_by NOT NULL,
  was_in_candidate_set   BOOLEAN NOT NULL,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON ks.edge_decision_log (decision);
CREATE INDEX ON ks.edge_decision_log (was_in_candidate_set);
