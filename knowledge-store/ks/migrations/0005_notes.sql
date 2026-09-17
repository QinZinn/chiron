-- Ghi chép scan (ảnh / PDF → OCR) chờ người học sửa rồi mới rút khái niệm.
--
-- Luồng: note (OCR xong, người học sửa text) → bấm "rút khái niệm" → text được
-- lưu thành một transcript kind='note' → extract_concepts như mọi transcript →
-- extracted_concepts 'pending_review' → accept/discard. Tức là note KHÔNG có
-- đường riêng vào ks.nodes: nó đi qua đúng cổng xác nhận mà transcript phiên
-- học đi qua, nên lỗi OCR lẫn lỗi LLM đều phải qua mắt người học trước.

-- Khái niệm rút từ ghi chép scan. Tách khỏi 'mnemosyne' để còn biết nguồn —
-- card_sync và thống kê đều đọc source_module.
ALTER TYPE ks.source_module ADD VALUE IF NOT EXISTS 'note_scan';

CREATE TYPE ks.note_status AS ENUM ('draft', 'extracted');

CREATE TABLE ks.notes (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title          TEXT NOT NULL,
  -- Tên các tệp đã tải lên, theo thứ tự. Ảnh gốc KHÔNG lưu: KS giữ tri thức,
  -- không giữ kho ảnh vở của người học.
  filenames      TEXT[] NOT NULL DEFAULT '{}',
  -- Kết quả OCR nguyên văn theo trang (text, lines + confidence). Giữ nguyên
  -- để còn so với bản đã sửa và đo xem OCR sai bao nhiêu.
  ocr_pages      JSONB NOT NULL,
  -- Văn bản người học đã sửa. Khởi tạo bằng text OCR; đây mới là thứ được rút.
  text           TEXT NOT NULL,
  status         ks.note_status NOT NULL DEFAULT 'draft',
  -- Điền khi đã rút khái niệm. Rút lại dùng lại chính transcript này.
  transcript_id  UUID NULL REFERENCES ks.transcripts(id),
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ON ks.notes (created_at DESC);
