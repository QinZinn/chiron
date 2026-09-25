# NOTES — Todo list nội bộ thay cho Todoist, 2026-09-25

Code: `backend/src/weak_cards.rs`, `backend/src/handlers/todos.rs`, bước 6 của
`backend/src/handlers/reviews.rs`, migration
`backend/sql/migrations/0010_replace_weak_card_tasks_with_todos.sql`.
Thiết kế cũ (task `@ontap` trên Todoist cho Horae xếp lịch):
`docs/da-ngung-dung/weakpoint-todoist-2026-09-13.md`.

## Giữ nguyên

Luật "thẻ yếu": ít nhất 40 % trong 5 lượt ôn gần nhất là "again", và thẻ có
dưới 5 lượt thì chưa bị xét (`WEAK_CARD_WINDOW`, `WEAK_CARD_ERROR_THRESHOLD`,
`assess`). Câu truy vấn 5 lượt gần nhất cũng giữ nguyên. Chỉ **đích ghi** đổi:
bảng `todo_items` thay cho lời gọi Todoist.

## Chỗ lệch khỏi brief, và vì sao

1. **Thêm bảng `todo_item_cards`.** Brief chỉ đưa `todo_items`. Nhưng màn
   Điểm yếu (`GET /weak_cards`) được dựng từ danh sách *thẻ nào* làm set bị yếu,
   mà danh sách đó trước đây nằm ở `weak_card_task_cards`. Xoá bảng đó mà không
   có bảng thay thì màn Điểm yếu mất nguồn dữ liệu. Migration 0010 chép cả hai
   bảng cũ sang rồi mới `DROP`.
2. **Thêm cột `last_weak_card_at`.** Mỗi lần review yếu trong set đều cập nhật
   cột này, để danh sách xếp set đang yếu gần nhất lên trước và màn Điểm yếu
   ghi được "thẻ yếu gần nhất … trước". Có CHECK bắt buộc cột này cho mục
   `weak_card`.
3. **Phần 1 và phần 2 dùng chung migration.** Brief muốn gỡ Todoist (phần 1)
   xong và kiểm tra rồi mới làm todo (phần 2). Việc thẻ yếu *ghi vào đâu* thì
   không tách được: nếu migration phần 1 `DROP` bảng cũ trước khi có bảng mới,
   phát hiện thẻ yếu sẽ không còn chỗ ghi. Vì vậy phần 1 đã tạo luôn bảng todo và
   được kiểm tra trước (review thật → mục todo, không có lời gọi Todoist). Các
   endpoint `/todos` và giao diện làm sau đó.
4. **`GET /todos` không có tham số `user_id`.** Brief ghi `GET /todos?user_id=`.
   Mọi route của người học đều lấy `user_id` từ bearer token; nhận nó qua query
   sẽ cho phép một token đọc todo của người khác. Test
   `todos_are_scoped_to_the_token` khoá lại điều này.
5. **Không cần advisory lock.** Bản Todoist phải khoá theo set vì lời gọi mạng
   nằm giữa lúc "chưa có task" và lúc ghi DB. Giờ chỉ còn một câu
   `INSERT … ON CONFLICT (study_set_id) WHERE done = false AND source = 'weak_card'`
   trên chính partial unique index `todo_items_open_weak_per_set`, nên Postgres
   tự tuần tự hoá hai review đến cùng lúc. Test
   `weak_cards_concurrent_weak_cards_in_one_set_share_one_item` (hai connection
   thật, cùng lúc) cho ra 1 mục, 2 thẻ.
6. **Bỏ tự đóng sau 5 ngày.** Đúng theo brief: người học tự tick. Mục đã tick
   xong thì không mở lại; lần review yếu tiếp theo trong set mở một mục mới
   (`weak_cards_a_ticked_off_item_is_replaced_by_the_next_weak_review`).
7. **Tiêu đề mục thẻ yếu không chứa tên set** ("Ôn lại các thẻ đang yếu"). Tên
   set được join lúc đọc, nên đổi tên set thì mục đổi theo.

## Test

- Bỏ 9 test DB chỉ kiểm hành vi Todoist: fake client, update, sweep đóng task
  im lặng, task mồ côi, task bị hoàn thành trên Todoist, lỗi Todoist rollback.
  Bỏ luôn test e2e `reviews_a_todoist_outage_does_not_break_the_review`, vì
  không còn lời gọi nào để hỏng.
- Thêm 5 test DB trong `weak_cards`, 3 trong `handlers::todos`, và 1 test e2e
  `reviews_a_weak_card_files_one_todo_item`. Test e2e này chạy 6 lượt review
  qua handler thật và kiểm: đúng 1 mục, đúng thẻ, response khớp DB.
- Kết quả 2026-09-25 trên Postgres của Compose: `cargo test --workspace` được
  137 + 10 passed; `-- --ignored --skip live_` được 27 passed.

## Ngưỡng — vẫn chưa có số liệu thật

Hai câu truy vấn kiểm ngưỡng ở `docs/da-ngung-dung/weakpoint-todoist-2026-09-13.md`
(mục "Validate ngưỡng") vẫn dùng được cho câu hỏi 40 %/5 lượt. DB hiện chưa có
dữ liệu học thật nên chưa chạy.
