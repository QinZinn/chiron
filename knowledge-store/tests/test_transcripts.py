"""save_transcript (không bao giờ raise) + extract_concepts (job retry được)."""

from __future__ import annotations

import json
import uuid

import pytest

from ks.llm import LLMParseError, LLMQuotaError, LLMTransientError
from ks.transcripts import (
    TranscriptNotFound,
    build_prompt,
    extract_concepts,
    parse_extraction,
    pending_transcripts,
    render_transcript,
    save_transcript,
)

from tests.test_edges import FakeProvider

CONTENT = [
    {"role": "assistant", "content": "Định luật Newton 2 nói gì?"},
    {"role": "user", "content": "F bằng m nhân a."},
]

TWO_CONCEPTS = json.dumps([
    {"title": "Định luật Newton 2", "subject": "Vật lý", "summary": "F = m*a."},
    {"title": "Quang hợp", "subject": "Sinh học", "summary": "Cây dùng ánh sáng."},
])


def _save(conn, session_ref="s-1", content=CONTENT):
    """save_transcript tự mở connection, nên test dùng cùng URL của fixture."""
    result = save_transcript(session_ref, content, url=conn.info.dsn)
    return result


# ---------------------------------------------------------------- save


def test_save_tra_ve_ok_va_transcript_id(migrated_url):
    result = save_transcript(f"s-{uuid.uuid4()}", CONTENT, url=migrated_url)
    assert result.ok is True
    assert result.transcript_id is not None
    assert result.error is None


def test_save_KHONG_BAO_GIO_raise_khi_db_chet():
    """Phiên học không được hỏng vì KS chết."""
    result = save_transcript("s-x", CONTENT, url="postgresql://nobody@127.0.0.1:1/khong_co")
    assert result.ok is False
    assert result.transcript_id is None
    assert result.error


def test_save_khong_raise_ca_khi_url_vo_nghia():
    result = save_transcript("s-x", CONTENT, url="đây không phải dsn")
    assert result.ok is False


def test_save_khong_raise_khi_content_khong_serialize_duoc():
    result = save_transcript("s-x", {"f": object()}, url="postgresql://x@127.0.0.1:1/y")
    assert result.ok is False


def test_save_idempotent_that_theo_session_ref(migrated_url):
    ref = f"s-{uuid.uuid4()}"
    first = save_transcript(ref, CONTENT, url=migrated_url)
    second = save_transcript(ref, CONTENT, url=migrated_url)
    assert second.ok is True
    assert second.transcript_id == first.transcript_id


def test_save_lai_KHONG_de_len_content_da_luu(migrated_url):
    ref = f"s-{uuid.uuid4()}"
    save_transcript(ref, CONTENT, url=migrated_url)
    save_transcript(ref, [{"role": "user", "content": "nội dung khác"}], url=migrated_url)
    import psycopg
    with psycopg.connect(migrated_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT content FROM ks.transcripts WHERE session_ref = %s", (ref,))
        assert cur.fetchone()[0] == CONTENT


def test_save_nhan_content_JSON_bat_ky(migrated_url):
    for content in ({"a": 1}, [1, 2, 3], "chuỗi", 42, None):
        assert save_transcript(f"s-{uuid.uuid4()}", content, url=migrated_url).ok is True


# ---------------------------------------------------------------- render / parse


def test_render_nhan_dien_dang_role_content():
    assert render_transcript(CONTENT) == (
        "assistant: Định luật Newton 2 nói gì?\nuser: F bằng m nhân a."
    )


def test_render_dang_khac_van_giu_du_lieu():
    out = render_transcript({"gì đó": "khác"})
    assert "gì đó" in out


def test_prompt_chua_noi_dung_transcript():
    prompt = build_prompt(CONTENT)[1].content
    assert "F bằng m nhân a." in prompt


def test_parse_bo_qua_phan_tu_thieu_field():
    text = json.dumps([{"title": "X"}, {"title": "Y", "subject": "S", "summary": "M"}])
    assert parse_extraction(text) == (("Y", "S", "M"),)


def test_parse_khong_phai_json_thi_parse_error():
    with pytest.raises(LLMParseError):
        parse_extraction("xin lỗi")


def test_parse_mang_rong_hop_le():
    assert parse_extraction("[]") == ()


# ---------------------------------------------------------------- extract


def _new_transcript(conn, content=CONTENT):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ks.transcripts (session_ref, content) VALUES (%s, %s) RETURNING id",
            (f"s-{uuid.uuid4()}", json.dumps(content, ensure_ascii=False)),
        )
        return cur.fetchone()[0]


def test_extract_ghi_khai_niem_o_trang_thai_cho_xac_nhan(conn):
    tid = _new_transcript(conn)
    result = extract_concepts(conn, tid, FakeProvider(TWO_CONCEPTS))
    assert result.ok is True
    assert len(result.concepts) == 2
    assert {c.status for c in result.concepts} == {"pending_review"}


def test_extract_KHONG_tu_ghi_vao_nodes(conn):
    """Extraction chỉ đề xuất; vào đồ thị là việc của accept."""
    tid = _new_transcript(conn)
    extract_concepts(conn, tid, FakeProvider(TWO_CONCEPTS))
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ks.nodes")
        assert cur.fetchone()[0] == 0


def test_extract_thanh_cong_thi_status_done(conn):
    tid = _new_transcript(conn)
    extract_concepts(conn, tid, FakeProvider("[]"))
    with conn.cursor() as cur:
        cur.execute("SELECT status, attempts, last_error FROM ks.transcripts WHERE id = %s", (tid,))
        assert cur.fetchone() == ("done", 1, None)


def test_extract_llm_loi_thi_KHONG_raise_va_tang_attempts(conn):
    tid = _new_transcript(conn)
    result = extract_concepts(conn, tid, FakeProvider(error=LLMTransientError("503")))
    assert result.ok is False and result.attempts == 1
    with conn.cursor() as cur:
        cur.execute("SELECT status, attempts, last_error FROM ks.transcripts WHERE id = %s", (tid,))
        status, attempts, error = cur.fetchone()
    assert (status, attempts) == ("pending", 1)
    assert "503" in error


def test_extract_can_luot_thi_status_failed(conn):
    tid = _new_transcript(conn)
    for _ in range(3):
        extract_concepts(conn, tid, FakeProvider(error=LLMQuotaError("429")), max_attempts=3)
    with conn.cursor() as cur:
        cur.execute("SELECT status, attempts FROM ks.transcripts WHERE id = %s", (tid,))
        assert cur.fetchone() == ("failed", 3)


def test_retry_don_ket_qua_cu_con_pending_review(conn):
    tid = _new_transcript(conn)
    extract_concepts(conn, tid, FakeProvider(TWO_CONCEPTS))
    extract_concepts(conn, tid, FakeProvider(json.dumps(
        [{"title": "Chỉ một", "subject": "Toán", "summary": "m"}])))
    with conn.cursor() as cur:
        cur.execute("SELECT title FROM ks.extracted_concepts WHERE transcript_id = %s", (tid,))
        assert [r[0] for r in cur.fetchall()] == ["Chỉ một"]


def test_retry_GIU_NGUYEN_thu_da_accepted_hoac_discarded(conn):
    """Không hỏi lại câu người dùng đã trả lời."""
    from ks.confirm import accept, discard
    tid = _new_transcript(conn)
    first = extract_concepts(conn, tid, FakeProvider(TWO_CONCEPTS)).concepts
    accept(conn, first[0].id)
    discard(conn, first[1].id)
    extract_concepts(conn, tid, FakeProvider(json.dumps(
        [{"title": "Mới toanh", "subject": "Toán", "summary": "m"}])))
    with conn.cursor() as cur:
        cur.execute(
            "SELECT title, status FROM ks.extracted_concepts WHERE transcript_id = %s"
            " ORDER BY status, title", (tid,))
        # enum sắp theo thứ tự khai báo: pending_review → accepted → discarded
        assert cur.fetchall() == [
            ("Mới toanh", "pending_review"),
            ("Định luật Newton 2", "accepted"),
            ("Quang hợp", "discarded"),
        ]


def test_extract_transcript_khong_ton_tai_thi_raise(conn):
    with pytest.raises(TranscriptNotFound):
        extract_concepts(conn, uuid.uuid4(), FakeProvider())


def test_pending_transcripts_bo_qua_thu_da_done(conn):
    a = _new_transcript(conn)
    b = _new_transcript(conn)
    extract_concepts(conn, a, FakeProvider("[]"))
    assert pending_transcripts(conn) == (b,)


def test_pending_transcripts_bo_qua_thu_can_luot(conn):
    tid = _new_transcript(conn)
    with conn.cursor() as cur:
        cur.execute("UPDATE ks.transcripts SET attempts = 5 WHERE id = %s", (tid,))
    assert pending_transcripts(conn, max_attempts=5) == ()
