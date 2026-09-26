"""ingest_concepts: creating, merging duplicates, recording candidates, and failing loud."""

from __future__ import annotations

from uuid import UUID

import psycopg
import pytest

from ks.ingest import find_candidates, ingest_concepts
from ks.models import ConceptDraft, SourceModule

# Very different names — §12: avoid near-identical pairs as fixtures.
QUANG_HOP = ConceptDraft("Quang hợp", "Sinh học", "Cây dùng ánh sáng tạo chất hữu cơ.", SourceModule.MNEMOSYNE)
CHIEN_TRANH = ConceptDraft("Chiến tranh Lạnh", "Lịch sử", "Đối đầu Mỹ - Liên Xô.", SourceModule.MNEMOSYNE)
PHUONG_TRINH = ConceptDraft("Phương trình bậc hai", "Toán", "ax^2 + bx + c = 0.", SourceModule.MNEMOSYNE)


def _titles(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT title FROM ks.nodes ORDER BY title")
        return [r[0] for r in cur.fetchall()]


def test_first_draft_creates_a_new_node(conn):
    result = ingest_concepts(conn, [QUANG_HOP])
    assert len(result.ingested) == 1
    assert result.ingested[0].created is True


def test_node_id_is_a_uuid_and_the_row_really_exists(conn):
    node_id = ingest_concepts(conn, [QUANG_HOP]).ingested[0].node_id
    assert isinstance(node_id, UUID)
    with conn.cursor() as cur:
        cur.execute("SELECT title, subject, summary, source_module FROM ks.nodes WHERE id = %s", (node_id,))
        assert cur.fetchone() == ("Quang hợp", "Sinh học", "Cây dùng ánh sáng tạo chất hữu cơ.", "mnemosyne")


def test_identical_title_merges_rather_than_creating(conn):
    first = ingest_concepts(conn, [QUANG_HOP]).ingested[0]
    second = ingest_concepts(conn, [QUANG_HOP]).ingested[0]
    assert second.created is False
    assert second.node_id == first.node_id
    assert _titles(conn) == ["Quang hợp"]


def test_duplicates_within_one_batch_still_merge(conn):
    """The second draft must see the node just created by the first."""
    result = ingest_concepts(conn, [QUANG_HOP, QUANG_HOP])
    a, b = result.ingested
    assert a.created is True and b.created is False
    assert a.node_id == b.node_id


def test_clearly_different_concepts_get_their_own_nodes(conn):
    result = ingest_concepts(conn, [QUANG_HOP, CHIEN_TRANH, PHUONG_TRINH])
    assert all(item.created for item in result.ingested)
    assert len({item.node_id for item in result.ingested}) == 3


def test_result_order_matches_draft_order(conn):
    result = ingest_concepts(conn, [CHIEN_TRANH, QUANG_HOP])
    assert [item.draft.title for item in result.ingested] == ["Chiến tranh Lạnh", "Quang hợp"]


def test_empty_batch_returns_an_empty_result(conn):
    assert ingest_concepts(conn, []).ingested == ()


def test_candidates_empty_when_nothing_is_similar(conn):
    ingest_concepts(conn, [QUANG_HOP])
    result = ingest_concepts(conn, [CHIEN_TRANH])
    assert result.ingested[0].candidates == ()


def test_candidates_populated_even_when_created_is_true(conn):
    """Near-misses below the threshold must still be logged — logging only merges
    cannot measure false negatives."""
    ingest_concepts(conn, [ConceptDraft("Định luật Ohm", "Vật lý", "U = I*R.", SourceModule.MNEMOSYNE)])
    result = ingest_concepts(
        conn,
        [ConceptDraft("Định luật Newton 2", "Vật lý", "F = m*a.", SourceModule.MNEMOSYNE)],
    )
    item = result.ingested[0]
    assert item.created is True
    assert [c.title for c in item.candidates] == ["Định luật Ohm"]
    assert 0 < item.candidates[0].score < 0.6


def test_candidates_sorted_by_score_descending(conn):
    ingest_concepts(conn, [QUANG_HOP, ConceptDraft("Quang hợp ở thực vật C4", "Sinh học", "x", SourceModule.MNEMOSYNE)])
    cands = find_candidates(conn, "Quang hợp ở cây xanh")
    scores = [c.score for c in cands]
    assert scores == sorted(scores, reverse=True)


def test_candidates_skip_merged_nodes(conn):
    """Never suggest merging into a node that is gone."""
    a = ingest_concepts(conn, [QUANG_HOP]).ingested[0].node_id
    b = ingest_concepts(conn, [CHIEN_TRANH]).ingested[0].node_id
    with conn.cursor() as cur:
        cur.execute("UPDATE ks.nodes SET merged_into_id = %s WHERE id = %s", (b, a))
    assert find_candidates(conn, "Quang hợp") == ()


def test_candidate_limit_is_respected(conn):
    """Inserted with raw SQL to bypass dedup — these variants are so alike that
    ingest_concepts would merge them all into one."""
    with conn.cursor() as cur:
        for i in range(6):
            cur.execute(
                "INSERT INTO ks.nodes (title, subject, summary, source_module)"
                " VALUES (%s,'Sinh học','x','mnemosyne')",
                (f"Quang hợp biến thể {i}",),
            )
    assert len(find_candidates(conn, "Quang hợp", limit=3)) == 3
    assert len(find_candidates(conn, "Quang hợp", limit=10)) == 6


def test_below_threshold_creates_a_new_node_rather_than_merging(conn):
    ingest_concepts(conn, [QUANG_HOP])
    result = ingest_concepts(conn, [QUANG_HOP], threshold=1.1)
    assert result.ingested[0].created is True
    assert len(_titles(conn)) == 2


def test_source_module_lexiflash_can_be_written(conn):
    draft = ConceptDraft("Từ vựng IELTS band 7", "Tiếng Anh", "x", SourceModule.LEXIFLASH)
    node_id = ingest_concepts(conn, [draft]).ingested[0].node_id
    with conn.cursor() as cur:
        cur.execute("SELECT source_module FROM ks.nodes WHERE id = %s", (node_id,))
        assert cur.fetchone()[0] == "lexiflash"


def test_fail_loud_db_errors_propagate(conn):
    """DELIBERATELY OPPOSITE to save_transcript: ingest does NOT swallow errors."""
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE ks.nodes ADD CONSTRAINT tmp_no_empty CHECK (title <> '')")
    with pytest.raises(psycopg.Error):
        ingest_concepts(conn, [ConceptDraft("", "Sinh học", "x", SourceModule.MNEMOSYNE)])


def test_does_not_commit_itself_rollback_loses_the_data(conn):
    """The transaction belongs to the caller."""
    ingest_concepts(conn, [QUANG_HOP])
    conn.rollback()
    assert _titles(conn) == []
