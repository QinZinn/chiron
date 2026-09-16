# NOTES — quyết định, giới hạn, và bẫy đã trả giá

## Quyết định đã chốt (không bàn lại)
- Postgres `nodes` + `edges`, **không Neo4j**.
- Node = một khái niệm, **không phải một phiên học**.
- Dò trùng bằng full-text `pg_trgm`, ngưỡng **0.6**. **Không embedding, không pgvector** —
  nâng cấp chờ bằng chứng thật từ Mnemosyne.
- Edge: LLM gợi ý → `pending` → người duyệt → `approved`. **Không auto-approve.**
- `subject` là TEXT tự do, **KHÔNG enum** (Horae không có taxonomy môn học đóng kín).
- `rejected` / `discarded` giữ vĩnh viễn, không xoá — "không hỏi lại câu người dùng đã trả lời".
- Merge node dùng `merged_into_id`, **không xoá cứng**.
- Mnemosyne ghi sau cả phiên, không sau mỗi câu trả lời.
- Lưu transcript raw TRƯỚC, extraction là job riêng retry được.
- LLM client viết riêng cho KS, không import gì từ Horae, nhưng cùng quy ước.

## Hai hàm cốt lõi ĐỐI LẬP nhau có chủ đích
| Hàm | Hành vi |
|---|---|
| `save_transcript` | **KHÔNG BAO GIỜ raise.** Nuốt mọi lỗi kể cả mất kết nối DB → `SaveResult(ok=False)`. Phiên học không được hỏng vì KS chết; Mnemosyne là system of record. |
| `ingest_concepts` | **Fail-loud.** `psycopg.Error` văng thẳng ra. Wrapper HTTP bắt → 503. |

## Giới hạn đã biết — KHÔNG sửa, chỉ khoá bằng test
Dedup trigram gộp nhầm khái niệm tên gần giống. Khoá bằng
`tests/test_dedup_limits.py::test_numbered_variants_are_wrongly_deduped`.

### Ba evidence point — đã truy lại được, môi trường KHÔNG đổi
Đo trên PostgreSQL 18.6, pg_trgm 1.6, collation `en_US.UTF-8`, provider `libc`:

| Chuỗi A (nguyên văn) | Chuỗi B (nguyên văn) | similarity | Gộp ở 0.6? |
|---|---|---|---|
| `Định luật Newton 1` | `Định luật Newton 2` | 0.8095 | **có** |
| `Định luật khúc xạ ánh sáng` | `Định luật phản xạ ánh sáng` | 0.6774 | **có** |
| `Định luật Ohm (curl-nodes)` | `Định luật Newton 2 (curl-nodes)` | 0.6364 | **có** |
| `khúc xạ` | `phản xạ` | 0.2308 | không |
| `Định luật Ohm` | `Định luật Newton 2` | 0.4348 | không |

Cả ba evidence point của lần build trước đều tái hiện **chính xác** khi đo đúng
chuỗi (0.636 → 0.6364; 0.677 → 0.6774). Ban đầu tôi đo trên cặp title trần
(`khúc xạ` vs `phản xạ`, `Định luật Ohm` vs `Định luật Newton 2`) và kết luận
nhầm rằng môi trường đã đổi. **Không có khác biệt môi trường nào.** Giả thuyết
collation đã bị bác bỏ, không cần điều tra thêm.

Cơ chế: phần chung của hai chuỗi chiếm đa số trigram. Cùng tiền tố
`"Định luật "` cộng cùng hậu tố `" ánh sáng"` / `" (curl-nodes)"` đẩy similarity
từ 0.23 lên 0.68 dù phần khác biệt y hệt nhau. Hậu tố dùng chung là thứ nguy
hiểm nhất cho dedup trigram — và title thật từ Mnemosyne rất dễ có hậu tố chung
(tên chương, tên môn, tên bộ đề).

### QUY TẮC: cách ghi một evidence point
Ghi lại một con số mà không ghi chuỗi đầu vào chính xác thì **không tái sử dụng
được** — đó là bài học đắt nhất rút ra ở đây. Hai trong ba số cũ suýt bị diễn
giải thành "môi trường đã đổi" chỉ vì thiếu chuỗi gốc.

Từ nay mọi evidence point về dedup PHẢI ghi đủ:
1. **Nguyên văn cả hai chuỗi**, kể cả tiền tố/hậu tố trông như rác kỹ thuật
   (`(curl-nodes)` chính là thứ tạo ra con số).
2. `SELECT extversion FROM pg_extension WHERE extname = 'pg_trgm';`
3. `SELECT datcollate, datctype, datlocprovider FROM pg_database WHERE datname = current_database();`

Thiếu ba thứ này thì đến lúc quyết pgvector sẽ không biết số cũ nghĩa là gì.
`tests/test_dedup_limits.py` khoá cả ba bằng test, gồm cả dấu vân tay môi trường.

## QUY TẮC: trao đổi dữ liệu thô, không trao đổi kết luận

Ba lần trong đợt làm việc với Mnemosyne, KS kết luận rộng hơn dữ liệu cho phép,
và cả ba đều bị bên kia bắt được:

1. "restart `chiron-ks-http` kích hoạt fallback của Mnemosyne" — sai, restart
   cho `connection refused` → `knowledge_store_error`. Fallback chỉ chạy khi KS
   sống nhưng chạy code cũ.
2. Test truncated dùng body có field `message` — body thật chỉ có `error` và
   `reason`.
3. "0 dòng 502 nên không có lỗi cũ cần diễn giải lại" — đúng ra là giả thuyết
   KHÔNG CÓ CA NÀO ĐỂ KIỂM, chưa bị bác bỏ.

Cả ba đều là kết luận rút ra từ chỗ **chỉ một bên nhìn thấy dữ liệu**. KS không
có cách nào biết `restart` cho `connection refused` thay vì HTML 404, vì hành vi
đó nằm trong client phía Mnemosyne.

Thứ làm chúng lộ ra không phải sự cẩn thận của bên nào, mà là việc **hai bên
viết ra đủ cụ thể để bên kia đối chiếu được với thứ mình đang cầm**: KS gửi
payload nguyên văn thay vì mô tả, Mnemosyne gửi bảng số thay vì kết luận. Nhờ
vậy chỗ lệch mới va vào nhau thay vì trôi qua.

Áp dụng: khi báo cáo qua ranh giới module, gửi payload/số đo nguyên văn kèm
theo kết luận, đừng gửi mỗi kết luận. Cùng gốc với quy tắc evidence point ở trên.

## Nợ kỹ thuật đã ghi nhận
- `find_candidates` dùng `similarity()` chứ không dùng toán tử `%`, nên **không
  dùng GIN index**. Đổi lại: ngưỡng không phụ thuộc GUC `pg_trgm.similarity_threshold`
  của session. Chấp nhận được ở quy mô một người dùng.

## deepseek-v4-flash là model REASONING — reasoning token tính vào max_tokens

Phát hiện khi chạy thật, không phải suy đoán. Một lần gọi gợi ý edge với 2 ứng viên:

```
completion_tokens: 222
  completion_tokens_details.reasoning_tokens: 158   ← 71% ngân sách
prompt_tokens: 474 (cached 384)
```

Lượng reasoning thay đổi mỗi lần chạy. Với `max_tokens=1000`, đã có lần reasoning
ngốn gần hết ngân sách và chỉ còn ~15 token cho JSON → phản hồi cụt giữa chừng:

```
[
  {
    "candidate": 0,
    "relation_type": "pr        ← hết token ở đây
```

Hai thứ đã sửa:
1. `EDGE_SUGGESTION_MAX_TOKENS` / `EXTRACTION_MAX_TOKENS` = **4000**. Đừng hạ hai
   số này theo độ dài output NHÌN THẤY được (~200 ký tự) — phần lớn ngân sách là
   reasoning vô hình. Chi phí chỉ tính theo token thực sinh ra; cắt ngang thì
   hỏng cả lô.
2. `LLMTruncatedError` (con của `LLMTransientError`) bắt `finish_reason == "length"`
   ở OpenAI-compatible và `stop_reason == "max_tokens"` ở Anthropic. Trước đó JSON
   cụt lọt xuống `json.loads` và hiện ra dưới dạng `LLMParseError` — chẩn đoán sai
   hoàn toàn, vì cấu trúc phản hồi không hề sai, nó chỉ chưa viết xong. Là lớp con
   của Transient nên retry được: cùng `max_tokens` lúc đủ lúc không.

Instrumentation đã ghi đúng sự cố này (`edge_suggestion_run.outcome = 'parse_error'`)
— đây chính là bằng chứng ràng buộc §7 có tác dụng thật.

## card_sync: trạng thái verify từng nhánh

Năm ca đã chạy thật với Mnemosyne sống trên `127.0.0.1:8081`:

| Ca | Verify | Kết quả |
|---|---|---|
| 201 card mới | ✅ thật | `sent` |
| 409 card đã có | ✅ thật | `sent` (Mnemosyne check TRƯỚC khi gọi LLM → không tốn token) |
| 404 set/node sai | ✅ thật | `skipped`, không retry |
| Không gọi nổi Mnemosyne | ✅ thật | dừng cả lô, `attempts` giữ nguyên |
| 503 KS chưa cấu hình | ❌ chỉ fake | `pending` |
| 502 `reason="truncated"` | ⚠️ đã thử 6 node, không tái hiện | `failed`, không retry |

### ĐÃ SĂN: 6 node, KHÔNG tái hiện được truncation tự nhiên qua card_sync

Mục tiêu: ép một ca `reason="truncated"` **tự nhiên** (không hạ `max_tokens`)
đi qua đúng đường `card_sync` → `POST /cards/from_node` → DeepSeek mặc định.
Kết quả: **6/6 node đều `sent` (201). Không ca nào truncated.**

| Node | prompt (ký tự) | token tiêu | Kết quả |
|---|---|---|---|
| Sự hình thành và tiến hóa của sao | 2345 | 1925 | sent |
| Định lý bất toàn Gödel | 3023 | **5164** | sent |
| Nghịch lý Sorites | 1059 | 1116 | sent |
| Mèo Schrödinger | 1092 | **3058** | sent |
| Con tàu Theseus | 1034 | 1150 | sent |
| Bài toán xe điên | 1247 | **3811** | sent |

**Giả thuyết ban đầu SAI, và số đo bác bỏ nó.** Tôi cho rằng summary dài sẽ đẩy
tới trần. Mnemosyne chỉ ra lỗi lập luận: summary dài làm tăng token phía
**prompt**, còn `max_tokens` chỉ chặn phía **completion** — hai ngân sách khác
nhau. Biến thật là **độ khó suy luận**.

Số đo xác nhận họ đúng: Schrödinger prompt 1092 ký tự đốt 3058 token, còn
Theseus prompt 1034 ký tự chỉ đốt 1150 — cùng độ dài, chênh 2,7 lần. Node cuối
(“Bài toán xe điên”, thiết kế riêng để tối đa hoá cân nhắc: bốn khung đạo đức
cạnh tranh cộng một trực giác đảo chiều) đốt 3811 token với prompt chỉ 1247 ký
tự. Hướng đúng, nhưng vẫn không chạm trần.

Cao nhất quan sát được là **5164 token, vẫn thành công** — nên ngân sách mặc
định của DeepSeek còn dư trên mức đó.

**Kết luận (hợp lệ, không phải bế tắc):** ở phân bố dữ liệu hiện tại, truncation
tự nhiên qua `card_sync` **hiếm tới mức 6 lần thử có chủ đích không gặp**. Điều
này CỦNG CỐ quyết định giữ `failed` / không retry: một hiện tượng hiếm tới vậy
không đáng đánh đổi lấy rủi ro retry mù. Bảng sáu nhánh dưới giữ nguyên nhãn
`fake` cho dòng này, nhưng ghi chú đổi từ “chưa thử” thành “đã thử 6 node,
không tái hiện”.

### ⚠️ Timeout 30s của KS đã CHE MẤT một phân loại thật
Phát hiện ngoài dự kiến trong lúc săn. `HttpCardClient` đặt timeout cứng 30 giây.
Node Gödel mất hơn 30s để sinh card, nên KS bỏ cuộc và ghi `CardClientError:
timeout`, **mất luôn** phân loại thật mà Mnemosyne sắp trả về.

Timeout ngắn tệ hơn là chậm: nó ghi đè mọi `reason` (`truncated` /
`provider_error` / `knowledge_store_error`) thành một lỗi hạ tầng vô nghĩa. Đúng
một lần nó đã che mất ca đang cần quan sát.

Đã sửa: `KS_MNEMOSYNE_TIMEOUT`, mặc định **180 giây**. Chạy lại cùng node đó với
timeout rộng thì ra `sent` (201) sau 36 giây.

**Nghi vấn đã bị BÁC BỎ — Actix KHÔNG huỷ handler khi client ngắt kết nối.**
Tôi từng nghi timeout của KS làm huỷ request DeepSeek đang dở phía Mnemosyne.
Sai. Mnemosyne chứng minh bằng thí nghiệm: giết client sau 2 giây
(`curl --max-time 2`), handler của họ vẫn chạy tới cùng và **tạo card bình
thường** 3 giây sau đó.

Đáng chú ý hơn: phản chứng chặt nhất nằm ngay trong dữ liệu tôi đã cầm. Nếu
handler bị huỷ thì dòng `ai_interactions` lúc 15:07:58 **không thể tồn tại** —
future bị drop thì không chạy nhánh lỗi, không INSERT được gì. Dòng đó có mặt,
tức handler vẫn sống 31 giây sau khi KS bỏ cuộc. Chính khoảng lệch thời gian mà
tôi thấy khả nghi lại là bằng chứng bác bỏ. Bài học: tôi có sẵn phản chứng và
không dùng.

Lỗi EOF thật sự là gì: message của Mnemosyne kèm `body snippet:` **rỗng**, tức
DeepSeek trả **HTTP 2xx kèm body rỗng**. Kết nối đứt giữa chừng thì reqwest báo
`Network` chứ không phải `Parse`. Đây là bất thường phía upstream, không liên
quan tới KS.

### Lai lịch 6 node trong DB production — GIỮ có chủ đích, không phải rác
Sáu node dưới đây được tạo trong vòng săn truncation, **không phải vì có học
sinh nào học chúng**. Người dùng đã quyết giữ; cả hai phía không dọn.

`Sự hình thành và tiến hóa của sao` · `Định lý bất toàn Gödel` ·
`Nghịch lý Sorites` · `Mèo Schrödinger` · `Con tàu Theseus` · `Bài toán xe điên`

Chúng là khái niệm mạch lạc, đã tốn token thật để sinh card, và mỗi node có đúng
một card trong set "KS review" phía Mnemosyne (tổng 8 card = 2 node cũ + 6 node
này). Giữ chúng không gây hại, và xoá thì tốn công phối hợp hai phía.

**Nếu sau này quyết dọn: phải dọn ĐỒNG THỜI hai phía.** Xoá node phía KS mà để
card lại thì card mồ côi; xoá card phía Mnemosyne mà để node lại thì `card_sync`
sinh lại chúng ở lần chạy kế tiếp. Không bên nào tự dọn một mình được.

### Timeout sinh ra KẾT QUẢ MỒ CÔI, không phá việc
Vì handler bên kia chạy tới cùng, timeout của KS không huỷ gì cả — nó tạo ra
tình trạng **hai bên tin hai chuyện khác nhau về cùng một node**: Mnemosyne có
card, KS ghi hỏng.

Hệ thống tự hoà giải, nhưng chỉ nhờ một chuỗi hai bước mà **cả hai bước đều bắt
buộc**:

1. Timeout ghi `pending`, **không phải** `failed` → node còn được chọn lại.
2. Lần sau nhận `409` → ghi `sent`, vì **409 không phải lỗi**. Mnemosyne cố ý
   trả kèm `existing_card_id` chính vì mục đích hoà giải này.

Đổi bất kỳ bước nào cũng làm ca mồ côi mắc kẹt vĩnh viễn. Đã khoá bằng test
`test_timeout_roi_409_tu_hoa_giai_ket_qua_mo_coi` chạy đúng chuỗi đó.

Ca Gödel thực tế **không** mồ côi — lần đó handler của họ cũng thất bại thật
(body rỗng), nên set "KS review" có đúng 8 card, không dư. Nhưng nếu DeepSeek
trả lời bình thường thì đã có một card mà KS ghi là hỏng.

### `truncated`: wire format đã xác nhận, đường KS vẫn chưa chạy thật
Mnemosyne đã ép được truncation qua API thật (vá tạm `max_tokens=200` trong
client của họ, chạy một lần, bỏ vá không commit). Cả ba giá trị `reason` giờ
đều đã thấy trên dây thật. Body nguyên văn:

```json
{"error": "DeepSeek stopped mid-answer at its token limit (length); nothing was parsed. Retrying, or requesting fewer items, may succeed.",
 "reason": "truncated"}
```

**Không có field `message`** — bản test cũ của KS bịa ra field đó. Test giờ
anchor vào payload nguyên văn ở trên (`TRUNCATED_BODY` trong
`tests/test_card_sync.py`), đúng bài học evidence point: neo vào wire thật,
đừng neo vào tưởng tượng.

Vẫn phải nói cho đúng phạm vi: **`card_sync` của KS chưa từng NHẬN một response
truncated thật.** Probe của Mnemosyne gọi thẳng endpoint của họ, không đi qua
job này. Cái đã được xác nhận là *hình dạng dữ liệu*, không phải *đường đi*.
`_decide()` đã được kiểm bằng chính payload đó và trả `failed` đúng thiết kế.

### CHỐT: KHÔNG retry `truncated`. Quyết định đã đóng, đừng mở lại.
Brief chốt "KHÔNG retry cùng input — gần như chắc chắn lặp lại y hệt".
Mnemosyne đo 40 call thật (cùng node, cùng prompt, 10 lần mỗi mức ngân sách):

| max_tokens | truncated | reasoning quan sát | retry cùng input thành công |
|---|---|---|---|
| 200 | 10/10 | 200 (đụng trần) | 0/10 |
| 350 | 10/10 | 232 – 350 | 0/10 |
| 500 | 8/10 | 78 – 500 | 2/8 |
| 650 | 7/10 | 162 – 650 | 2/7 |

Cả hai câu khẳng định trước đó đều sai một nửa: dưới vùng biên retry thành công
**0/20**, trong vùng biên **2–3/10**. Retry đáng giá nhưng chỉ ở vùng biên, và
nhiều nhất 1–2 lượt.

**CẢNH BÁO khi đọc bảng này — đừng mang tỉ lệ 2–3/10 sang vận hành thật.**
Mnemosyne **không gửi `max_tokens`**, dùng mặc định của model. Nên truncation
trong vận hành nghĩa là reasoning đã ăn hết TOÀN BỘ ngân sách mặc định — rơi ra
**ngoài** vùng đo được ở trên. Không ai có số cho chế độ đó và không suy ra được.

**Agent A đã chốt: giữ `failed`, không retry.** Lý do nêu rõ khi chốt — *không
đổi hành vi dựa trên số liệu đo khác phạm vi cần quyết*. Bảng 40-call là dữ liệu
tốt, nhưng đo trong dải ép `max_tokens` thấp, còn phạm vi cần quyết là chế độ
mặc định của model. Số liệu tốt ở sai phạm vi vẫn là sai căn cứ.

Đây là quyết định ĐÃ ĐÓNG. Chỉ mở lại khi có số đo trong ĐÚNG chế độ vận hành
(không ép `max_tokens`). Nếu khi đó đổi ý, chỗ sửa là nhánh `truncated` trong
`ks/card_sync.py::_decide()`, và nó nên dùng chung ngân sách retry với
`provider_error` chứ không retry vô hạn.

### Reply bị cắt THƯỜNG có nội dung — đây mới là cái bẫy thật
Ở `max_tokens=350`, **5/10** call truncated vẫn trả về content thật (JSON viết
dở, tới 310 ký tự). Không phải ca hiếm.

Không check `finish_reason` thì đám đó đi thẳng vào parser và báo lỗi **định
dạng**, khiến người đọc log đi soi prompt trong khi lỗi thật là **ngân sách
token**. Đúng bug production ban đầu của KS — trước khi có `LLMTruncatedError`,
`suggest_edges` đã báo `parse_error` cho chính ca này.

KS được bảo vệ: trong `ks/llm.py`, `finish_reason == "length"` được kiểm TRƯỚC
khi đọc `content`, nên phản hồi cắt-nhưng-có-nội-dung vẫn thành `LLMTruncatedError`.
Ba test khoá lại:
- cắt kèm JSON dở → `LLMTruncatedError`, không phải `LLMParseError`
- **cắt kèm JSON HỢP LỆ CÚ PHÁP** → vẫn `LLMTruncatedError`. Đây là ca âm thầm
  nguy hiểm nhất: model viết xong `]` rồi mới cạn token, chuỗi parse được nhưng
  nội dung THIẾU. Chấp nhận nó là im lặng mất dữ liệu. Ngân sách phải thắng cú pháp.
- đối chứng `finish_reason == "stop"` → đi qua bình thường, không chặn nhầm

Kiểm lại lịch sử `ks.card_sync_log`: **không có dòng 502 nào**. Đọc cho đúng —
nghĩa là giả thuyết "lỗi parse cũ thật ra là truncation" **không có ca nào để
kiểm chứng**, KHÔNG phải đã bị bác bỏ. Sạch theo nghĩa không có nợ cũ, không
phải theo nghĩa đã chứng minh được điều gì.

Chi tiết tái hiện nằm ở `docs/gotchas.md` mục 2 phía Mnemosyne.

### Biến động chi phí một prompt: gấp 4 lần
Đo của KS (65 → 200) đúng và còn nhẹ. Ở `max_tokens=650`, cùng một request tiêu
từ **162 tới 650** reasoning token, không có gì thay đổi phía người gọi. **Bất
kỳ logic nào giả định một prompt có chi phí ổn định đều sai** — kể cả việc chọn
`max_tokens` theo độ dài output nhìn thấy được.

### ⚠️ `tokens_used` bên Mnemosyne ghi 0 cho mọi lượt fail
Mnemosyne tự phát hiện: call bị truncated **vẫn đốt token thật** (~200 ở lần
probe) nhưng `ai_interactions.tokens_used` ghi `0`, vì `LLMError::Truncated`
không mang theo `usage`. Mọi lượt thất bại đều vô hình trong sổ chi phí — và
truncation là loại đắt nhất, vì reasoning token đã cháy hết trước khi hỏng.

Ảnh hưởng tới KS: **không**. `ks stats` không có cột chi phí nào và không nên
thêm — KS không phải nơi ghi sổ token của Mnemosyne. Chỉ cần nhớ: nếu sau này
ai đó đọc số liệu chi phí phía Mnemosyne, cột đó **không tin được cho các lượt
fail**. Họ đã báo lên phía điều phối của họ, KS không đụng vào.

### Đính chính: điều gì KÍCH HOẠT fallback list-scan của Mnemosyne
Tôi từng nói với Mnemosyne rằng `systemctl --user restart chiron-ks-http` sẽ
kích hoạt nhánh fallback của họ. **Sai.** Service chưa lên thì connection bị
refuse → `KsError::Unreachable` → 502 `knowledge_store_error`, không phải đường
fallback.

Fallback chỉ chạy khi KS **đang sống nhưng chạy code cũ**: Werkzeug trả HTML 404
cho route chưa tồn tại, và HTML 404 đó không phân biệt được với "node không tồn
tại" nếu chỉ nhìn mã trạng thái. Tức là **lệch phiên bản**, không phải downtime.
Ca này có thật vì hai service phát triển song song trong cùng một checkout.
Mnemosyne giữ fallback và cho nó log warning khi chạy — hai đường không tương
đương (đường list-scan không resolve được merge), nên thay thế âm thầm sẽ để lại
khác biệt đó thành một bí ẩn phát hiện sau.

## Mnemosyne KHÔNG có systemd unit

`card_sync` phụ thuộc Mnemosyne sống ở `127.0.0.1:8081`, nhưng Mnemosyne chạy
thủ công bằng `cargo run -p backend` và không có unit systemd nào. Timer
`chiron-ks-card-sync.timer` chạy hằng giờ bất kể — khi Mnemosyne chết, job ghi
`pending` và KHÔNG đốt lượt retry, nên tick sau tự bù. Không mất dữ liệu, chỉ
trễ. Không cần sửa gì phía KS.

Mnemosyne cũng chưa có auth layer (simplification có chủ ý phía họ), nên
`KS_MNEMOSYNE_TOKEN` để trống được. KS vẫn gửi header `Authorization` NẾU biến
có giá trị, để sẵn sàng cho lúc họ thêm auth.

### ⚠️ Endpoint `card_sync` phụ thuộc CHƯA có trên origin
Tính tới lúc viết dòng này, Mnemosyne có **11 commit chưa push**, và
`POST /cards/from_node` nằm trong số đó. Nghĩa là toàn bộ `card_sync` đang phụ
thuộc vào một endpoint **chỉ tồn tại ở local checkout của máy này** — không có
trên origin, không khôi phục được nếu máy hỏng.

Đừng ghi ở đâu rằng phía Mnemosyne "đã an toàn trên origin". Nó chưa.

Hệ quả cụ thể cần nêu, và chỉ nêu: **`card_sync` đang được xác nhận là đúng dựa
trên code chỉ tồn tại ở local phía Mnemosyne.** Nếu máy đó gặp sự cố, milestone
vừa giao KHÔNG verify lại được.

KS không push repo của họ, và **không nhắc họ push nữa** — kể cả với lý do chính
đáng. Đây là thông tin để người dùng phía Mnemosyne tự quyết, không phải yêu cầu.
Push lên origin là hành động hướng ra ngoài, quyền thuộc về người dùng của họ, và
một lần cho phép trước đó không phải cho phép vĩnh viễn. KS đã một lần thúc và
nhận sai; Agent A cũng từng lặp nhẹ lỗi tương tự. Không lặp lại.

Đối chiếu: chính brief dựng lại KS tồn tại vì lần trước code không được push
trước khi cài lại máy — mất sạch. Đây là cùng một hình dạng rủi ro, ở module
khác.

## LỆCH BRIEF CÓ CHỦ ĐÍCH: systemd ở mức USER, không phải system

Brief §10 và bản build lần trước dùng system-level (`/etc/systemd/system/`,
`sudo systemctl ...`). Lần này chốt **user-level** (`~/.config/systemd/user/`).

Hệ quả — mọi lệnh vận hành trong brief và tài liệu cũ đều phải thêm `--user`:

| Tài liệu cũ | Đúng cho bản này |
|---|---|
| `sudo systemctl status chiron-ks-http` | `systemctl --user status chiron-ks-http` |
| `sudo systemctl restart chiron-ks-http` | `systemctl --user restart chiron-ks-http` |
| `sudo journalctl -u chiron-ks-http` | `journalctl --user -u chiron-ks-http` |
| `sudo systemctl show ... -p MainPID` | `systemctl --user show ... -p MainPID` |

Đánh đổi đã cân nhắc:
- **Được:** không cần sudo mỗi lần sửa unit; unit chạy đúng dưới user `zinnn`,
  cùng user sở hữu PGDATA `~/.local/share/chiron-ks-postgres`, nên không phải
  khai báo `User=`/`Group=` hay lo quyền thư mục.
- **Mất:** cần `sudo loginctl enable-linger zinnn` (một lần) thì service mới
  sống qua logout và tự lên lúc boot. **Chưa chạy** — `Linger=no`. Không có
  linger thì KS chết khi logout và Mnemosyne mất endpoint.

## Bẫy vận hành — đừng lặp lại
1. **`fish` không có `export`.** Truyền biến bằng `env VAR=value command`.
2. **Trước khi kill tiến trình `ks serve`:** xác nhận
   `systemctl --user show chiron-ks-http.service -p MainPID`. Đã có lần SIGKILL
   nhầm service production hai lần vì phán đoán "orphan" chỉ dựa vào `lsof`/port.
3. **Sửa code xong, systemd vẫn chạy code cũ** cho tới khi `systemctl restart`.
4. Postgres mặc định `unix_socket_directories = '/run/postgresql'` → user thường
   gặp `FATAL: could not create lock file`. Sửa thành PGDATA.
5. Port là **5432** (mặc định `initdb`). Nếu thấy `55432` ở đâu đó, đó là cluster
   của lần build trước — không dùng lại.

## Phát hiện mới trong lần build này
**`KS_HTTP_TOKEN` bắt buộc phải là ASCII.** Đo bằng curl thật: token chứa tiếng
Việt làm MỌI request 403 vĩnh viễn. Nguyên nhân: WSGI giải mã giá trị header HTTP
bằng latin-1, nên byte UTF-8 của token tới tay ứng dụng dưới dạng mojibake và
không bao giờ khớp. Đây là lỗi CẤU HÌNH, không phải lỗi client — nên
`validate_token_config()` chạy lúc `ks serve` khởi động và chết ngay nếu token
non-ASCII, thay vì im lặng hỏng.

Khác với bug `hmac.compare_digest` (§9 của brief): bug đó là header CLIENT gửi lên
có ký tự non-ASCII làm crash 500; đã chặn bằng cách so trên bytes. Hai lỗi độc lập,
đều có test riêng.
