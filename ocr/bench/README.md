# OCR accuracy benchmark

Results and conclusions: [`../NOTES.md`](../NOTES.md).

```bash
cd ocr/bench
python3 gen.py        # renders pages from the ground truth in gen.py (needs Noto/Roboto/Liberation/DejaVu fonts)
docker run --rm --network none -w /app -e PYTHONPATH=/app -v "$PWD":/data ghcr.io/qinzinn/chiron-ocr:0.2.0 python /data/run_ocr.py
python3 cer.py --lower ground_truth.json ocr_out.json
```

`gen.py` uses a fixed seed. Generated images (`*.png`, `*.jpg`) and
`ocr_out.json` are not committed.

`cer.py` reports, per page: character error rate (Levenshtein distance over the
reference length, whitespace collapsed), errors by kind (letters/digits,
punctuation, symbols) and an order-free word accuracy — words matched as a
multiset, so a column read in a different order is not counted as wrong.
