"""Shared fixtures: a real test DB (Postgres is not mocked — trigram matching is Postgres behaviour).

Each test runs in a transaction that is rolled back at the end, so tests do not see each other.
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
    """Rebuild the schema from scratch and run every migration. Once per session."""
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
    """A connection for one test. Rolled back at the end — nothing left behind."""
    with psycopg.connect(migrated_url) as connection:
        yield connection
        connection.rollback()


@pytest.fixture(autouse=True)
def _clean_committed_rows(migrated_url):
    """Some paths open their own connection and commit (save_transcript, the HTTP
    routes) — the `conn` fixture's rollback cannot clean those up. Delete what was committed after
    each test so tests do not see each other.

    Autouse, so this fixture is set up BEFORE `conn`, and its teardown therefore runs AFTER `conn`
    has rolled back — no lock contention with the test's transaction.
    """
    yield
    with psycopg.connect(migrated_url) as cleanup:
        with cleanup.cursor() as cur:
            cur.execute(
                "TRUNCATE ks.edge_decision_log, ks.edge_suggestion_run, ks.ingest_log,"
                " ks.edges, ks.extracted_concepts, ks.notes, ks.transcripts, ks.nodes CASCADE"
            )
        cleanup.commit()
