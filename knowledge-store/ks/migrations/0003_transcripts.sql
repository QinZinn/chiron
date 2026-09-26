-- 0003_transcripts.sql — store the raw transcript FIRST; extraction is a separate, retryable job.

CREATE TYPE ks.transcript_status         AS ENUM ('pending', 'done', 'failed');
CREATE TYPE ks.extracted_concept_status  AS ENUM ('pending_review', 'accepted', 'discarded');

-- session_ref UNIQUE is the basis of real idempotency: Mnemosyne can retry the same
-- session_ref any number of times, completely safely.
CREATE TABLE ks.transcripts (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_ref TEXT NOT NULL UNIQUE,
  content     JSONB NOT NULL,
  status      ks.transcript_status NOT NULL DEFAULT 'pending',
  attempts    INTEGER NOT NULL DEFAULT 0,
  last_error  TEXT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON ks.transcripts (status);

-- Extraction results AWAITING CONFIRMATION. 'discarded' keeps the row forever, no delete —
-- same logic as a 'rejected' edge.
CREATE TABLE ks.extracted_concepts (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  transcript_id UUID NOT NULL REFERENCES ks.transcripts(id),
  title         TEXT NOT NULL,
  subject       TEXT NOT NULL,
  summary       TEXT NOT NULL,
  source_module ks.source_module NOT NULL,
  status        ks.extracted_concept_status NOT NULL DEFAULT 'pending_review',
  node_id       UUID NULL REFERENCES ks.nodes(id),   -- filled in when accepted
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON ks.extracted_concepts (status);
CREATE INDEX ON ks.extracted_concepts (transcript_id);
