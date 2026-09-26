"""Postgres connection and migrations. Errors are not hidden — except where noted."""

from __future__ import annotations

import pathlib

import psycopg

from ks import settings

MIGRATIONS_DIR = pathlib.Path(__file__).parent / "migrations"

# The migration tracking table lives in public, because the ks schema is created by 0001.
_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS public.ks_schema_migrations (
  filename    TEXT PRIMARY KEY,
  applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def connect(url: str | None = None) -> psycopg.Connection:
    """Open a connection. url=None → read KS_DATABASE_URL."""
    return psycopg.connect(url or settings.database_url())


def migration_files() -> list[pathlib.Path]:
    """The .sql files in name order (0001, 0002, ...)."""
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def applied_migrations(conn: psycopg.Connection) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(_TRACKING_TABLE)
        cur.execute("SELECT filename FROM public.ks_schema_migrations")
        return {row[0] for row in cur.fetchall()}


def migrate(conn: psycopg.Connection) -> list[str]:
    """Run the migrations not yet applied. Returns the files that just ran.

    One transaction per migration: a failing file rolls back only itself; the
    files before it stay applied.
    """
    applied = applied_migrations(conn)
    conn.commit()

    ran: list[str] = []
    for path in migration_files():
        if path.name in applied:
            continue
        sql = path.read_text(encoding="utf-8")
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute(
                "INSERT INTO public.ks_schema_migrations (filename) VALUES (%s)",
                (path.name,),
            )
        conn.commit()
        ran.append(path.name)
    return ran
