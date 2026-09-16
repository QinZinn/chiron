# Knowledge Store

"Second brain" lưu khái niệm học sinh đã học, thuộc hệ sinh thái Chiron.
Python + Postgres. Consumer duy nhất hiện tại là **Mnemosyne** (Socratic Coach,
Rust/Actix) — runtime khác nên giao tiếp bắt buộc qua HTTP.

- Horae **KHÔNG** ghi vào đây. LexiFlash hoãn có chủ đích.
- Node = một **khái niệm** ("Định luật Newton 2"), không phải một phiên học.
- Đọc [NOTES.md](NOTES.md) trước khi đổi bất cứ quyết định thiết kế nào.

## Cài đặt

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp .env.example .env    # rồi điền giá trị thật
```

`KS_HTTP_TOKEN` **phải là ASCII** — xem NOTES.md.

## Chạy

`fish` không có `export`. Truyền biến bằng `env VAR=value command` — cách này
hoạt động với mọi shell và mô phỏng đúng cách systemd gọi lệnh.

```bash
env KS_DATABASE_URL=postgresql://postgres@127.0.0.1:5432/chiron_ks .venv/bin/python -m ks.cli migrate
```

## CLI

| Lệnh | Việc |
|---|---|
| `migrate` | Chạy migration chưa áp dụng |
| `create-node` | Tạo khái niệm (qua dò trùng) |
| `suggest-edges --node-id` | Top-K ứng viên → LLM → cạnh `pending` |
| `list-pending` / `approve` / `reject` / `edit` | Duyệt cạnh |
| `add-edge` | Tự thêm cạnh (vào thẳng `approved`) |
| `neighbors --node-id` | Node kề qua cạnh đã approved |
| `stats` | Số liệu instrumentation |
| `save-transcript --session-ref --file` | Lưu transcript raw (không chạm LLM) |
| `extract` | Rút khái niệm từ transcript chờ xử lý |
| `list-extracted` / `accept` / `discard` | Xác nhận khái niệm đã rút |
| `card-sync` | Đẩy node đã duyệt sang Mnemosyne thành card |
| `serve` | Chạy HTTP server (block vô hạn) |

## HTTP API

Auth: `Authorization: Bearer <KS_HTTP_TOKEN>`. `/health` không cần auth.

| Route | Ghi chú |
|---|---|
| `GET /health` | Liveness thuần, không chạm DB |
| `POST /transcripts` | `{session_ref, content}`. Idempotent thật theo `session_ref`. Không trigger extraction. `ok:false` + HTTP 200 = KS sống, DB chết |
| `POST /ingest` | `{drafts:[...]}`. All-or-nothing. `psycopg.Error` → 503. Idempotency là **fuzzy match theo similarity**, không phải khoá định danh — retry an toàn chỉ khi giữ nguyên văn `title` |
| `GET /nodes` | `?subject=&source_module=&limit=` (mặc định 50, **trần 500**, vượt trần → 400 chứ không âm thầm cắt). Không trả edges |
| `GET /nodes/{id}` | Một node. `400` id không phải UUID, `404` không có. Node đã merge → trả **node đích với 200** (một bước), nên `id` trả về có thể khác id đã hỏi |

## card_sync

Đẩy node đã duyệt sang Mnemosyne thành flashcard. Xử lý lỗi theo field `reason`
của response `502`, **không retry mù** — `truncated` chết ngay không retry,
`provider_error` retry có giới hạn, `knowledge_store_error` retry thoải mái.

Study set là **UUID**, không phải tên. Tạo set một lần rồi điền id vào `.env`:

```bash
curl -X POST http://127.0.0.1:8081/study_sets -H 'Content-Type: application/json' -d '{"user_id":"<uuid>","name":"KS review","topic":"..."}'
```

Mnemosyne không có unique constraint trên tên set, nên đừng để job tự tạo.
Đọc NOTES.md — nhánh `truncated` chưa từng verify trên dữ liệu thật.

## Test

```bash
env KS_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:5432/chiron_ks_test .venv/bin/python -m pytest -q
```

Test chạy trên Postgres **thật** — trigram là hành vi của Postgres, mock nó thì
test mất hết giá trị.

## Vận hành (systemd **user** units)

Đây là lựa chọn có chủ đích, **lệch brief §10** (brief dùng system-level). Mọi
lệnh vận hành trong tài liệu cũ phải thêm `--user` — xem NOTES.md để biết đánh đổi.

Cần chạy **một lần** để service sống qua logout và tự lên lúc boot:

```bash
sudo loginctl enable-linger zinnn
```

File unit ở [deploy/](deploy/). Cài:

```bash
cp deploy/*.service deploy/*.timer ~/.config/systemd/user/ && systemctl --user daemon-reload
```

| Unit | Vai trò |
|---|---|
| `chiron-ks-postgres.service` | Cụm Postgres riêng, port 5432 |
| `chiron-ks-http.service` | `ks serve`, `Type=simple` (block vô hạn, không phải cron) |
| `chiron-ks-extract.timer` | Mỗi 30 phút. **Cần `DEEPSEEK_API_KEY`** |
| `chiron-ks-stats.timer` | 23:00 hằng ngày |
| `chiron-ks-card-sync.timer` | Hằng giờ. Cần Mnemosyne sống ở `KS_MNEMOSYNE_URL` |

Log: `journalctl --user -u chiron-ks-http -f`.

Sửa code xong phải `systemctl --user restart chiron-ks-http` — không restart thì
curl vẫn đang test code cũ.

Trước khi kill bất kỳ tiến trình `ks serve` nào, xác nhận
`systemctl --user show chiron-ks-http.service -p MainPID`. Đừng phán đoán
"orphan" chỉ dựa vào `lsof`/port.
