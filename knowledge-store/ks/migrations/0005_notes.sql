-- Scanned notes (image / PDF → OCR) waiting for the learner to correct them before concepts are extracted.
--
-- Flow: note (OCR done, learner corrects the text) → presses "extract concepts" → the text is
-- stored as a kind='note' transcript → extract_concepts like any transcript →
-- extracted_concepts 'pending_review' → accept/discard. In other words a note has NO
-- route of its own into ks.nodes: it goes through the same confirmation gate as
-- study-session transcripts, so both OCR and LLM errors pass the learner's eyes first.

-- Concepts extracted from scanned notes. Separate from 'mnemosyne' so the source stays known —
-- card_sync and the stats both read source_module.
ALTER TYPE ks.source_module ADD VALUE IF NOT EXISTS 'note_scan';

CREATE TYPE ks.note_status AS ENUM ('draft', 'extracted');

CREATE TABLE ks.notes (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title          TEXT NOT NULL,
  -- Names of the uploaded files, in order. The original images are NOT stored: KS keeps knowledge,
  -- not an archive of the learner's notebook photos.
  filenames      TEXT[] NOT NULL DEFAULT '{}',
  -- Raw OCR result per page (text, lines + confidence). Kept as-is
  -- to compare with the corrected version and measure how much OCR got wrong.
  ocr_pages      JSONB NOT NULL,
  -- The learner's corrected text. Starts as the OCR text; this is what gets extracted.
  text           TEXT NOT NULL,
  status         ks.note_status NOT NULL DEFAULT 'draft',
  -- Filled in once concepts are extracted. Re-extracting reuses this same transcript.
  transcript_id  UUID NULL REFERENCES ks.transcripts(id),
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON ks.notes (created_at DESC);
