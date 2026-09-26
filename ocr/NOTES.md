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

## 2026-09-25 — Chữ viết tay thật (vở của người học)

**Dữ liệu.** 3 ảnh chụp điện thoại vở thật, 3046 ký tự, 604 ký tự mang dấu:

- 1 trang Hoá (phân bón), vở kẻ ngang, ảnh nhỏ 525×1280, mép trái bị cắt.
- 2 trang Sinh (trao đổi chất) viết kiểu **Cornell**: cột gợi ý bên trái, cột
  ghi chép bên phải, phần tóm tắt ở cuối trang 2. Ảnh 846×1280 và 1440×2228.

Văn bản gốc do Claude gõ lại từ ảnh, theo đúng chữ viết (giữ chữ viết tắt
`nl`, `sv`, `qtrình`), và **chưa được người học xác nhận**. Ảnh và văn bản
gốc **không** commit vì đây là vở cá nhân. OCR chạy qua đúng đường của ứng dụng
(`POST /notes` → service OCR), image `ghcr.io/qinzinn/chiron-ocr:0.1.0`.

Dòng của cột gợi ý được đặt cạnh dòng ghi chép nằm cùng độ cao, đúng thứ tự
OCR đọc, để CER không phạt thứ tự. Cả hai phía chuẩn hoá cách đặt dấu cũ/mới
(`hoá`/`hóa`, `luỹ`/`lũy`) trước khi so.

**Số đo.**

| Trang | Ký tự | CER | Ký tự có dấu sai | Từ đúng hẳn | Từ chỉ sai dấu | Confidence |
|---|---:|---:|---:|---:|---:|---:|
| Hoá · phân bón | 914 | 26,3 % | 59 / 162 | 60,8 % | 23 | 0,799 |
| Sinh · trang 1 (Cornell) | 922 | 15,2 % | 53 / 177 | 69,2 % | 27 | 0,822 |
| Sinh · trang 2 (Cornell + tóm tắt) | 1210 | 23,2 % | 110 / 265 | 52,7 % | 54 | 0,811 |
| **Tổng** | **3046** | **21,7 %** | **222 / 604** | **60,2 %** | **104 (16,0 %)** | |

- Chỉ tính chữ cái/chữ số: CER 19,4 %. Không phân biệt hoa thường: 20,8 %.
- "Từ đúng hẳn" so các từ như một tập hợp, không tính thứ tự và không phân
  biệt hoa thường. 23,8 % số từ sai chữ hoặc mất hẳn.
- Lỗi điển hình:
  - Dấu thanh: `lượng`→`lường`, `dưỡng`→`dương`/`đường`, `tự`→`từ`.
  - Nhầm chữ: `Chiếm`→`Thiêm`, `lũy`→`lấy`, `hữu cơ`→`hiểu ả`.
  - Ký hiệu: `&`→`b`/`k`/`8`, `⇄`→`2`, `+`→`t`/`4`.
  - Chỉ số dưới: `H2S`, `NO2-` và các đơn vị như `mg/kg` ra rác.
  - Dòng mép phải bị cắt trên ảnh Hoá thì mất chữ.
- **Cornell:** vì dòng được nhóm theo độ cao, câu của cột gợi ý bị dính vào
  đầu dòng ghi chép cùng độ cao (`Duy trì sự sống. Giúp sinh vật tồn tại…`,
  `Tự dưỡng: tự sống, tự hấp Dùng chất vô cơ…`). Chữ vẫn đọc được, nhưng văn
  bản trộn hai cột; người học phải tách lại ở bước sửa.

**Confidence và "Dòng nên kiểm tra".** Confidence có tụt so với chữ in
(0,80–0,82 so với 0,92), nhưng chỉ tương quan vừa với lỗi của dòng (r = −0,46).
Có 29/91 dòng bị đánh dấu (dưới 0,80), với CER dòng trung vị 27 %; dòng không
bị đánh dấu có trung vị 18 %. Trong 46 dòng có CER > 20 %, **chỉ 21 dòng được
đánh dấu**.

**Kết luận.**

1. Với chữ viết tay, OCR chỉ là **bản nháp**: khoảng 4/10 từ phải sửa, và 1/3
   số ký tự có dấu bị sai. Bước sửa văn bản trước khi rút khái niệm là bắt buộc,
   không phải tuỳ chọn.
2. "Dòng nên kiểm tra" bỏ sót hơn một nửa số dòng sai nặng. Giao diện không
   được ngụ ý rằng các dòng không bị đánh dấu là đúng.
3. Bố cục Cornell cần được tách cột trước khi nhóm dòng; hiện tại hai cột bị
   trộn. Chưa sửa.
4. Ảnh độ phân giải thấp (trang Hoá, 525 px ngang) cho CER cao nhất. Chụp gần
   hơn, hoặc gửi ảnh gốc thay vì ảnh đã nén qua ứng dụng chat, có thể giúp;
   chưa đo.

## Chưa đo

- Chữ viết tay của người khác, và chữ viết tay ở độ phân giải gốc (chưa nén).
- Hiệu quả của việc tách cột Cornell (chưa làm).
