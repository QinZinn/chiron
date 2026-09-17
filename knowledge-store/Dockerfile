# syntax=docker/dockerfile:1
# Knowledge Store — Python/Flask.

FROM python:3.14-slim
WORKDIR /app

# Dependencies first, source second, so editing code does not reinstall them.
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir "psycopg[binary]>=3.2" "flask>=3.0"
COPY ks ./ks

# Same reason as Mnemosyne: bind every interface inside the container; the
# compose file decides what, if anything, reaches the host (nothing, for KS).
ENV KS_HTTP_HOST=0.0.0.0 \
    KS_HTTP_PORT=8080 \
    PYTHONUNBUFFERED=1
EXPOSE 8080

# `migrate` is idempotent (ks_schema_migrations) and creates pg_trgm itself.
CMD ["sh", "-c", "python -m ks.cli migrate && exec python -m ks.cli serve"]
