# Chiron frontend

Giao diện thống nhất cho hệ sinh thái Chiron (Mnemosyne, Knowledge Store, Horae).
Vite + React + TypeScript. Thiết kế: Claude Design project
`4709e4d2-d436-43d1-acc6-94d9de853713` (`Chiron.dc.html`) — design system
Nocturne (`src/styles/nocturne.css`, chép nguyên) + bảng màu Nord
(`src/styles/theme.css`).

## Chạy

```bash
cp .env.example .env   # điền CHIRON_KS_TOKEN, CHIRON_ONE_* — xem comment trong file
npm install
npm run dev            # http://localhost:5173  (cổng cố định — CORS của Mnemosyne cần đúng cổng này)
npm run build && npm run preview   # bản build, http://localhost:4173
```

Phải chạy qua `dev` hoặc `preview`: KS và Lịch học đi qua proxy trong Vite
server (`server/chironProxy.ts`), mở thẳng `dist/index.html` thì không có proxy.

## Kết nối

| Module | Cách gọi | Ghi chú |
|---|---|---|
| Mnemosyne `:8081` | Browser gọi thẳng | Không có secret. CORS chỉ cho `localhost`/`127.0.0.1` cổng 5173 và 4173 |
| Knowledge Store | Proxy `/api/ks/*` | Proxy gắn `Bearer CHIRON_KS_TOKEN`. Chỉ GET `/health`, `/nodes`, `/nodes/{id}` |
| Google Calendar | Proxy `/api/gcal/*` → withone.ai | Proxy gắn secret One. Chỉ calendarList + events.list — không có đường ghi |
| Horae | Không gọi | Không có HTTP API. Lịch học đọc block `[Auto]` Horae ghi lên Calendar |

Đường dẫn passthrough của withone.ai là **tương đối so với `baseUrl` của
action** (`https://www.googleapis.com/calendar/v3`): `/calendars/{id}/events`,
không phải `/calendar/v3/calendars/...` — đường dẫn đầy đủ trả 404.

Secret không bao giờ tới browser: `vite.config.ts` chỉ đưa ra browser một
allowlist cấu hình public (`src/config.ts`).

## Trạng thái từng khu vực

| Khu vực | Nguồn | Trạng thái |
|---|---|---|
| Chat · Học bài | Mnemosyne `/socratic/*` | Hoạt động |
| Chat · Hỏi bài | Mnemosyne `/chat/*` (`mode: ask`) | Hoạt động |
| Chat · Giải bài | Mnemosyne `/chat/*` (`mode: solve`) | Hoạt động |
| Thẻ ghi nhớ | Mnemosyne `GET /due`, `POST /review` | Hoạt động |
| Quiz | Mnemosyne `/quiz/*` | Hoạt động |
| Kiến thức | KS `GET /nodes`, `/nodes/{id}` | Hoạt động |
| Điểm yếu | Mnemosyne `GET /weak_cards` | Hoạt động |
| Lịch học | Google Calendar qua withone.ai | Hoạt động (chỉ đọc) |
| Số liệu học tập | Mnemosyne `GET /stats` | Hoạt động (hiện ở Thẻ ghi nhớ và Điểm yếu) |
| Cài đặt | localStorage + `GET /me` | Đăng nhập bằng token, màu nhấn, thu gọn thanh bên, nền sao, trạng thái kết nối |

Mỗi khu vực xử lý lỗi riêng: module nào tắt thì chỉ khu vực đó báo
"không kết nối được", phần còn lại vẫn chạy.

## Những phần lệch khỏi bản thiết kế (có chủ đích)

- Nút chọn model "Chiron 2 · Cân bằng" → chip tĩnh "Socratic · Mnemosyne":
  Mnemosyne không có lựa chọn model, dropdown sẽ là nút bấm không làm gì.
- Tag "Horae đã đồng bộ" → trạng thái kết nối Mnemosyne thật: frontend không
  biết Horae đã đồng bộ hay chưa.
- Bỏ nút đính kèm / ảnh / micro: chưa backend nào nhận.
- Màn Điểm yếu (1c) chỉ giữ khung + thông báo sắp có, không có số liệu mẫu.
- Các màn Thẻ ghi nhớ, Quiz, Kiến thức, Lịch học, Cài đặt không có trong bản
  thiết kế; chúng dùng lại bố cục của màn 1c (header, tiêu đề + mô tả, hàng
  số liệu, đường kẻ mờ, danh sách thẻ `.wk`).
- Mode mặc định là Học bài (bản 1a để "Giải bài"): hai mode kia chưa dùng được.
- "Gần đây" lấy từ `GET /socratic` và `GET /chat`, không phải từ localStorage,
  nên đổi trình duyệt vẫn thấy đủ và trạng thái "đã kết thúc" luôn đúng.
- Mnemosyne cần token: token nằm trong localStorage của trình duyệt (dán ở màn
  Cài đặt), không đặt trong `.env`.
