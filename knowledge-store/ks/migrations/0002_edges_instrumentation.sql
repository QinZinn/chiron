-- 0002_edges_instrumentation.sql — đo hành vi dò trùng và duyệt edge.
-- Log nằm TRONG chính hàm nghiệp vụ, cùng transaction. Không phải bảng phụ
-- caller tự nhớ gọi.

CREATE TYPE ks.ingest_decision   AS ENUM ('created', 'merged');
CREATE TYPE ks.suggestion_outcome AS ENUM ('ok', 'no_candidates', 'llm_error', 'parse_error');
CREATE TYPE ks.edge_decision     AS ENUM ('approved', 'rejected', 'edited', 'manual_add');

-- Mỗi draft đi qua ingest_concepts để lại đúng một dòng ở đây, kể cả khi
-- tạo mới. Không log ca merge một chiều thì không đo được false negative.
CREATE TABLE ks.ingest_log (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  node_id       UUID NOT NULL REFERENCES ks.nodes(id),
  draft_title   TEXT NOT NULL,
  draft_subject TEXT NOT NULL,
  source_module ks.source_module NOT NULL,
  decision      ks.ingest_decision NOT NULL,
  threshold     REAL NOT NULL,
  top_score     REAL NULL,            -- NULL = không có candidate nào trên sàn
  candidates    JSONB NOT NULL,       -- [{"node_id":..., "title":..., "score":...}]
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON ks.ingest_log (created_at);

-- Candidate set mỗi lần suggest: đo được full-text đưa ra những gì.
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

-- Quyết định của người dùng trên từng edge.
-- was_in_candidate_set là CHỈ SỐ QUAN TRỌNG NHẤT: edge người dùng tự thêm tay
-- mà full-text KHÔNG đề xuất được. Đây là căn cứ duy nhất để sau này quyết pgvector.
CREATE TABLE ks.edge_decision_log (
  id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  edge_id                UUID NULL REFERENCES ks.edges(id),
  from_node_id           UUID NOT NULL REFERENCES ks.nodes(id),
  to_node_id             UUID NOT NULL REFERENCES ks.nodes(id),
  relation_type          ks.relation_type NOT NULL,
  previous_relation_type ks.relation_type NULL,   -- chỉ có với decision='edited'
  decision               ks.edge_decision NOT NULL,
  suggested_by           ks.suggested_by NOT NULL,
  was_in_candidate_set   BOOLEAN NOT NULL,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON ks.edge_decision_log (decision);
CREATE INDEX ON ks.edge_decision_log (was_in_candidate_set);
