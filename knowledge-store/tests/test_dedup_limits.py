"""Giới hạn ĐÃ BIẾT của dò trùng trigram — khoá bằng test, KHÔNG chỉnh ngưỡng.

Chỉnh mù là đoán; hạ ngưỡng chắc chắn kéo theo false negative ở nơi khác.
Quyết định nâng cấp (pgvector/embedding) chờ dữ liệu thật từ Mnemosyne.

QUY TẮC GHI EVIDENCE POINT (xem NOTES.md): mỗi con số phải đi kèm NGUYÊN VĂN cả
hai chuỗi, phiên bản pg_trgm, và collation của DB. Ghi số trần thì lần sau không
diễn giải lại được — đã suýt trả giá đúng một lần vì chuyện này.
"""

from __future__ import annotations

import pytest

from ks import settings
from ks.ingest import ingest_concepts
from ks.models import ConceptDraft, SourceModule


def _sim(conn, a: str, b: str) -> float:
    with conn.cursor() as cur:
        cur.execute("SELECT similarity(%s, %s)", (a, b))
        return float(cur.fetchone()[0])


def _draft(title: str) -> ConceptDraft:
    return ConceptDraft(title, "Vật lý", "x", SourceModule.MNEMOSYNE)


# ---------------------------------------------------------------- dấu vân tay môi trường


def test_dau_van_tay_moi_truong_cua_cac_evidence_point(conn):
    """Các số dưới đây chỉ có nghĩa kèm môi trường này. Test đỏ = môi trường đổi,
    KHÔNG phải code hỏng — đọc NOTES.md trước khi sửa số."""
    with conn.cursor() as cur:
        cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'pg_trgm'")
        assert cur.fetchone()[0] == "1.6"
        cur.execute(
            "SELECT datcollate, datctype FROM pg_database WHERE datname = current_database()"
        )
        assert cur.fetchone() == ("en_US.UTF-8", "en_US.UTF-8")


# ---------------------------------------------------------------- evidence point


@pytest.mark.parametrize(
    "a, b, expected, bi_gop",
    [
        # Ba evidence point gốc của lần build trước, đo đúng chuỗi nguyên văn.
        ("Định luật Newton 1", "Định luật Newton 2", 0.8095, True),
        ("Định luật khúc xạ ánh sáng", "Định luật phản xạ ánh sáng", 0.6774, True),
        ("Định luật Ohm (curl-nodes)", "Định luật Newton 2 (curl-nodes)", 0.6364, True),
        # Cùng khái niệm, bỏ tiền tố/hậu tố dùng chung → tụt xuống dưới ngưỡng.
        # Đây là bằng chứng similarity phụ thuộc phần CHUNG chứ không phải phần khác.
        ("khúc xạ", "phản xạ", 0.2308, False),
        ("Định luật Ohm", "Định luật Newton 2", 0.4348, False),
    ],
)
def test_evidence_point_similarity(conn, a, b, expected, bi_gop):
    score = _sim(conn, a, b)
    assert score == pytest.approx(expected, abs=0.001)
    assert (score >= settings.DUPLICATE_THRESHOLD) is bi_gop


# ---------------------------------------------------------------- hành vi gộp nhầm


def test_numbered_variants_are_wrongly_deduped(conn):
    """LỖI ĐÃ BIẾT: hai định luật khác nhau bị gộp làm một chỉ vì tên khác mỗi chữ số."""
    ingest_concepts(conn, [_draft("Định luật Newton 1")])
    result = ingest_concepts(conn, [_draft("Định luật Newton 2")])
    assert result.ingested[0].created is False, (
        "Nếu test này đỏ: hành vi dedup đã đổi, đọc NOTES.md trước khi sửa"
    )


def test_khai_niem_doi_lap_bi_gop_khi_title_co_hau_to_chung(conn):
    """khúc xạ và phản xạ là hai hiện tượng ĐỐI LẬP, vẫn bị gộp — vì tiền tố
    'Định luật ' cộng hậu tố ' ánh sáng' chiếm đa số trigram (0.6774)."""
    ingest_concepts(conn, [_draft("Định luật khúc xạ ánh sáng")])
    result = ingest_concepts(conn, [_draft("Định luật phản xạ ánh sáng")])
    assert result.ingested[0].created is False


def test_HAU_TO_DUNG_CHUNG_la_thu_nguy_hiem_nhat(conn):
    """Cùng một cặp khái niệm: bỏ hậu tố chung thì KHÔNG gộp, thêm vào thì GỘP.

    Phần khác biệt y hệt nhau ở cả hai lượt — chỉ phần CHUNG thay đổi. Title thật
    từ Mnemosyne rất dễ mang hậu tố chung (tên chương, tên môn, tên bộ đề), nên
    đây là đường false-positive đáng theo dõi nhất khi có dữ liệu thật.
    """
    khong_hau_to = ingest_concepts(
        conn, [_draft("Định luật Ohm"), _draft("Định luật Newton 2")]
    ).ingested
    assert [i.created for i in khong_hau_to] == [True, True]

    co_hau_to = ingest_concepts(
        conn,
        [_draft("Định luật Ohm (curl-nodes)"), _draft("Định luật Newton 2 (curl-nodes)")],
    ).ingested
    assert [i.created for i in co_hau_to] == [True, False]


def test_nguong_gop_van_la_0_6(conn):
    """Khoá hằng số: đổi ngưỡng là quyết định của Agent A, không phải của code."""
    assert settings.DUPLICATE_THRESHOLD == 0.6
