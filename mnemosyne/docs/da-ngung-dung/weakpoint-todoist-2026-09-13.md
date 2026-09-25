> **ĐÃ NGỪNG DÙNG (2026-09-25).** Tính năng này đã bị gỡ: Chiron không còn gọi
> Todoist và không còn gắn với Horae. Thẻ yếu giờ tạo mục trong todo list nội bộ
> (`todo_items`, migration 0010). File này chỉ giữ lại làm lịch sử quyết định;
> xem `../../NOTES.md` cho thiết kế hiện hành.

# NOTES — Weakpoint Dashboard: thẻ yếu → task `@ontap` trên Todoist, 2026-09-13

Nguồn: spec `claude/spec-weakpoint-dashboard-2026-09-13.md` (được dán vào session, không nằm trong repo). Code: `backend/src/weak_cards.rs`, `backend/src/todoist_client.rs`, bước 6 của `backend/src/handlers/reviews.rs`, migration `backend/sql/migrations/0006_add_weak_card_tasks.sql`.

## Quyết định Zinnn chốt trước khi code

- **§4.1 — một task mở cho mỗi study_set** ("chu kỳ mở"), không phải một task mỗi ngày lịch.
- **Mọi user** đều kích hoạt: thẻ yếu của learner nào cũng tạo task trên tài khoản Todoist của `TODOIST_TOKEN`.
- **Sửa luôn Horae** (adapter Todoist của Horae đang hỏng, xem mục dưới).
- Không có `TODOIST_TOKEN` trên máy dev này. Phía Todoist được kiểm tra qua Todoist connector của Claude (chỉ đọc).

## Chỗ lệch khỏi spec, và vì sao

1. **Dùng các endpoint kiểu REST của API v1, không dùng lệnh `/sync`.** Spec §6.2 ghi `item_add`/`item_update`/`item_close` qua `/api/v1/sync` (form-urlencoded, `temp_id_mapping`), với độ tin cậy "trung bình". Tôi đọc OpenAPI chính thức (`https://developer.todoist.com/openapi.json`, tải trực tiếp chứ không qua bản tóm tắt). API v1 có `POST /api/v1/tasks`, `POST /api/v1/tasks/{id}` và `POST /api/v1/tasks/{id}/close`: body JSON thuần, trả thẳng `id` dạng chuỗi. Cách này đơn giản hơn và không phải tách mapping. Kết quả curl không token ngày 13/09:
   ```
   https://api.todoist.com/rest/v2/tasks -> 410
   https://api.todoist.com/sync/v9/sync  -> 410   ("This endpoint is deprecated.")
   https://api.todoist.com/api/v1/tasks  -> 401   (route tồn tại, chỉ thiếu token)
   ```
2. **Label tên là `@ontap` (có `@`), không phải `ontap`.** Spec §4.4 nói gửi `labels: ["ontap"]`. Đọc tài khoản thật qua connector: label duy nhất liên quan là `{"id":"2184722393","name":"@ontap"}`, và task thật "Ôn IELTS [90m/ngày]" mang `"labels":["@ontap"]`. Gửi `"ontap"` sẽ lặng lẽ tạo thêm một label thứ hai. Horae nhận cả hai cách viết vì `_normalize_labels` bóc `@`, nên đây chỉ là chuyện không làm bẩn tài khoản. Hằng số: `todoist_client::ONTAP_LABEL`.
3. **Todoist lỗi thì rollback phần DB.** Spec §4.3 muốn giữ dòng `weak_card_task_cards` và chấp nhận description lệch "tới lần cập nhật kế tiếp". Nhưng theo chính spec, một thẻ đã liệt kê thì không gọi Todoist nữa. Vậy nếu không có thẻ mới nào vào set, "lần cập nhật kế tiếp" không bao giờ tới và thẻ đó không bao giờ lên Todoist. Rollback thì lần review yếu sau tự thử lại. Test: `weak_cards_a_failed_update_does_not_list_the_card` (đã tạm gài lại lỗi cũ và thấy test FAIL).
4. **Mọi review yếu đều gia hạn chu kỳ, kể cả của thẻ đã liệt kê.** Spec tự mâu thuẫn ở điểm này: §4.2/§4.3 chỉ bump `last_weak_card_at` khi có thẻ yếu *mới*, còn §5.2 nói thẻ "tiếp tục sai (bump `last_weak_card_at`, task không bị đóng)". Tôi theo §5.2: một thẻ vẫn đang bị sai là đúng thứ task này sinh ra để nhắc. Làm theo §4.3 thì task sẽ bị đóng sau 5 ngày trong khi learner vẫn sai thẻ đó mỗi ngày.
5. **Xét thẻ vừa review trước, sweep sau** (spec §4.3 để sweep ở bước 1). Nếu sweep chạy trước, task đã quá hạn nhưng thẻ vẫn đang yếu sẽ bị đóng rồi mở lại ngay thành một task Todoist mới. Test: `weak_cards_a_card_still_failing_keeps_its_task_open` (đảo thứ tự thì test FAIL, đã thử).
6. **Advisory lock theo set** (`pg_advisory_xact_lock`) bao toàn bộ thao tác trên task của một set, gồm cả lời gọi Todoist. Spec không xử lý race cho luồng mới này: hai review yếu cùng lúc trong một set sẽ cùng thấy "chưa có task" và cùng tạo task trên Todoist. Unique index chặn dòng thứ hai trong DB, nhưng task Todoist thứ hai vẫn thành task mồ côi, còn thẻ kia thì không được liệt kê. Test: `weak_cards_concurrent_weak_cards_in_one_set_share_one_task` (hai connection thật, fake Todoist trễ 300ms). Bỏ lock thì test FAIL vì có 2 lần create. Race đã biết ở bước 2/5 của `reviews.rs` KHÔNG bị đụng (§9).
7. **Task bị hoàn thành hoặc xoá bằng tay trên Todoist** (spec không đề cập): response của update có `checked`/`is_deleted`, hoặc update trả 404. Khi đó đóng dòng cũ và mở chu kỳ mới chỉ gồm thẻ vừa yếu. Nếu không làm vậy, thẻ yếu mới sẽ bị đổ vào một task đã tick xong mà Horae không bao giờ đọc. Test: `weak_cards_a_task_completed_in_todoist_is_replaced`.
8. **Task mồ côi**: nếu Todoist đã tạo task mà DB không ghi được, code gọi close ngay. Nếu close cũng lỗi thì log `ORPHAN Todoist task <id> … Close it by hand`.
9. **Câu validate §5.2 được thay.** `avg(interval)` của các review sai đo khoảng FSRS *đặt lịch* sau một lần sai (gần như luôn là 1 ngày, vì sai thì reset), chứ không đo learner *thực sự* quay lại set sau bao lâu. Mà N (số ngày tự đóng) phụ thuộc đúng vào con số thứ hai. Câu thay thế đo khoảng cách giữa các ngày có review trong cùng một set (xem mục dưới).
10. Timeout client Todoist là 5s (KS dùng 10s), vì lời gọi nằm ngay trong `POST /review`.

## Validate ngưỡng (§2.3, §5.2) — CHƯA có số liệu thật

Máy dev này không có DB thật của Mnemosyne. Postgres 18 mới được cài, chưa init. Để chạy test, tôi dựng một cluster tạm trong scratchpad (port 5433, trust auth), nạp `schema.sql` và áp `0006`. Cả hai câu truy vấn chạy được, nhưng DB chỉ có đúng một thẻ do chính tôi tạo để verify:

```
== §2.3 (câu của spec, giữ nguyên)
 error_rate | n_cards
------------+---------
        0.4 |       1
== §5.2 thay thế
 gap_days | count
----------+-------
(0 rows)
```

Theo đúng quy tắc của spec ("quá ít dữ liệu → giữ nguyên mặc định"), tôi **giữ X=5, ngưỡng 40%, N=5 ngày**. Ba hằng số `WEAK_CARD_WINDOW`, `WEAK_CARD_ERROR_THRESHOLD`, `WEAK_TASK_AUTO_CLOSE_DAYS` nằm ở đầu `weak_cards.rs`. Cần chạy hai câu sau trên DB thật (cổng 5432):

```sql
-- §2.3: phân bố tỷ lệ sai trên cửa sổ 5 review gần nhất
WITH windows AS (
  SELECT card_id, user_id, is_correct,
         ROW_NUMBER() OVER (PARTITION BY card_id, user_id ORDER BY created_at DESC) AS rn
  FROM learning_events),
last5 AS (
  SELECT card_id, user_id, COUNT(*) AS n, COUNT(*) FILTER (WHERE NOT is_correct) AS wrong
  FROM windows WHERE rn <= 5 GROUP BY card_id, user_id HAVING COUNT(*) = 5)
SELECT (wrong::float / n) AS error_rate, COUNT(*) AS n_cards FROM last5 GROUP BY 1 ORDER BY 1;

-- §5.2 (thay thế): learner quay lại một set sau bao nhiêu ngày.
-- Nếu nhiều khoảng > 5 ngày mà learner vẫn đang học set đó, N=5 đang đóng task quá sớm.
WITH days AS (
  SELECT DISTINCT c.set_id, le.user_id, (le.created_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AS d
  FROM learning_events le JOIN cards c ON c.id = le.card_id),
gaps AS (
  SELECT d - lag(d) OVER (PARTITION BY set_id, user_id ORDER BY d) AS gap_days FROM days)
SELECT gap_days, count(*) FROM gaps WHERE gap_days IS NOT NULL GROUP BY 1 ORDER BY 1;
```

## Kiểm thử — kết quả thật

- `cargo test -p backend` (offline): **121 passed, 0 failed, 22 ignored**, không có warning.
- `cargo test -p backend -- --ignored weak_cards_ reviews_ due_` trên cluster tạm: **17 passed**, gồm 7 test DB có sẵn và 10 test mới (9 trong `weak_cards`, 1 test đầu-cuối cho handler `reviews_a_todoist_outage_does_not_break_the_review`). Sau khi chạy xong, DB còn 0 user: hai test phải commit dữ liệu đều tự xoá learner của mình, và mọi dòng liên quan xoá theo cascade.
- Đã kiểm chứng test không vô nghĩa bằng cách tạm gài 3 lỗi, mỗi lỗi làm FAIL đúng test tương ứng. Sau đó khôi phục (`diff` sạch):
  - giữ dòng listing khi Todoist update lỗi → `weak_cards_a_failed_update_does_not_list_the_card` FAIL
  - chạy sweep trước khi xét thẻ → `weak_cards_a_card_still_failing_keeps_its_task_open` FAIL
  - bỏ advisory lock → `weak_cards_concurrent_weak_cards_in_one_set_share_one_task` FAIL
- **Đưa đúng hình dạng task vào parser thật của Horae** (`parse_tasks` với `PRESET_STUDENT_VN`):
  ```
  TaskClassification(task_id='m1', title='Ôn thẻ yếu — Từ vựng Unit 5', kind='ongoing', estimate_minutes=40, source='title')
  TaskClassification(task_id='m2', title='Ôn thẻ yếu — test set', kind='ongoing', estimate_minutes=20, source='title')
  ```
  Ca đối chứng (ghi "@ontap" vào title, không set `labels`) bị Horae bỏ hẳn, với cảnh báo "không có due date → không tạo Assignment". Đúng lỗi mà spec §4.4 cảnh báo, và thực tế còn tệ hơn spec đoán: task không rơi vào nhánh assignment mà mất luôn.
- **Server thật + Todoist thật, token cố tình sai** (`TODOIST_TOKEN=deliberately-invalid-token`). Tạo user, set "Từ vựng Unit 5" và thẻ "ubiquitous là gì?" qua API, rồi review lần lượt `again good again good good`:
  ```
  HTTP 201 in 0.012857s <- again
  HTTP 201 in 0.004350s <- good
  HTTP 201 in 0.002448s <- again
  HTTP 201 in 0.002211s <- good
  HTTP 201 in 0.729906s <- good
  [weak-cards] card eee9970b-…: nothing recorded, the review itself is stored: PERMANENT — Todoist rejected the token; check TODOIST_TOKEN in .env. This will fail on every review until it is fixed: Todoist HTTP 401: {"error":"Unauthorized","error_code":477,…,"http_code":401}
  ```
  Kết quả: 5 dòng `learning_events`, 0 dòng `weak_card_tasks`. Review thứ 5 (lần đầu thẻ thành yếu) thực sự gọi `POST /api/v1/tasks` và nhận 401 thật, được log đúng nhánh PERMANENT. Response vẫn 201.

## Việc còn lại — cần token thật / DB thật, chưa làm được ở đây

1. Áp `backend/sql/migrations/0006_add_weak_card_tasks.sql` lên DB thật (không tự chạy, đúng quy ước).
2. Thêm `TODOIST_TOKEN=` vào `.env` của Mnemosyne (cùng token với Horae).
3. `cargo test -p backend -- --ignored --nocapture live_todoist_round_trip`: tạo, sửa rồi đóng một task thật. Đây là lần xác nhận wire format bằng request có token (hiện mới khớp OpenAPI và qua được đường 401 thật). Test để lại một task đã hoàn thành trong lịch sử Todoist.
4. Chạy hai câu SQL ở trên trên dữ liệu thật; chỉnh hằng số nếu cần, ghi lý do vào đây.
5. §8 bước 1–4 với token thật. Trước đó Horae cũng phải chạy được live với adapter mới (xem `Horae/NOTES.md`): adapter đã sửa nhưng chưa từng gọi Todoist thật bằng token.
