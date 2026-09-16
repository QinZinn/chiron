-- 0001_init.sql — schema gốc: nodes + edges.
CREATE SCHEMA ks;

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TYPE ks.source_module   AS ENUM ('mnemosyne', 'lexiflash');
CREATE TYPE ks.relation_type   AS ENUM ('prerequisite', 'related', 'contrasts_with');
CREATE TYPE ks.suggested_by    AS ENUM ('llm', 'manual');
CREATE TYPE ks.edge_status     AS ENUM ('pending', 'approved', 'rejected');

CREATE TABLE ks.nodes (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title          TEXT NOT NULL,
  subject        TEXT NOT NULL,          -- TEXT tự do, KHÔNG enum
  summary        TEXT NOT NULL,
  source_module  ks.source_module NOT NULL,
  merged_into_id UUID NULL REFERENCES ks.nodes(id),
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON ks.nodes USING GIN (title gin_trgm_ops);
CREATE INDEX ON ks.nodes USING GIN (summary gin_trgm_ops);

CREATE TABLE ks.edges (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  from_node_id  UUID NOT NULL REFERENCES ks.nodes(id),
  to_node_id    UUID NOT NULL REFERENCES ks.nodes(id),
  relation_type ks.relation_type NOT NULL,
  suggested_by  ks.suggested_by NOT NULL,
  status        ks.edge_status NOT NULL DEFAULT 'pending',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (from_node_id, to_node_id, relation_type)
);
