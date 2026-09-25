# OCR — số đo độ chính xác

Độ tin cậy (`mean_confidence`) là con số mô hình tự báo, **không phải độ
chính xác**. File này ghi độ chính xác đo được, so với văn bản gốc do người gõ.
Cách chạy lại: [`bench/README.md`](bench/README.md).

## 2026-09-25 — Chữ in tiếng Việt, ảnh dựng từ font

**Dữ liệu.** 6 trang, 2119 ký tự, 415 ký tự mang dấu. Văn bản gốc nằm trong
`bench/ground_truth.json`; ảnh do `bench/gen.py` dựng. Nội dung kiểu sách giáo
khoa (Sinh, Lý, Sử, Văn), có công thức (`6CO2 + 6H2O → …`, `Φ = B·S·cosα`,
`e = −ΔΦ/Δt`).

- 4 trang sạch: Noto Serif 34 px, Roboto 30 px, Liberation Serif 32 px,
  DejaVu Sans 26 px.
- 2 trang giả ảnh chụp: nền giấy ngả vàng, sáng không đều, nghiêng 1,8°, mờ
  Gaussian 1,1 px, JPEG chất lượng 70.

Engine: `PP-OCRv6_medium_det + vietocr/vgg_transformer` (image `chiron-ocr`
build 2026-09-17).

**Số đo.** CER là khoảng cách Levenshtein ở mức ký tự chia cho độ dài văn bản
gốc, sau khi gộp mọi khoảng trắng thành một. "Lỗi dấu" là phép thay thế giữ
nguyên chữ gốc, chỉ sai dấu (ví dụ `ề`→`ể`).

| Trang | Ký tự | Lỗi | CER | Lỗi dấu | Confidence |
|---|---:|---:|---:|---:|---:|
| sinh-serif | 393 | 7 | 1,78 % | 0 | 0,916 |
| ly-sans | 359 | 10 | 2,79 % | 0 | 0,922 |
| su-times | 305 | 0 | 0,00 % | 0 | 0,923 |
| van-dejavu | 310 | 4 | 1,29 % | 3 | 0,917 |
| sinh-serif-photo | 393 | 8 | 2,04 % | 0 | 0,920 |
| ly-sans-photo | 359 | 16 | 4,46 % | 0 | 0,922 |
| **Tổng** | **2119** | **45** | **2,12 %** | **3 / 415 ký tự có dấu** | |

45 lỗi chia theo loại ký tự gốc:

- **26 lỗi ký hiệu/công thức (58 %).** `+`→`%`, `→`→`ở`, `=`→`-`, `·`→`-`,
  `Φ`→`Đ`/`D`/`0`/`P`, `Δ`→`A`, `α`→`a`, dấu trừ `−` bị bỏ.
- **9 lỗi dấu câu.** Chủ yếu là mất dấu chấm cuối dòng, hay gặp ở ảnh chụp.
- **10 lỗi chữ cái/chữ số (CER 0,64 % trên 1570 chữ).** 6 lỗi là `O`→`0`
  trong công thức hoá học (`CO2`, `O2`), 1 lỗi `V`→`v`, và 3 lỗi dấu thanh ở
  cùng trang DejaVu: `chiều`→`chiểu`, `cõi`→`cối`, `bể`→`bế`.

Thời gian: 4,3–5,6 giây mỗi trang trên CPU.

**Kết luận.**

1. Chữ in tiếng Việt thường đọc rất tốt: trên chữ cái, 0,64 % lỗi và 3/415 ký
   tự có dấu bị sai. Ảnh giả chụp chỉ tăng CER nhẹ (1,78 % → 2,04 %), trừ trang
   có công thức.
2. **Công thức và ký hiệu là điểm yếu chính.** VietOCR không có `Φ`, `Δ`, `→`,
   `·` trong bảng ký tự nên luôn đọc sai chúng. Với ghi chép Lý/Hoá, người học
   phải sửa tay các công thức ở bước duyệt văn bản.
3. **Confidence không phản ánh lỗi.** Trang không lỗi nào (0,923) và trang lỗi
   4,46 % (0,922) có confidence gần như bằng nhau. Không có dòng nào dưới
   ngưỡng 0,80, nên "Dòng nên kiểm tra" trên giao diện bỏ sót toàn bộ 45 lỗi.
   Không nên dựa vào đó để báo người học rằng văn bản đã ổn.
4. Đây là ảnh dựng từ font, sạch hơn ảnh chụp vở thật. Số đo này là **cận
   trên** cho chữ in, **chưa nói gì về chữ viết tay**.

## Chưa đo

- **Chữ viết tay:** cần ảnh vở thật của người học và văn bản gốc tự gõ cho
  các trang đó.
- **Ảnh chụp thật** (máy ảnh điện thoại, giấy cong, bóng tay).
