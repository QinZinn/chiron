# Knowledge Store

A "second brain" of the concepts a learner has studied, part of the Chiron
ecosystem. Python + Postgres. Its only consumer today is **Mnemosyne** (the
Socratic coach, Rust/Actix) — a different runtime, so they must talk over HTTP.

- Horae does **NOT** write here. LexiFlash is deliberately postponed.
- A node is one **concept** ("Newton's second law"), not one study session.
- Read [NOTES.md](NOTES.md) before changing any design decision.

## Setup

```bash
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp .env.example .env    # then fill in real values
```

`KS_HTTP_TOKEN` **must be ASCII** — see NOTES.md.

## Running

`fish` has no `export`. Pass variables with `env VAR=value command` — this works
in every shell and mirrors exactly how systemd runs commands.

```bash
env KS_DATABASE_URL=postgresql://postgres@127.0.0.1:5432/chiron_ks .venv/bin/python -m ks.cli migrate
```

## CLI

| Command | What it does |
|---|---|
| `migrate` | Apply pending migrations |
| `create-node` | Create a concept (through duplicate detection) |
| `suggest-edges --node-id` | Top-K candidates → LLM → `pending` edges |
| `list-pending` / `approve` / `reject` / `edit` | Review edges |
| `add-edge` | Add an edge by hand (goes straight to `approved`) |
| `neighbors --node-id` | Neighbours through approved edges |
| `stats` | Instrumentation numbers |
| `save-transcript --session-ref --file` | Store a raw transcript (no LLM) |
| `extract` | Extract concepts from pending transcripts |
| `list-extracted` / `accept` / `discard` | Confirm extracted concepts |
| `split <concept_id>` | Undo a wrong merge: give an accepted concept its own node |
| `card-sync` | Push reviewed nodes to Mnemosyne as cards |
| `serve` | Run the HTTP server (blocks forever) |

## HTTP API

Auth: `Authorization: Bearer <KS_HTTP_TOKEN>`. `/health` needs no auth.

| Route | Notes |
|---|---|
| `GET /health` | Pure liveness, does not touch the DB |
| `POST /transcripts` | `{session_ref, content}`. Truly idempotent on `session_ref`. Does not trigger extraction. `ok:false` + HTTP 200 = KS alive, DB down |
| `POST /ingest` | `{drafts:[...]}`. All-or-nothing. `psycopg.Error` → 503. Idempotency is a **fuzzy similarity match**, not an identity key — retrying is only safe with the `title` kept verbatim |
| `GET /nodes` | `?subject=&source_module=&limit=` (default 50, **cap 500**; over the cap → 400, never silently truncated). No edges |
| `GET /nodes/{id}` | One node. `400` if the id is not a UUID, `404` if there is none. A merged node → the **target node with 200** (one step), so the returned `id` may differ from the one requested |
| `GET /edges` | **Approved** edges for the concept map; `?node_id=`, `?limit=` (at most 2000). Both ends resolved through merges one step, as in `GET /nodes`; edges that become loops after resolving are dropped, duplicates collapse into one. `symmetric` = an undirected relation |
| `POST /notes` | multipart `files` (images/PDF) + optional `title`. Calls the OCR service and stores a `draft` note. Does **not** extract concepts yet |
| `GET /notes`, `GET /notes/{id}` | List / detail (with the per-page OCR result and the extracted concepts) |
| `PATCH /notes/{id}` | `{title, text}` — the learner corrects the OCR text |
| `POST /notes/{id}/extract` | Store the corrected text as a `kind:"note"` transcript, then extract concepts → `pending_review` |
| `GET /extracted` | The review queue (`?status=pending_review\|accepted\|discarded`), including concepts from study sessions |
| `PATCH /extracted/{id}` | Edit `title/subject/summary` before deciding. `409` if already decided |
| `POST /extracted/{id}/accept` \| `/discard` | Accept goes through `ingest_concepts` (duplicate detection); `created:false` = merged into an existing node |

## Scanned notes

Image/PDF → OCR service (`Chiron/ocr`, PaddleOCR) → `draft` note → the learner
corrects the text → concepts are extracted → each concept is reviewed. A note has
**no** route of its own into `ks.nodes`: its text is stored as a `kind:"note"`
transcript and goes through the same `extract_concepts` + `pending_review` as a
study-session transcript, with its own prompt and `source_module = note_scan`.
Original images are not stored.

Needs `KS_OCR_URL` (empty → `/notes` returns `503`; the rest of KS keeps working)
and the `KS_LLM_*` variables for extraction.

## card_sync

Pushes reviewed nodes to Mnemosyne as flashcards. Errors are handled by the
`reason` field of a `502` response, with **no blind retries** — `truncated` fails
at once without retrying, `provider_error` retries a limited number of times,
`knowledge_store_error` retries freely.

The study set is a **UUID**, not a name. Create the set once and put its id in `.env`:

```bash
curl -X POST http://127.0.0.1:8081/study_sets -H 'Content-Type: application/json' -d '{"user_id":"<uuid>","name":"KS review","topic":"..."}'
```

Mnemosyne has no unique constraint on set names, so do not let the job create it.
Read NOTES.md — the `truncated` branch has never been verified on real data.

## Tests

```bash
env KS_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:5432/chiron_ks_test .venv/bin/python -m pytest -q
```

Tests run against a **real** Postgres — trigram matching is Postgres behaviour;
mocking it would make the tests worthless. The database must use the
`en_US.UTF-8` collation: `test_dedup_limits` pins the environment its recorded
similarity numbers were measured in.

## Operations (systemd **user** units)

A deliberate choice that **departs from brief §10** (the brief uses system-level
units). Every operational command in older documents needs `--user` added — see
NOTES.md for the trade-offs.

Run **once** so the services survive logout and start at boot:

```bash
sudo loginctl enable-linger zinnn
```

The unit files are in [deploy/](deploy/). To install:

```bash
cp deploy/*.service deploy/*.timer ~/.config/systemd/user/ && systemctl --user daemon-reload
```

| Unit | Role |
|---|---|
| `chiron-ks-postgres.service` | Its own Postgres cluster, port 5432 |
| `chiron-ks-http.service` | `ks serve`, `Type=simple` (blocks forever, not a cron job) |
| `chiron-ks-extract.timer` | Every 30 minutes. **Needs `DEEPSEEK_API_KEY`** |
| `chiron-ks-stats.timer` | Daily at 23:00 |
| `chiron-ks-card-sync.timer` | Hourly. Needs Mnemosyne running at `KS_MNEMOSYNE_URL` |

Logs: `journalctl --user -u chiron-ks-http -f`.

After changing code, run `systemctl --user restart chiron-ks-http` — without a
restart, curl is still testing the old code.

Before killing any `ks serve` process, confirm it with
`systemctl --user show chiron-ks-http.service -p MainPID`. Do not judge a process
"orphaned" from `lsof`/ports alone.
