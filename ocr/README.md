# Chiron OCR

PaddleOCR + VietOCR (CPU) sau một HTTP API nhỏ, dùng cho màn **Scan ghi chép**: ảnh chụp vở
hoặc PDF → văn bản để người học sửa rồi rút khái niệm vào Knowledge Store.

Service nội bộ: không mở cổng ra host, người gọi duy nhất là Knowledge Store. Nó
không lưu gì — tệp tải lên nằm trong thư mục tạm suốt request rồi bị xoá.

## API

| Route | Việc |
|---|---|
| `GET /health` | `{"status":"ok","ready":bool,"engine":…,"error":…}` — `ready` chỉ `true` khi mô hình đã nạp xong |
| `POST /ocr` | multipart, một hoặc nhiều trường `files` (ảnh hoặc PDF) → `{"pages":[…], "page_count", "elapsed_ms"}` |

Mỗi trang: `text` (các dòng đã sắp theo thứ tự đọc), `lines` (từng dòng kèm
`confidence`), `mean_confidence` (`null` nếu trang không có chữ) và
`low_confidence_count`.

Lỗi: `400 invalid_document` (không phải ảnh/PDF, PDF hỏng/có mật khẩu),
`413 too_many_pages` / `too_large`, `503 not_ready` (mô hình đang nạp),
`500 ocr_failed` (mô hình lỗi khi chạy).

## Mô hình

Hai mô hình, mỗi cái làm một việc:

1. **PaddleOCR `PP-OCRv6_medium_det`** tìm các dòng chữ trên trang, sau khi
   `PP-LCNet_x1_0_doc_ori` xoay lại ảnh chụp bị lệch 90/180/270°.
2. **VietOCR `vgg_transformer`** đọc từng dòng (cắt và nắn thẳng theo tứ giác
   bộ phát hiện trả về).

Không dùng bộ nhận dạng của PaddleOCR vì không mô hình chính thức nào viết
được tiếng Việt: từ điển ký tự của PP-OCRv6 (thứ `lang="vi"` chọn) và latin
PP-OCRv5 có "ư", "đ" nhưng không có nguyên âm mang dấu thanh (ợ, ạ, ệ…), nên
"Quang hợp ở thực vật" ra "Quang hp  thc vt". VietOCR được huấn luyện trên cả
chữ in lẫn chữ viết tay tiếng Việt.

Mọi mô hình được tải **lúc build** và nằm sẵn trong image (VietOCR ở
`/app/models/vietocr`), nên container khởi động không cần mạng. PyTorch là bản
CPU-only.

Kỳ vọng thực tế: chữ in/đánh máy tiếng Việt đọc khá tốt; chữ viết tay — nhất là
dấu và công thức — sai nhiều hơn. Vì vậy dòng độ tin cậy thấp được **đánh dấu,
không bị xoá**, và người học luôn sửa văn bản trước khi LLM đọc.

## Biến môi trường

| Biến | Mặc định | |
|---|---|---|
| `OCR_DOC_ORIENTATION` | `1` | Tự xoay trang 90/180/270° |
| `OCR_ENABLE_MKLDNN` | `0` | oneDNN của Paddle; tắt vì Paddle 3.3 lỗi với PP-OCRv6 trên CPU |
| `OCR_VIETOCR_DIR` | `/app/models/vietocr` (image) | Config + trọng số VietOCR; trống thì tải lần đầu |
| `OCR_MAX_PAGES` | `30` | Số trang tối đa mỗi lần |
| `OCR_MAX_UPLOAD_MB` | `40` | Dung lượng tối đa mỗi request |
| `OCR_PDF_DPI` | `200` | Độ phân giải render PDF |
| `OCR_MAX_IMAGE_SIDE` | `4000` | Ảnh lớn hơn được thu nhỏ trước khi OCR |

## Test

Không cần nạp mô hình — sắp dòng, xử lý tệp và cắt/xoay ảnh tách riêng khỏi engine:

```bash
docker compose run --rm ocr python -m pytest -q
```

## Build

`docker-compose.yml` build với `network: host`: trên máy dev, tải tệp lớn từ bên
trong mạng bridge của Docker bị treo vô hạn, mà bước build phải tải PaddlePaddle,
PyTorch và mô hình.
