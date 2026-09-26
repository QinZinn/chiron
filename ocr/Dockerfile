# Chiron OCR — PaddleOCR text detection and English recognition, on CPU, behind
# a small HTTP API.
#
# Python 3.13: the newest version PaddlePaddle 3.3 publishes wheels for.

FROM python:3.13-slim

# libgomp1: Paddle's CPU kernels use OpenMP. libgl1/libglib2.0-0: OpenCV, which
# PaddleX pulls in, loads them at import even when only reading images.
# Timeout + retries: a stalled mirror connection hung an earlier build for
# 10+ minutes with no error; this fails over instead of waiting forever.
RUN apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 update \
 && apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 install -y --no-install-recommends \
      libgomp1 libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --timeout 60 --retries 5 -r requirements.txt pytest
COPY chiron_ocr ./chiron_ocr
COPY tests ./tests

ENV OCR_HOST=0.0.0.0 \
    OCR_PORT=8866 \
    PYTHONUNBUFFERED=1

# Models are fetched at BUILD time and baked into the image, not downloaded on
# first start. Two reasons: the container then starts with no network at all,
# and on this machine large downloads from inside Docker's bridge network
# stall indefinitely (measured: 9.7 MB took 2.5 s over host networking and
# never finished over the bridge) — build with `network: host`, as
# docker-compose.yml does, and the runtime never has to download anything.
RUN python -c "from chiron_ocr import engine; engine.load(); assert engine.status()['ready'], engine.status()"

# Links the image published to ghcr.io/qinzinn/chiron-ocr to this repository.
LABEL org.opencontainers.image.source="https://github.com/QinZinn/chiron" \
      org.opencontainers.image.description="Chiron OCR: PaddleOCR text detection + English recognition (CPU)" \
      org.opencontainers.image.licenses="MIT"

EXPOSE 8866
CMD ["python", "-m", "chiron_ocr.app"]
