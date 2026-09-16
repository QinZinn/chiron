-- 0003_transcripts.sql — lưu transcript raw TRƯỚC, extraction là job riêng retry được.

CREATE TYPE ks.transcript_status         AS ENUM ('pending', 'done', 'failed');
CREATE TYPE ks.extracted_concept_status  AS ENUM ('pending_review', 'accepted', 'discarded');

-- session_ref UNIQUE là nền của idempotency thật: Mnemosyne retry cùng
-- session_ref bao nhiêu lần cũng an toàn tuyệt đối.
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

-- Kết quả extraction CHỜ XÁC NHẬN. 'discarded' giữ row vĩnh viễn, không xoá —
-- cùng logic với edge 'rejected'.
CREATE TABLE ks.extracted_concepts (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  transcript_id UUID NOT NULL REFERENCES ks.transcripts(id),
  title         TEXT NOT NULL,
  subject       TEXT NOT NULL,
  summary       TEXT NOT NULL,
  source_module ks.source_module NOT NULL,
  status        ks.extracted_concept_status NOT NULL DEFAULT 'pending_review',
  node_id       UUID NULL REFERENCES ks.nodes(id),   -- điền khi accepted
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON ks.extracted_concepts (status);
CREATE INDEX ON ks.extracted_concepts (transcript_id);
