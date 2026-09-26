"""Constants, and configuration read from the environment. No hard-coded secrets."""

from __future__ import annotations

import os

# ---------------------------------------------------------------- database

DATABASE_URL_ENV = "KS_DATABASE_URL"
TEST_DATABASE_URL_ENV = "KS_TEST_DATABASE_URL"


def database_url() -> str:
    """Postgres URL. Missing variable → raise; no guessed default."""
    url = os.environ.get(DATABASE_URL_ENV, "")
    if not url:
        raise RuntimeError(f"Missing environment variable {DATABASE_URL_ENV}")
    return url


# ---------------------------------------------------------------- duplicate detection

# Merge threshold: similarity >= threshold → treated as the same concept.
# 0.6 is a settled decision. Read NOTES.md §limits before changing it.
DUPLICATE_THRESHOLD = 0.6

# Floor for recording a candidate: below the merge threshold but still worth logging (measures false negatives).
CANDIDATE_FLOOR = 0.3

# Maximum candidates returned per draft.
CANDIDATE_LIMIT = 5

# ---------------------------------------------------------------- edges

# Number of neighbouring nodes included in the edge-suggestion prompt.
EDGE_SUGGESTION_TOP_K = 8

# Token budget for LLM output.
#
# MEASURED: deepseek-v4-flash is a REASONING model — reasoning tokens COUNT
# toward max_tokens. One edge-suggestion call with 2 candidates used 222
# completion tokens, 158 of them reasoning (71%). The amount of reasoning varies
# per run, so max_tokens=1000 once left ~15 tokens for the JSON and cut it off.
#
# Be generous: cost only accrues for tokens ACTUALLY generated, while a cut-off
# ruins the whole batch. Do not lower these two numbers to match visible output length.

# Edge suggestion: MEASURED 2026-09-26 on 15 real nodes (Chemistry + Biology notes):
# with 4000, 8 of 15 calls were cut off (reasoning 3630–4000, no tokens left for
# the JSON) — every one a Biology node. The user chose 20000.
EDGE_SUGGESTION_MAX_TOKENS = 20000

# Concept extraction needs far more. MEASURED 2026-09-25, deepseek-v4-flash, 3
# corrected handwritten pages (3050 characters): with 4000, BOTH runs were cut off
# at reasoning=4000, no tokens left for the JSON. With 16000: the 3 pages used 8270
# and 8577 completion tokens (reasoning 6871 / 6880); single pages used 944, 2382
# and 8723 — one 1210-character page used 7824 reasoning tokens. Splitting by page
# therefore does NOT solve it; 16000 is about twice the highest measured value.
EXTRACTION_MAX_TOKENS = 16000

# ---------------------------------------------------------------- http

HTTP_TOKEN_ENV = "KS_HTTP_TOKEN"
HTTP_PORT_ENV = "KS_HTTP_PORT"
DEFAULT_HTTP_PORT = 8080

# Bind host. Loopback only by default: KS has no TLS and treats itself as an
# internal service. In Docker it must be 0.0.0.0, because a container's 127.0.0.1
# is its own loopback — Mnemosyne and the frontend proxy in other containers cannot reach it.
HTTP_HOST_ENV = "KS_HTTP_HOST"
DEFAULT_HTTP_HOST = "127.0.0.1"

# Upper bound on GET /nodes' limit. Over it → 400, NOT silently truncated.
MAX_NODE_LIMIT = 500
# Upper bound for GET /edges, same rule: over it → 400.
MAX_EDGE_LIMIT = 2000
DEFAULT_NODE_LIMIT = 50

# ---------------------------------------------------------------- OCR (scanned notes)

# OCR service (Chiron/ocr, PaddleOCR). Empty → the /notes routes return 503 "not
# configured"; the rest of KS keeps working.
OCR_URL_ENV = "KS_OCR_URL"

# OCR runs on CPU: a few seconds per page on the dev machine; 30 pages can take
# over 2 minutes. Timing out here throws the whole scan away, so be generous.
OCR_TIMEOUT_SECONDS = 600

# Upload size cap through KS, matching the OCR service's OCR_MAX_UPLOAD_MB.
MAX_NOTE_UPLOAD_BYTES = 40 * 1024 * 1024

MAX_NOTE_LIMIT = 200

# ---------------------------------------------------------------- extraction

# Maximum retries for one transcript.
MAX_EXTRACTION_ATTEMPTS = 5


# ---------------------------------------------------------------- card_sync

MNEMOSYNE_URL_ENV = "KS_MNEMOSYNE_URL"
MNEMOSYNE_TOKEN_ENV = "KS_MNEMOSYNE_TOKEN"

# Timeout for POST /cards/from_node.
#
# MEASURED: generating a card for a node with a ~1900-character summary took 11
# seconds; a ~2900-character node took over 30 seconds. DeepSeek is a reasoning
# model, so response time scales with the amount of reasoning, which varies a lot.
#
# A timeout that is TOO SHORT is worse than slow: KS gives up before Mnemosyne
# answers, records a CardClientError, and LOSES the real classification (truncated /
# provider_error / knowledge_store_error). Exactly once this hid the very case we needed to see.
MNEMOSYNE_TIMEOUT_ENV = "KS_MNEMOSYNE_TIMEOUT"
DEFAULT_MNEMOSYNE_TIMEOUT = 180

# A fixed study set for this phase. NOT mapped by `subject`: subject is free TEXT
# and there is no evidence yet about its real distribution to design a sensible
# mapping. That decision waits for data rather than being guessed now.
#
# Mnemosyne takes study_set_id as a **UUID**, not a name — the brief's "KS review"
# is the set's NAME; what goes in the request is its id. The id comes from an
# environment variable: the set must be created ONCE outside the job. Mnemosyne has
# no unique constraint on set names, so letting the job create it on every run
# would produce a pile of sets with the same name.
CARD_SYNC_STUDY_SET_NAME = "KS review"
CARD_SYNC_STUDY_SET_ID_ENV = "KS_CARD_SYNC_STUDY_SET_ID"

# Retry limit for reason="provider_error". Applies only to provider_error —
# "truncated" is never retried, and knowledge_store_error/503 are transient
# infrastructure errors, so they retry without limit (the timer tries again next tick).
MAX_CARD_SYNC_ATTEMPTS = 2

# Nodes per job run. Kept small so one run does not hang for long.
CARD_SYNC_BATCH_LIMIT = 100
