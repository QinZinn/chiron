# Chiron OCR

PaddleOCR on CPU behind a small HTTP API, used by the **Note scan** screen: a
photo of a notebook page or a PDF becomes text the learner corrects, from which
concepts are extracted into the Knowledge Store.

Internal service: no host port, and its only caller is the Knowledge Store. It
stores nothing — uploads live in a temporary directory for the length of the
request.

## API

| Route | Does |
|---|---|
| `GET /health` | `{"status":"ok","ready":bool,"engine":…,"error":…}` — `ready` is `true` only once the models are loaded |
| `POST /ocr` | multipart, one or more `files` fields (images or PDFs) → `{"pages":[…], "page_count", "elapsed_ms"}` |

Each page has `text` (lines in reading order), `lines` (each with a
`confidence`), `mean_confidence` (`null` for a page with no text) and
`low_confidence_count`.

Errors: `400 invalid_document` (not an image/PDF, damaged or password-protected
PDF), `413 too_many_pages` / `too_large`, `503 not_ready` (models loading),
`500 ocr_failed` (a model failed at run time).

## Models

1. **`PP-LCNet_x1_0_doc_ori`** turns pages photographed sideways or upside down
   (90/180/270°) upright.
2. **`PP-OCRv6_medium_det`** finds the text lines. Each line is cut out along
   the detector's quadrilateral and straightened (`chiron_ocr/geometry.py`).
3. **`PP-OCRv6_small_rec`** reads each line.

The recogniser was chosen by measurement, not by name: six PaddleOCR
recognisers were run on the same line crops, and `PP-OCRv6_small_rec` had the
lowest character error rate on both printed English (0.35 %) and real
handwritten English notes (28 %), and was the fastest (1.3 s/page). Numbers and
method: [`NOTES.md`](NOTES.md). Override with `OCR_REC_MODEL`.

All models are downloaded **at build time** and baked into the image, so the
container starts with no network access.

What to expect: printed and typed English reads almost perfectly; handwriting
is a draft — roughly one word in two needs fixing — and text laid out in two
columns (a box beside the notes, a Cornell cue column) gets merged line by line.
That is why low-confidence lines are **flagged, never dropped**, and the learner
always corrects the text before an LLM reads it.

## Environment variables

| Variable | Default | |
|---|---|---|
| `OCR_REC_MODEL` | `PP-OCRv6_small_rec` | Recogniser (any PaddleOCR `TextRecognition` model name) |
| `OCR_DET_MODEL` | `PP-OCRv6_medium_det` | Text-line detector |
| `OCR_DOC_ORIENTATION` | `1` | Fix pages rotated by 90/180/270° |
| `OCR_ENABLE_MKLDNN` | `0` | Paddle's oneDNN; off because Paddle 3.3 fails on PP-OCRv6 with it on CPU |
| `OCR_MAX_PAGES` | `30` | Pages per request |
| `OCR_MAX_UPLOAD_MB` | `40` | Upload size per request |
| `OCR_PDF_DPI` | `200` | PDF render resolution |
| `OCR_MAX_IMAGE_SIDE` | `4000` | Larger images are scaled down first |

## Tests

No model loading needed — line grouping, file handling and crop/rotate geometry
are separate from the engine:

```bash
docker compose run --rm ocr python -m pytest -q
```

Accuracy benchmark (printed pages with known text): [`bench/README.md`](bench/README.md).

## Prebuilt image

Public on GHCR: `ghcr.io/qinzinn/chiron-ocr:<tag>` (code and public models only,
no secrets). `docker compose up` **pulls** it rather than building, so a demo
machine needs neither a PaddlePaddle build nor the dev machine's network setup.

## Building from source and pushing a new tag

After changing anything in `ocr/`:

```bash
cd ocr
docker build --network host -t ghcr.io/qinzinn/chiron-ocr:<tag> .
gh auth token | docker login ghcr.io -u QinZinn --password-stdin   # needs write:packages
docker push ghcr.io/qinzinn/chiron-ocr:<tag>
```

then bump the `image:` tag of the `ocr` service in `docker-compose.yml`.

`--network host`: on the dev machine, large downloads from inside Docker's
bridge network stall indefinitely, and the build downloads PaddlePaddle and the
models.
