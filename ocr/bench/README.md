# Đo độ chính xác OCR

Kết quả và kết luận: [`../NOTES.md`](../NOTES.md).

```bash
cd ocr/bench
python3 gen.py        # dựng trang từ ground_truth trong gen.py (cần font Noto/Roboto/Liberation/DejaVu)
docker run --rm --network none -w /app -e PYTHONPATH=/app -v "$PWD":/data chiron-ocr:latest python /data/run_ocr.py
python3 cer.py ground_truth.json ocr_out.json
```

`gen.py` dùng seed cố định. Ảnh sinh ra (`*.png`, `*.jpg`) và `ocr_out.json` không commit.
