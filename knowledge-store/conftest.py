"""Fixture chung: DB test thật (không mock Postgres — trigram là hành vi của Postgres).

Mỗi test chạy trong một transaction và rollback ở cuối, nên test không thấy nhau.
"""

from __future__ import annotations

import os

import psycopg
import pytest

from ks import db

DEFAULT_TEST_URL = "postgresql://postgres@127.0.0.1:5432/chiron_ks_test"


def _test_url() -> str:
    return os.environ.get("KS_TEST_DATABASE_URL", DEFAULT_TEST_URL)


@pytest.fixture(scope="session")
def migrated_url() -> str:
    """Dựng lại schema từ số 0 rồi chạy toàn bộ migration. Một lần mỗi session."""
    url = _test_url()
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS ks CASCADE")
            cur.execute("DROP TABLE IF EXISTS public.ks_schema_migrations")
        conn.commit()
        db.migrate(conn)
    return url


@pytest.fixture
def conn(migrated_url):
    """Connection cho một test. Rollback ở cuối — không để lại rác."""
    with psycopg.connect(migrated_url) as connection:
        yield connection
        connection.rollback()


@pytest.fixture(autouse=True)
def _clean_committed_rows(migrated_url):
    """Một số đường đi tự mở connection và tự commit (save_transcript, các route
    HTTP) — rollback của fixture `conn` không dọn được. Xoá phần đã commit sau
    mỗi test để test không thấy nhau.

    Autouse nên fixture này dựng TRƯỚC `conn`, do đó teardown chạy SAU khi `conn`
    đã rollback — không giành lock với transaction của test.
    """
    yield
    with psycopg.connect(migrated_url) as cleanup:
        with cleanup.cursor() as cur:
            cur.execute(
                "TRUNCATE ks.edge_decision_log, ks.edge_suggestion_run, ks.ingest_log,"
                " ks.edges, ks.extracted_concepts, ks.transcripts, ks.nodes CASCADE"
            )
        cleanup.commit()
