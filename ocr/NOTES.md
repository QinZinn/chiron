# OCR — accuracy measurements

Confidence (`mean_confidence`) is the number the model reports about itself; it is
**not accuracy**. This file records measured accuracy against ground-truth text
typed by a person. How to re-run: [`bench/README.md`](bench/README.md).

The first three sections were measured while Chiron still targeted Vietnamese
notes with the VietOCR recogniser; they are kept as the record that led to the
English-only recogniser chosen in the fourth.

## 2026-09-25 — Printed Vietnamese, images rendered from fonts

**Data.** 6 pages, 2119 characters, 415 of them with diacritics. The ground truth
was in `bench/ground_truth.json` and the images were rendered by `bench/gen.py`
(both have since been replaced with English). Textbook-style content (Biology,
Physics, History, Literature), with formulas (`6CO2 + 6H2O → …`, `Φ = B·S·cosα`,
`e = −ΔΦ/Δt`).

- 4 clean pages: Noto Serif 34 px, Roboto 30 px, Liberation Serif 32 px,
  DejaVu Sans 26 px.
- 2 fake phone photos: yellowed paper, uneven lighting, 1.8° tilt, 1.1 px
  Gaussian blur, JPEG quality 70.

Engine: `PP-OCRv6_medium_det + vietocr/vgg_transformer` (the `chiron-ocr` image
built 2026-09-17).

**Metrics.** CER is the character-level Levenshtein distance divided by the length
of the ground truth, after collapsing all whitespace to one space. A "diacritic
error" is a substitution that keeps the base letter and only gets the mark wrong
(e.g. `ề`→`ể`).

| Page | Chars | Errors | CER | Diacritic errors | Confidence |
|---|---:|---:|---:|---:|---:|
| sinh-serif | 393 | 7 | 1.78 % | 0 | 0.916 |
| ly-sans | 359 | 10 | 2.79 % | 0 | 0.922 |
| su-times | 305 | 0 | 0.00 % | 0 | 0.923 |
| van-dejavu | 310 | 4 | 1.29 % | 3 | 0.917 |
| sinh-serif-photo | 393 | 8 | 2.04 % | 0 | 0.920 |
| ly-sans-photo | 359 | 16 | 4.46 % | 0 | 0.922 |
| **Total** | **2119** | **45** | **2.12 %** | **3 / 415 marked chars** | |

The 45 errors by kind of original character:

- **26 symbol/formula errors (58 %).** `+`→`%`, `→`→`ở`, `=`→`-`, `·`→`-`,
  `Φ`→`Đ`/`D`/`0`/`P`, `Δ`→`A`, `α`→`a`, the minus sign `−` dropped.
- **9 punctuation errors.** Mostly a lost full stop at the end of a line, more
  often on the fake photos.
- **10 letter/digit errors (0.64 % CER over 1570 letters).** 6 were `O`→`0` in
  chemical formulas (`CO2`, `O2`), 1 was `V`→`v`, and 3 were tone-mark errors on
  the same DejaVu page: `chiều`→`chiểu`, `cõi`→`cối`, `bể`→`bế`.

Time: 4.3–5.6 seconds per page on CPU.

**Conclusions.**

1. Printed Vietnamese read very well: 0.64 % errors on letters, and 3/415 marked
   characters wrong. The fake photos raised CER only slightly (1.78 % → 2.04 %),
   except on the page with formulas.
2. **Formulas and symbols were the main weakness.** VietOCR has no `Φ`, `Δ`, `→`,
   `·` in its character set, so it always misread them. For Physics/Chemistry
   notes the learner had to fix formulas by hand at the text-review step.
3. **Confidence does not reflect errors.** A page with no errors (0.923) and a page
   with 4.46 % errors (0.922) had almost the same confidence. No line fell below
   the 0.80 threshold, so "Lines to check" in the UI missed all 45 errors. It
   should not be used to tell the learner the text is fine.
4. These images were rendered from fonts and are cleaner than real notebook photos.
   The numbers are an **upper bound** for printed text and **say nothing about
   handwriting**.

## 2026-09-25 — Real handwriting (the learner's notebooks)

**Data.** 3 phone photos of real notebooks, 3046 characters, 604 with diacritics:

- 1 Chemistry page (fertilisers), ruled notebook, a small 525×1280 image with the
  left edge cut off.
- 2 Biology pages (metabolism) in **Cornell** style: a cue column on the left, a
  notes column on the right, a summary at the bottom of page 2. 846×1280 and
  1440×2228 images.

The ground truth was typed by Claude from the photos, following the handwriting
exactly (keeping abbreviations such as `nl`, `sv`, `qtrình`), and **has not been
confirmed by the learner**. The images and ground truth are **not** committed
because they are personal notebooks. OCR ran through the application's own path
(`POST /notes` → OCR service), image `ghcr.io/qinzinn/chiron-ocr:0.1.0`.

Cue-column lines were placed next to the note line at the same height, in the
order OCR reads them, so CER does not penalise ordering. Both sides were
normalised for old/new tone-mark placement (`hoá`/`hóa`, `luỹ`/`lũy`) before comparing.

**Measurements.**

| Page | Chars | CER | Marked chars wrong | Words exactly right | Words wrong only in marks | Confidence |
|---|---:|---:|---:|---:|---:|---:|
| Chemistry · fertilisers | 914 | 26.3 % | 59 / 162 | 60.8 % | 23 | 0.799 |
| Biology · page 1 (Cornell) | 922 | 15.2 % | 53 / 177 | 69.2 % | 27 | 0.822 |
| Biology · page 2 (Cornell + summary) | 1210 | 23.2 % | 110 / 265 | 52.7 % | 54 | 0.811 |
| **Total** | **3046** | **21.7 %** | **222 / 604** | **60.2 %** | **104 (16.0 %)** | |

- Letters/digits only: 19.4 % CER. Case-insensitive: 20.8 %.
- "Words exactly right" compares words as a set, ignoring order and case. 23.8 %
  of words were misspelled or missing entirely.
- Typical errors:
  - Tone marks: `lượng`→`lường`, `dưỡng`→`dương`/`đường`, `tự`→`từ`.
  - Confused letters: `Chiếm`→`Thiêm`, `lũy`→`lấy`, `hữu cơ`→`hiểu ả`.
  - Symbols: `&`→`b`/`k`/`8`, `⇄`→`2`, `+`→`t`/`4`.
  - Subscripts: `H2S`, `NO2-` and units such as `mg/kg` came out as garbage.
  - Lines cut off at the right edge of the Chemistry photo lost their text.
- **Cornell:** because lines are grouped by height, a cue-column sentence was glued
  to the start of the note line at the same height (`Duy trì sự sống. Giúp sinh vật
  tồn tại…`, `Tự dưỡng: tự sống, tự hấp Dùng chất vô cơ…`). The text is still
  readable, but the two columns are interleaved; the learner has to separate them
  at the correction step.

**Confidence and "Lines to check".** Confidence did drop compared with print
(0.80–0.82 vs 0.92), but correlated only moderately with a line's errors
(r = −0.46). 29/91 lines were flagged (below 0.80), with a median line CER of
27 %; unflagged lines had a median of 18 %. Of the 46 lines with CER > 20 %,
**only 21 were flagged**.

**Conclusions.**

1. For handwriting, OCR is only a **draft**: about 4 in 10 words need fixing, and
   1/3 of marked characters are wrong. Correcting the text before extracting
   concepts is mandatory, not optional.
2. "Lines to check" misses more than half of the badly wrong lines. The UI must
   not imply that unflagged lines are correct.
3. Cornell layouts need column separation before lines are grouped; today the two
   columns are interleaved. Not fixed yet.
4. The low-resolution image (the Chemistry page, 525 px wide) had the highest CER.
   Shooting closer, or sending the original photo instead of one compressed by a
   chat app, may help; not measured.

## 2026-09-26 — Handwritten English notebook, ordinary layout (not Cornell)

**Data.** 19 notebook photos from a study-skills course (English, with a few
Vietnamese words), 960×1280, about 110–150 KB each (probably compressed by a chat
app). Measured on 5 pages picked by a fixed rule **before** looking at any output:
pages 1, 5, 9, 13, 17 in file-name order. 3123 characters. Ground truth typed by
Claude, not confirmed by the learner; not committed.

**Measurements** (case-insensitive). Same line detector and same crops; only the
recogniser changes:

| Recogniser | CER | Letters-only CER | Words exactly right | Confidence |
|---|---:|---:|---:|---:|
| VietOCR vgg_transformer (in use at the time) | 42.0 % | 36.7 % | 30.1 % | 0.71–0.79 |
| PaddleOCR PP-OCRv6_medium_rec | **32.5 %** | **29.0 %** | **51.1 %** | 0.79–0.91 |

Per page, VietOCR → Paddle: 27.4 → 16.0 %; 34.5 → 24.9 %; 53.1 → 34.4 %;
53.1 → 50.7 %; 33.3 → 30.8 %.

**Observations.**

- VietOCR invented Vietnamese when reading English handwriting:
  `L: "I don't believe it"` → "Nên có thứ tropersitional", another line →
  "Thời tổ chị yên giái thị thị".
- PaddleOCR has no vowels with tone marks: "quý ông" came out with 2/2 marked
  characters wrong (consistent with the 25/09 finding on print).
- Page 13 was bad with both recognisers (about 51 %). The fault is the layout: a
  text box on the right was joined onto the line to its left (as with Cornell
  columns), plus the exponent `1.10^999` and crossed-out words.
- Compared with the Vietnamese Biology notes (21.7 % CER), this notebook was twice
  as bad with the recogniser in use. The main cause is the language, not the
  Cornell layout.

**Conclusion.** No recogniser was good for both languages: VietOCR for Vietnamese,
Paddle for English. Choosing the recogniser by the notes' language was left open —
and then settled by the English-only switch below.

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

## Not measured yet

- Other people's handwriting, and handwriting at full (uncompressed) resolution.
- The effect of separating Cornell columns (not implemented yet).
