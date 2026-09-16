"""ingest_concepts: tạo mới, gộp trùng, ghi candidate, và fail-loud."""

from __future__ import annotations

from uuid import UUID

import psycopg
import pytest

from ks.ingest import find_candidates, ingest_concepts
from ks.models import ConceptDraft, SourceModule

# Tên rất khác nhau — §12: tránh dùng cặp gần giống làm fixture.
QUANG_HOP = ConceptDraft("Quang hợp", "Sinh học", "Cây dùng ánh sáng tạo chất hữu cơ.", SourceModule.MNEMOSYNE)
CHIEN_TRANH = ConceptDraft("Chiến tranh Lạnh", "Lịch sử", "Đối đầu Mỹ - Liên Xô.", SourceModule.MNEMOSYNE)
PHUONG_TRINH = ConceptDraft("Phương trình bậc hai", "Toán", "ax^2 + bx + c = 0.", SourceModule.MNEMOSYNE)


def _titles(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT title FROM ks.nodes ORDER BY title")
        return [r[0] for r in cur.fetchall()]


def test_draft_dau_tien_tao_node_moi(conn):
    result = ingest_concepts(conn, [QUANG_HOP])
    assert len(result.ingested) == 1
    assert result.ingested[0].created is True


def test_node_id_la_uuid_va_row_that_su_ton_tai(conn):
    node_id = ingest_concepts(conn, [QUANG_HOP]).ingested[0].node_id
    assert isinstance(node_id, UUID)
    with conn.cursor() as cur:
        cur.execute("SELECT title, subject, summary, source_module FROM ks.nodes WHERE id = %s", (node_id,))
        assert cur.fetchone() == ("Quang hợp", "Sinh học", "Cây dùng ánh sáng tạo chất hữu cơ.", "mnemosyne")


def test_title_y_het_thi_gop_khong_tao_moi(conn):
    first = ingest_concepts(conn, [QUANG_HOP]).ingested[0]
    second = ingest_concepts(conn, [QUANG_HOP]).ingested[0]
    assert second.created is False
    assert second.node_id == first.node_id
    assert _titles(conn) == ["Quang hợp"]


def test_trung_trong_cung_mot_lo_van_gop(conn):
    """Draft thứ hai phải nhìn thấy node vừa tạo bởi draft thứ nhất."""
    result = ingest_concepts(conn, [QUANG_HOP, QUANG_HOP])
    a, b = result.ingested
    assert a.created is True and b.created is False
    assert a.node_id == b.node_id


def test_khai_niem_khac_han_thi_tao_node_rieng(conn):
    result = ingest_concepts(conn, [QUANG_HOP, CHIEN_TRANH, PHUONG_TRINH])
    assert all(item.created for item in result.ingested)
    assert len({item.node_id for item in result.ingested}) == 3


def test_thu_tu_ket_qua_khop_thu_tu_draft(conn):
    result = ingest_concepts(conn, [CHIEN_TRANH, QUANG_HOP])
    assert [item.draft.title for item in result.ingested] == ["Chiến tranh Lạnh", "Quang hợp"]


def test_lo_rong_tra_ve_ket_qua_rong(conn):
    assert ingest_concepts(conn, []).ingested == ()


def test_candidates_rong_khi_khong_co_gi_giong(conn):
    ingest_concepts(conn, [QUANG_HOP])
    result = ingest_concepts(conn, [CHIEN_TRANH])
    assert result.ingested[0].candidates == ()


def test_candidates_populate_ca_khi_created_true(conn):
    """Near-miss dưới ngưỡng vẫn phải log — nếu chỉ log ca merge thì không đo
    được false negative."""
    ingest_concepts(conn, [ConceptDraft("Định luật Ohm", "Vật lý", "U = I*R.", SourceModule.MNEMOSYNE)])
    result = ingest_concepts(
        conn,
        [ConceptDraft("Định luật Newton 2", "Vật lý", "F = m*a.", SourceModule.MNEMOSYNE)],
    )
    item = result.ingested[0]
    assert item.created is True
    assert [c.title for c in item.candidates] == ["Định luật Ohm"]
    assert 0 < item.candidates[0].score < 0.6


def test_candidates_sap_giam_dan_theo_score(conn):
    ingest_concepts(conn, [QUANG_HOP, ConceptDraft("Quang hợp ở thực vật C4", "Sinh học", "x", SourceModule.MNEMOSYNE)])
    cands = find_candidates(conn, "Quang hợp ở cây xanh")
    scores = [c.score for c in cands]
    assert scores == sorted(scores, reverse=True)


def test_candidates_bo_qua_node_da_merge(conn):
    """Không gợi ý gộp vào một node đã chết."""
    a = ingest_concepts(conn, [QUANG_HOP]).ingested[0].node_id
    b = ingest_concepts(conn, [CHIEN_TRANH]).ingested[0].node_id
    with conn.cursor() as cur:
        cur.execute("UPDATE ks.nodes SET merged_into_id = %s WHERE id = %s", (b, a))
    assert find_candidates(conn, "Quang hợp") == ()


def test_candidate_limit_duoc_ton_trong(conn):
    """Insert thẳng SQL để bỏ qua dedup — các biến thể này giống nhau tới mức
    ingest_concepts sẽ gộp hết làm một."""
    with conn.cursor() as cur:
        for i in range(6):
            cur.execute(
                "INSERT INTO ks.nodes (title, subject, summary, source_module)"
                " VALUES (%s,'Sinh học','x','mnemosyne')",
                (f"Quang hợp biến thể {i}",),
            )
    assert len(find_candidates(conn, "Quang hợp", limit=3)) == 3
    assert len(find_candidates(conn, "Quang hợp", limit=10)) == 6


def test_duoi_nguong_thi_tao_node_moi_chu_khong_gop(conn):
    ingest_concepts(conn, [QUANG_HOP])
    result = ingest_concepts(conn, [QUANG_HOP], threshold=1.1)
    assert result.ingested[0].created is True
    assert len(_titles(conn)) == 2


def test_source_module_lexiflash_ghi_duoc(conn):
    draft = ConceptDraft("Từ vựng IELTS band 7", "Tiếng Anh", "x", SourceModule.LEXIFLASH)
    node_id = ingest_concepts(conn, [draft]).ingested[0].node_id
    with conn.cursor() as cur:
        cur.execute("SELECT source_module FROM ks.nodes WHERE id = %s", (node_id,))
        assert cur.fetchone()[0] == "lexiflash"


def test_fail_loud_loi_db_van_thang_ra(conn):
    """ĐỐI LẬP CÓ CHỦ ĐÍCH với save_transcript: ingest KHÔNG nuốt lỗi."""
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE ks.nodes ADD CONSTRAINT tmp_no_empty CHECK (title <> '')")
    with pytest.raises(psycopg.Error):
        ingest_concepts(conn, [ConceptDraft("", "Sinh học", "x", SourceModule.MNEMOSYNE)])


def test_khong_tu_commit_rollback_mat_du_lieu(conn):
    """Transaction thuộc về caller."""
    ingest_concepts(conn, [QUANG_HOP])
    conn.rollback()
    assert _titles(conn) == []
