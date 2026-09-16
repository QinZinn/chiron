"""list / accept / discard khái niệm đã rút."""

from __future__ import annotations

import json
import uuid

import pytest

from ks.confirm import AlreadyDecided, ExtractedConceptNotFound, accept, discard, list_extracted
from ks.transcripts import extract_concepts

from tests.test_edges import FakeProvider

TWO = json.dumps([
    {"title": "Định luật Newton 2", "subject": "Vật lý", "summary": "F = m*a."},
    {"title": "Quang hợp", "subject": "Sinh học", "summary": "Cây dùng ánh sáng."},
])


@pytest.fixture
def extracted(conn):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ks.transcripts (session_ref, content) VALUES (%s, '[]') RETURNING id",
            (f"s-{uuid.uuid4()}",),
        )
        tid = cur.fetchone()[0]
    concepts = extract_concepts(conn, tid, FakeProvider(TWO)).concepts
    return conn, concepts


def test_list_chi_tra_pending_review(extracted):
    conn, concepts = extracted
    assert len(list_extracted(conn)) == 2
    discard(conn, concepts[0].id)
    assert [c.id for c in list_extracted(conn)] == [concepts[1].id]


def test_accept_tao_node_qua_ingest_concepts(extracted):
    conn, concepts = extracted
    item = accept(conn, concepts[0].id)
    assert item.created is True
    with conn.cursor() as cur:
        cur.execute("SELECT title FROM ks.nodes WHERE id = %s", (item.node_id,))
        assert cur.fetchone()[0] == "Định luật Newton 2"


def test_accept_di_qua_dung_luat_do_trung(extracted):
    """Không có cửa sau: accept dùng chung ingest_concepts nên cũng bị gộp."""
    conn, concepts = extracted
    first = accept(conn, concepts[0].id)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ks.extracted_concepts (transcript_id, title, subject, summary, source_module)"
            " VALUES (%s, 'Định luật Newton 2', 'Vật lý', 'Nhắc lại.', 'mnemosyne') RETURNING id",
            (concepts[0].transcript_id,),
        )
        again_id = cur.fetchone()[0]
    second = accept(conn, again_id)
    assert second.created is False
    assert second.node_id == first.node_id


def test_accept_ghi_lai_node_id_va_doi_status(extracted):
    conn, concepts = extracted
    item = accept(conn, concepts[0].id)
    with conn.cursor() as cur:
        cur.execute("SELECT status, node_id FROM ks.extracted_concepts WHERE id = %s",
                    (concepts[0].id,))
        assert cur.fetchone() == ("accepted", item.node_id)


def test_accept_cung_de_lai_ingest_log(extracted):
    """Instrumentation không có ngoại lệ cho đường accept."""
    conn, concepts = extracted
    accept(conn, concepts[0].id)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ks.ingest_log")
        assert cur.fetchone()[0] == 1


def test_discard_giu_row_KHONG_xoa(extracted):
    conn, concepts = extracted
    discard(conn, concepts[0].id)
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM ks.extracted_concepts WHERE id = %s", (concepts[0].id,))
        assert cur.fetchone()[0] == "discarded"


def test_discard_khong_tao_node(extracted):
    conn, concepts = extracted
    discard(conn, concepts[0].id)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ks.nodes")
        assert cur.fetchone()[0] == 0


def test_accept_lai_thu_da_quyet_thi_raise(extracted):
    conn, concepts = extracted
    accept(conn, concepts[0].id)
    with pytest.raises(AlreadyDecided):
        accept(conn, concepts[0].id)


def test_discard_lai_thu_da_quyet_thi_raise(extracted):
    conn, concepts = extracted
    discard(conn, concepts[0].id)
    with pytest.raises(AlreadyDecided):
        discard(conn, concepts[0].id)


def test_id_khong_ton_tai_thi_raise(conn):
    with pytest.raises(ExtractedConceptNotFound):
        accept(conn, uuid.uuid4())


def test_list_loc_theo_status(extracted):
    conn, concepts = extracted
    accept(conn, concepts[0].id)
    assert [c.id for c in list_extracted(conn, status="accepted")] == [concepts[0].id]
