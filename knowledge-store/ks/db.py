"""Kết nối Postgres và chạy migration. Không giấu lỗi — trừ nơi có ghi rõ."""

from __future__ import annotations

import pathlib

import psycopg

from ks import settings

MIGRATIONS_DIR = pathlib.Path(__file__).parent / "migrations"

# Bảng theo dõi migration nằm ở public, vì schema ks do 0001 tạo ra.
_TRACKING_TABLE = """
CREATE TABLE IF NOT EXISTS public.ks_schema_migrations (
  filename    TEXT PRIMARY KEY,
  applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def connect(url: str | None = None) -> psycopg.Connection:
    """Mở connection. url=None → đọc KS_DATABASE_URL."""
    return psycopg.connect(url or settings.database_url())


def migration_files() -> list[pathlib.Path]:
    """Danh sách file .sql theo thứ tự tên (0001, 0002, ...)."""
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def applied_migrations(conn: psycopg.Connection) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(_TRACKING_TABLE)
        cur.execute("SELECT filename FROM public.ks_schema_migrations")
        return {row[0] for row in cur.fetchall()}


def migrate(conn: psycopg.Connection) -> list[str]:
    """Chạy các migration chưa áp dụng. Trả danh sách file vừa chạy.

    Mỗi migration một transaction: file lỗi → chỉ file đó rollback, các file
    trước vẫn giữ.
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
