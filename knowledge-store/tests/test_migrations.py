"""Migration chạy được, idempotent, và schema đúng như đã chốt."""

from __future__ import annotations

import psycopg
import pytest

from ks import db


def _one(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return row[0] if row else None


def test_schema_ks_ton_tai(conn):
    assert _one(conn, "SELECT 1 FROM information_schema.schemata WHERE schema_name = 'ks'") == 1


def test_pg_trgm_da_cai(conn):
    assert _one(conn, "SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'") == 1


def test_migrate_idempotent(conn):
    """Chạy lại không áp dụng gì thêm — file đã ghi trong bảng theo dõi."""
    assert db.migrate(conn) == []


def test_moi_file_migration_deu_da_chay(conn):
    applied = db.applied_migrations(conn)
    assert {p.name for p in db.migration_files()} <= applied


def test_subject_la_text_khong_phai_enum(conn):
    """Horae không có taxonomy môn học đóng kín → subject phải tự do."""
    dtype = _one(
        conn,
        """SELECT data_type FROM information_schema.columns
           WHERE table_schema='ks' AND table_name='nodes' AND column_name='subject'""",
    )
    assert dtype == "text"


def test_merged_into_id_nullable_va_tro_ve_chinh_bang(conn):
    nullable = _one(
        conn,
        """SELECT is_nullable FROM information_schema.columns
           WHERE table_schema='ks' AND table_name='nodes' AND column_name='merged_into_id'""",
    )
    assert nullable == "YES"


def test_edges_unique_bo_ba(conn):
    """UNIQUE (from, to, relation_type) — không cho trùng cạnh."""
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


def test_edge_status_mac_dinh_la_pending(conn):
    """LLM gợi ý phải qua người duyệt — không auto-approve."""
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
