# Chiron

Hệ sinh thái AI học tập. Mỗi module chạy độc lập và giao tiếp qua HTTP; repo này
gộp chúng lại một chỗ để phát triển chung.

| Thư mục | Module | Stack | Địa chỉ |
|---|---|---|---|
| [`mnemosyne/`](mnemosyne/) | Học bài (Socratic, Feynman), thẻ ghi nhớ FSRS, quiz | Rust + Actix + Postgres | `127.0.0.1:8081` |
| [`knowledge-store/`](knowledge-store/) | "Second brain" lưu khái niệm đã học | Python + Flask + Postgres | `127.0.0.1:8080` |
| [`frontend/`](frontend/) | Giao diện web thống nhất | Vite + React + TypeScript | `localhost:5173` |
| [`ocr/`](ocr/) | Nhận dạng chữ cho "Scan ghi chép" | Python + PaddleOCR (CPU) | nội bộ `ocr:8866` |

Chiron không kết nối dịch vụ bên ngoài nào ngoài nhà cung cấp LLM. Việc cần ôn
(thẻ yếu, việc tự thêm) nằm trong todo list nội bộ của Mnemosyne; Chiron không tự
xếp lịch.

## Chạy bằng Docker (khuyến nghị)

```bash
cp .env.example .env                  # đặt POSTGRES_PASSWORD (openssl rand -hex 24)
docker compose up --build -d
docker compose ps                     # đợi cả năm service healthy
docker compose exec mnemosyne backend create-user <email>
```

Mở http://localhost:4173 và dán token vừa in ra vào màn Cài đặt.

- Secret của từng module vẫn ở `.env` của module đó (`mnemosyne/.env`,
  `knowledge-store/.env`, `frontend/.env`), vào container qua `env_file` lúc
  chạy, không nằm trong image.
- Database được dựng tự động lần đầu: Postgres tạo các database, rồi mỗi module
  tự chạy migration khi khởi động.
- Chỉ mở ra `127.0.0.1`: Mnemosyne `8081` (browser gọi thẳng) và frontend
  `4173`. Knowledge Store, OCR và Postgres chỉ nằm trong network nội bộ.
- Image `ocr` (~2,5 GB, PaddlePaddle + mô hình) được **pull** từ
  `ghcr.io/qinzinn/chiron-ocr`, không build tại chỗ — xem `ocr/README.md`.
- Dữ liệu nằm trong volume `chiron_pgdata`; `docker compose down` giữ nguyên
  nó, `docker compose down -v` thì xoá.

Cần psql vào database: `docker compose exec postgres psql -U postgres -d mnemosyne`.

## Postgres (chạy ngoài Docker)

Một instance duy nhất ở cổng `5432`, hai database riêng: `mnemosyne` và
`chiron_ks`. Dùng chung server để khỏi vận hành hai cụm, **không** dùng chung dữ
liệu — schema của hai module không được trộn vào nhau.

```bash
pg_ctl start -D ~/.local/share/chiron-ks-postgres -o "-p 5432" \
  -l ~/.local/share/chiron-ks-postgres/logfile -w
```

## Chạy

Mỗi module có `.env` riêng, chép từ `.env.example` của nó. Đọc README trong từng
thư mục trước.

```bash
# Knowledge Store
cd knowledge-store && .venv/bin/python -m ks.cli migrate && .venv/bin/python -m ks.cli serve

# Mnemosyne
cd mnemosyne && cargo run -p backend -- migrate && cargo run -p backend

# Frontend
cd frontend && npm install && npm run dev
```

Mnemosyne cần một token để dùng: cấp bằng
`cd mnemosyne && cargo run -p backend -- create-user <email>`, rồi dán token đó
vào màn Cài đặt của frontend. Token chỉ hiện đúng một lần.

Chạy như dịch vụ: `mnemosyne/deploy/` và `knowledge-store/deploy/` có sẵn unit
systemd mức user.

## Quy ước phát triển

Tính năng làm trên branch riêng, `main` chỉ nhận phần đã kiểm tra xong.

## Đã ngừng dùng

Ghi lại để tra cứu; không còn đoạn code hay biến môi trường nào dùng tới.

- **Horae** (xếp lịch tự học) tách khỏi repo ngày 2026-09-20, và từ 2026-09-25
  Chiron không còn gắn với nó dưới bất kỳ hình thức nào.
- **Todoist** — thẻ yếu từng tạo task `@ontap` để Horae xếp lịch. Gỡ ngày
  2026-09-25 (migration `0010` của Mnemosyne thay bằng `todo_items`). Nhật ký
  thiết kế cũ: `mnemosyne/docs/da-ngung-dung/`.
- **Google Calendar qua withone.ai** — màn "Lịch học" từng đọc calendar của
  Horae. Gỡ cả màn ngày 2026-09-25.

## Giấy phép

MIT — xem [LICENSE](LICENSE).
