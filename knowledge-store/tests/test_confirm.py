"""list / accept / discard of extracted concepts."""

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


def test_list_returns_only_pending_review(extracted):
    conn, concepts = extracted
    assert len(list_extracted(conn)) == 2
    discard(conn, concepts[0].id)
    assert [c.id for c in list_extracted(conn)] == [concepts[1].id]


def test_accept_creates_a_node_through_ingest_concepts(extracted):
    conn, concepts = extracted
    item = accept(conn, concepts[0].id)
    assert item.created is True
    with conn.cursor() as cur:
        cur.execute("SELECT title FROM ks.nodes WHERE id = %s", (item.node_id,))
        assert cur.fetchone()[0] == "Định luật Newton 2"


def test_accept_goes_through_the_same_duplicate_rule(extracted):
    """No back door: accept shares ingest_concepts, so it gets merged too."""
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


def test_accept_records_node_id_and_changes_status(extracted):
    conn, concepts = extracted
    item = accept(conn, concepts[0].id)
    with conn.cursor() as cur:
        cur.execute("SELECT status, node_id FROM ks.extracted_concepts WHERE id = %s",
                    (concepts[0].id,))
        assert cur.fetchone() == ("accepted", item.node_id)


def test_accept_also_leaves_an_ingest_log(extracted):
    """Instrumentation has no exception for the accept path."""
    conn, concepts = extracted
    accept(conn, concepts[0].id)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ks.ingest_log")
        assert cur.fetchone()[0] == 1


def test_discard_keeps_the_row_does_NOT_delete(extracted):
    conn, concepts = extracted
    discard(conn, concepts[0].id)
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM ks.extracted_concepts WHERE id = %s", (concepts[0].id,))
        assert cur.fetchone()[0] == "discarded"


def test_discard_creates_no_node(extracted):
    conn, concepts = extracted
    discard(conn, concepts[0].id)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ks.nodes")
        assert cur.fetchone()[0] == 0


def test_accepting_an_already_decided_concept_raises(extracted):
    conn, concepts = extracted
    accept(conn, concepts[0].id)
    with pytest.raises(AlreadyDecided):
        accept(conn, concepts[0].id)


def test_discarding_an_already_decided_concept_raises(extracted):
    conn, concepts = extracted
    discard(conn, concepts[0].id)
    with pytest.raises(AlreadyDecided):
        discard(conn, concepts[0].id)


def test_unknown_id_raises(conn):
    with pytest.raises(ExtractedConceptNotFound):
        accept(conn, uuid.uuid4())


def test_list_filters_by_status(extracted):
    conn, concepts = extracted
    accept(conn, concepts[0].id)
    assert [c.id for c in list_extracted(conn, status="accepted")] == [concepts[0].id]
