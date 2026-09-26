# Chiron

An AI learning ecosystem. Each module runs on its own and talks to the others
over HTTP; this repository keeps them in one place so they can be developed
together.

| Directory | Module | Stack | Address |
|---|---|---|---|
| [`mnemosyne/`](mnemosyne/) | Studying (Socratic, Feynman, Blurting), FSRS flashcards, quizzes | Rust + Actix + Postgres | `127.0.0.1:8081` |
| [`knowledge-store/`](knowledge-store/) | A "second brain" of the concepts the learner has studied | Python + Flask + Postgres | `127.0.0.1:8080` |
| [`frontend/`](frontend/) | The unified web interface | Vite + React + TypeScript | `localhost:5173` |
| [`ocr/`](ocr/) | Text recognition for "Note scan" | Python + PaddleOCR (CPU) | internal `ocr:8866` |

Chiron connects to no outside service other than the LLM provider. Things to
review (weak cards, items the learner adds) live in Mnemosyne's own to-do list;
Chiron does not schedule anything by itself.

## Running with Docker (recommended)

```bash
cp .env.example .env                  # set POSTGRES_PASSWORD (openssl rand -hex 24)
docker compose up --build -d
docker compose ps                     # wait until all five services are healthy
docker compose exec mnemosyne backend create-user <email>
```

Open http://localhost:4173 and paste the printed token into the Settings screen.

- Each module's secrets stay in that module's `.env` (`mnemosyne/.env`,
  `knowledge-store/.env`, `frontend/.env`). They reach the containers through
  `env_file` at run time and are never baked into an image.
- Databases are created automatically on first start: Postgres creates them,
  then each module runs its own migrations when it starts.
- Only two ports are published, on `127.0.0.1`: Mnemosyne `8081` (the browser
  calls it directly) and the frontend `4173`. The Knowledge Store, OCR and
  Postgres stay on the internal network.
- The `ocr` image (~2.5 GB, PaddlePaddle + models) is **pulled** from
  `ghcr.io/qinzinn/chiron-ocr` rather than built locally — see `ocr/README.md`.
- Data lives in the `chiron_pgdata` volume; `docker compose down` keeps it,
  `docker compose down -v` deletes it.

To open psql on a database: `docker compose exec postgres psql -U postgres -d mnemosyne`.

## Postgres (outside Docker)

One instance on port `5432` with two separate databases: `mnemosyne` and
`chiron_ks`. They share a server so there is only one cluster to run, but they
do **not** share data — the two modules' schemas are never mixed.

```bash
pg_ctl start -D ~/.local/share/chiron-ks-postgres -o "-p 5432" \
  -l ~/.local/share/chiron-ks-postgres/logfile -w
```

## Running without Docker

Each module has its own `.env`, copied from its `.env.example`. Read the README
in each directory first.

```bash
# Knowledge Store
cd knowledge-store && .venv/bin/python -m ks.cli migrate && .venv/bin/python -m ks.cli serve

# Mnemosyne
cd mnemosyne && cargo run -p backend -- migrate && cargo run -p backend

# Frontend
cd frontend && npm install && npm run dev
```

Mnemosyne needs a token: issue one with
`cd mnemosyne && cargo run -p backend -- create-user <email>`, then paste it into
the frontend's Settings screen. The token is shown only once.

To run as services: `mnemosyne/deploy/` and `knowledge-store/deploy/` contain
user-level systemd units.

## Development conventions

Features are built on their own branch; `main` only takes work that has been
checked.

## Retired

Kept for reference; no code or environment variable uses these any more.

- **Horae** (self-study scheduling) was split out of the repository on
  2026-09-20, and since 2026-09-25 Chiron has no link to it at all.
- **Todoist** — weak cards used to create `@ontap` tasks for Horae to schedule.
  Removed on 2026-09-25 (Mnemosyne migration `0010` replaced them with
  `todo_items`). The old design log: `mnemosyne/docs/da-ngung-dung/`.
- **Google Calendar via withone.ai** — a "Study calendar" screen used to read
  Horae's calendar. The whole screen was removed on 2026-09-25.

## License

MIT — see [LICENSE](LICENSE).
