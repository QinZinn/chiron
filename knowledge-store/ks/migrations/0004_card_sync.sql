-- 0004_card_sync.sql — theo dõi việc đẩy node sang Mnemosyne thành card.
--
-- GIỮ STATE LÀ BẮT BUỘC, không phải phòng thủ thừa: không có bảng này thì một
-- node lỗi tạm thời (mạng, service chết) không phân biệt được với "node chưa
-- từng gửi", gây mất dữ liệu im lặng.

CREATE TYPE ks.card_sync_status AS ENUM ('pending', 'sent', 'failed', 'skipped');

CREATE TABLE ks.card_sync_log (
  node_id      UUID PRIMARY KEY REFERENCES ks.nodes(id),
  status       ks.card_sync_status NOT NULL DEFAULT 'pending',
  attempts     INTEGER NOT NULL DEFAULT 0,
  attempted_at TIMESTAMPTZ,
  last_error   TEXT
);

CREATE INDEX ON ks.card_sync_log (status);

-- `attempts` KHÔNG có trong schema Agent A đưa ra. Thêm vì brief yêu cầu
-- "retry CÓ GIỚI HẠN" cho provider_error — không đếm được số lần đã thử thì
-- không giới hạn được qua nhiều lần timer chạy. Bảng ks.transcripts đã dùng
-- đúng mẫu này cho extraction.
COMMENT ON COLUMN ks.card_sync_log.attempts IS
  'Số lần đã gọi POST /cards/from_node cho node này. Dùng để giới hạn retry provider_error.';

-- last_error lưu NGUYÊN VĂN reason + message, KHÔNG rút gọn: nhánh
-- reason="truncated" chưa từng được verify qua API thật (Mnemosyne tự xác nhận
-- fake provider của họ phủ qua trait boundary nên không ép được truncation
-- thật). Lần đầu job gặp truncated ngoài đời, dòng log này là bằng chứng duy
-- nhất để kiểm hành vi có đúng thiết kế không.
COMMENT ON COLUMN ks.card_sync_log.last_error IS
  'Nguyên văn reason + message từ Mnemosyne. KHÔNG rút gọn.';
