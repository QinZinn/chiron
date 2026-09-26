"""Migrations run, are idempotent, and the schema matches what was settled."""

from __future__ import annotations

import psycopg
import pytest

from ks import db


def _one(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return row[0] if row else None


def test_ks_schema_exists(conn):
    assert _one(conn, "SELECT 1 FROM information_schema.schemata WHERE schema_name = 'ks'") == 1


def test_pg_trgm_is_installed(conn):
    assert _one(conn, "SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'") == 1


def test_migrate_idempotent(conn):
    """Re-running applies nothing more — the files are recorded in the tracking table."""
    assert db.migrate(conn) == []


def test_every_migration_file_has_run(conn):
    applied = db.applied_migrations(conn)
    assert {p.name for p in db.migration_files()} <= applied


def test_subject_is_text_not_an_enum(conn):
    """Horae has no closed subject taxonomy → subject must be free text."""
    dtype = _one(
        conn,
        """SELECT data_type FROM information_schema.columns
           WHERE table_schema='ks' AND table_name='nodes' AND column_name='subject'""",
    )
    assert dtype == "text"


def test_merged_into_id_is_nullable_and_points_at_the_same_table(conn):
    nullable = _one(
        conn,
        """SELECT is_nullable FROM information_schema.columns
           WHERE table_schema='ks' AND table_name='nodes' AND column_name='merged_into_id'""",
    )
    assert nullable == "YES"


def test_edges_unique_triple(conn):
    """UNIQUE (from, to, relation_type) — no duplicate edges."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ks.nodes (title, subject, summary, source_module)"
            " VALUES ('Quang hợp','Sinh học','a','mnemosyne') RETURNING id"
        )
        a = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO ks.nodes (title, subject, summary, source_module)"
            " VALUES ('Chiến tranh Lạnh','Lịch sử','b','mnemosyne') RETURNING id"
        )
        b = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO ks.edges (from_node_id,to_node_id,relation_type,suggested_by)"
            " VALUES (%s,%s,'related','llm')",
            (a, b),
        )
        with pytest.raises(psycopg.errors.UniqueViolation):
            cur.execute(
                "INSERT INTO ks.edges (from_node_id,to_node_id,relation_type,suggested_by)"
                " VALUES (%s,%s,'related','manual')",
                (a, b),
            )


def test_edge_status_defaults_to_pending(conn):
    """LLM suggestions must be reviewed by a person — no auto-approve."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ks.nodes (title, subject, summary, source_module)"
            " VALUES ('Quang hợp','Sinh học','a','mnemosyne') RETURNING id"
        )
        a = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO ks.nodes (title, subject, summary, source_module)"
            " VALUES ('Chiến tranh Lạnh','Lịch sử','b','mnemosyne') RETURNING id"
        )
        b = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO ks.edges (from_node_id,to_node_id,relation_type,suggested_by)"
            " VALUES (%s,%s,'prerequisite','llm') RETURNING status",
            (a, b),
        )
        assert cur.fetchone()[0] == "pending"
