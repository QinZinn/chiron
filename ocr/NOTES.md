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

## 2026-09-26 — Vở viết tay tiếng Anh, bố cục thường (không Cornell)

**Dữ liệu.** 19 ảnh vở ghi một khoá kỹ năng học (tiếng Anh, lẫn vài từ tiếng
Việt), 960×1280, khoảng 110–150 KB mỗi ảnh (nhiều khả năng đã nén qua app chat).
Đo trên 5 trang chọn theo quy tắc cố định **trước khi** xem kết quả: trang 1, 5,
9, 13, 17 theo thứ tự tên file. 3123 ký tự. Văn bản gốc do Claude gõ lại, chưa
được người học xác nhận; không commit.

**Số đo** (không phân biệt hoa thường). Cùng bộ phát hiện dòng và cùng cách cắt;
chỉ đổi bộ đọc chữ:

| Bộ đọc chữ | CER | CER chỉ chữ cái | Từ đúng hẳn | Confidence |
|---|---:|---:|---:|---:|
| VietOCR vgg_transformer (đang dùng) | 42,0 % | 36,7 % | 30,1 % | 0,71–0,79 |
| PaddleOCR PP-OCRv6_medium_rec | **32,5 %** | **29,0 %** | **51,1 %** | 0,79–0,91 |

Theo trang, VietOCR → Paddle: 27,4 → 16,0 %; 34,5 → 24,9 %; 53,1 → 34,4 %;
53,1 → 50,7 %; 33,3 → 30,8 %.

**Quan sát.**

- VietOCR bịa ra tiếng Việt khi đọc chữ viết tay tiếng Anh:
  `L: "I don't believe it"` → "Nên có thứ tropersitional", một dòng khác →
  "Thời tổ chị yên giái thị thị".
- PaddleOCR không có nguyên âm mang dấu thanh: "quý ông" ra 2/2 ký tự có dấu
  sai (đúng với phát hiện ngày 25/09 trên chữ in).
- Trang 13 tệ với cả hai bộ đọc (khoảng 51 %). Lỗi nằm ở bố cục: một hộp chữ
  bên phải bị nối vào dòng bên trái (giống cột Cornell), cộng với số mũ
  `1.10^999` và chữ bị gạch.
- So với vở Sinh tiếng Việt (CER 21,7 %), vở này tệ hơn gấp đôi với bộ đọc
  đang dùng. Nguyên nhân chính là ngôn ngữ, không phải bố cục Cornell.

**Kết luận.** Không bộ đọc nào tốt cho cả hai ngôn ngữ: VietOCR cho tiếng Việt,
Paddle cho tiếng Anh. Chọn bộ đọc theo ngôn ngữ của ghi chép là quyết định
chưa chốt.

## 2026-09-26 — English only: choosing the recogniser

Chiron is being built for an international hackathon and is now English only,
so VietOCR (Vietnamese) is gone. Six PaddleOCR recognisers were compared, each
on **the same detector output and the same line crops**, so only the
recogniser differs.

**Data.**

- Printed English: `bench/` — 6 pages, 2260 characters, 4 clean renders and 2
  fake phone photos (tinted paper, 1.8° tilt, blur, JPEG 70), with formulas
  (`6CO2 + 6H2O → …`, `Φ = B·A·cosθ`, `ε = −ΔΦ/Δt`).
- Handwritten English: 5 real notebook pages (a study-skills course), picked by a
  fixed rule before looking at any output — pages 1, 5, 9, 13, 17 by file
  name. 3123 characters. Ground truth typed by Claude from the photos, not
  confirmed by the learner; not committed (personal notes). 960×1280 JPEGs of
  about 120 KB, probably compressed by a chat app.

**Measurements** (case-insensitive; "words" = order-free exact word matches).

| Recogniser | Printed CER | Printed words | Handwritten CER | Handwritten words | Speed |
|---|---:|---:|---:|---:|---:|
| **PP-OCRv6_small_rec** | **0.35 %** | **98.9 %** | **27.95 %** | 55.2 % | **1.3 s/page** |
| en_PP-OCRv5_mobile_rec | 0.62 % | 98.1 % | 29.33 % | **57.8 %** | 2.7 s/page |
| latin_PP-OCRv5_mobile_rec | 0.35 % | 98.6 % | 31.96 % | 50.2 % | 2.7 s/page |
| PP-OCRv6_medium_rec | 0.44 % | 98.9 % | 32.47 % | 51.1 % | 3.0 s/page |
| PP-OCRv5_server_rec | 30.00 % | 43.8 % | 34.29 % | 45.0 % | 4.2 s/page |
| PP-OCRv4_server_rec | 3.41 % | 81.2 % | 44.09 % | 17.1 % | 4.1 s/page |
| *VietOCR vgg_transformer (previous)* | — | — | *42.01 %* | *30.1 %* | — |

**Conclusions.**

1. `PP-OCRv6_small_rec` is the default: lowest CER on both sets and the fastest.
   `en_PP-OCRv5_mobile_rec` matches 2.6 points more handwritten words, which on
   five pages is within noise; it stays one `OCR_REC_MODEL` away.
2. Replacing VietOCR cuts handwritten English CER from 42 % to 28 % — VietOCR
   invented Vietnamese on English lines (`L: "I don't believe it"` → "Nên có
   thứ tropersitional").
3. Printed English is essentially solved (4 letter errors in 1814). Handwriting
   is not: about one word in two still needs correcting, and two-column
   layouts (a box beside the notes) are merged line by line whatever the
   recogniser — page 13 stays around 50 % CER with every model.

## Chưa đo

- Chữ viết tay của người khác, và chữ viết tay ở độ phân giải gốc (chưa nén).
- Hiệu quả của việc tách cột Cornell (chưa làm).
