# Chiron

Hệ sinh thái AI học tập. Mỗi module chạy độc lập và giao tiếp qua HTTP; repo này
gộp chúng lại một chỗ để phát triển chung.

| Thư mục | Module | Stack | Địa chỉ |
|---|---|---|---|
| [`mnemosyne/`](mnemosyne/) | Học bài (Socratic, Feynman), thẻ ghi nhớ FSRS, quiz | Rust + Actix + Postgres | `127.0.0.1:8081` |
| [`knowledge-store/`](knowledge-store/) | "Second brain" lưu khái niệm đã học | Python + Flask + Postgres | `127.0.0.1:8080` |
| [`frontend/`](frontend/) | Giao diện web thống nhất | Vite + React + TypeScript | `localhost:5173` |

Horae (xếp lịch tự học) **không** thuộc repo này nữa — nó đã tách ra thành dự án
riêng. Frontend đọc block `[Auto]` Horae ghi trên Google Calendar, không gọi
Horae trực tiếp.

## Postgres

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

## Giấy phép

MIT — xem [LICENSE](LICENSE).
