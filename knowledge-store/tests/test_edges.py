"""Gợi ý cạnh, duyệt, và đọc đồ thị."""

from __future__ import annotations

import json

import pytest

from ks import edges
from ks.edges import (
    EdgeNotFound,
    NodeNotFound,
    add_edge,
    approve_edge,
    candidate_neighbors,
    edit_edge,
    list_pending,
    neighbors,
    parse_suggestions,
    reject_edge,
    suggest_edges,
)
from ks.llm import LLMQuotaError, LLMTransientError
from ks.models import ConceptDraft, RelationType, SourceModule
from ks.ingest import ingest_concepts


class FakeProvider:
    """Provider giả: trả text định sẵn hoặc ném lỗi định sẵn."""

    name = "fake"
    model = "fake-1"

    def __init__(self, text: str = "[]", error: Exception | None = None):
        self.text = text
        self.error = error
        self.calls: list[list] = []

    def complete(self, messages, *, max_tokens=1000, temperature=0.0) -> str:
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        return self.text


def _mk(conn, title, subject="Vật lý", summary="x"):
    draft = ConceptDraft(title, subject, summary, SourceModule.MNEMOSYNE)
    return ingest_concepts(conn, [draft]).ingested[0].node_id


@pytest.fixture
def graph(conn):
    """Ba khái niệm tên rất khác nhau — §12: tránh fixture gần giống."""
    a = _mk(conn, "Quang hợp", "Sinh học", "Cây dùng ánh sáng tạo chất hữu cơ.")
    b = _mk(conn, "Hô hấp tế bào", "Sinh học", "Tế bào giải phóng năng lượng từ glucose.")
    c = _mk(conn, "Chiến tranh Lạnh", "Lịch sử", "Đối đầu Mỹ - Liên Xô.")
    return conn, a, b, c


# ---------------------------------------------------------------- candidate set


def test_candidate_set_khong_chua_chinh_no(graph):
    conn, a, _, _ = graph
    assert all(c.node_id != a for c in candidate_neighbors(conn, a))


def test_candidate_set_uu_tien_cung_subject(graph):
    conn, a, b, c = graph
    ids = [x.node_id for x in candidate_neighbors(conn, a)]
    assert ids[0] == b, "cùng môn Sinh học phải đứng trước Lịch sử"


def test_candidate_set_bo_qua_node_da_merge(graph):
    conn, a, b, c = graph
    with conn.cursor() as cur:
        cur.execute("UPDATE ks.nodes SET merged_into_id = %s WHERE id = %s", (c, b))
    assert b not in [x.node_id for x in candidate_neighbors(conn, a)]


def test_candidate_set_loai_cap_da_bi_reject(graph):
    """Không hỏi lại câu người dùng đã trả lời."""
    conn, a, b, _ = graph
    edge_id = add_edge(conn, a, b, RelationType.RELATED)
    reject_edge(conn, edge_id)
    assert b not in [x.node_id for x in candidate_neighbors(conn, a)]


def test_candidate_set_loai_cap_da_approved(graph):
    conn, a, b, _ = graph
    add_edge(conn, a, b, RelationType.RELATED)
    assert b not in [x.node_id for x in candidate_neighbors(conn, a)]


def test_candidate_set_ton_trong_k(graph):
    conn, a, _, _ = graph
    assert len(candidate_neighbors(conn, a, k=1)) == 1


def test_node_khong_ton_tai_thi_raise(conn):
    import uuid
    with pytest.raises(NodeNotFound):
        candidate_neighbors(conn, uuid.uuid4())


# ---------------------------------------------------------------- parse


def test_parse_bo_qua_index_ngoai_pham_vi():
    text = json.dumps([{"candidate": 99, "relation_type": "related", "reason": "r"}])
    assert parse_suggestions(text, 2) == ()


def test_parse_bo_qua_relation_type_la():
    text = json.dumps([{"candidate": 0, "relation_type": "gì đó", "reason": "r"}])
    assert parse_suggestions(text, 2) == ()


def test_parse_boc_trong_code_fence():
    text = '```json\n[{"candidate": 0, "relation_type": "related", "reason": "r"}]\n```'
    assert parse_suggestions(text, 2) == ((0, RelationType.RELATED, "r"),)


def test_parse_mang_rong_la_hop_le():
    assert parse_suggestions("[]", 3) == ()


def test_parse_khong_phai_json_thi_parse_error():
    from ks.llm import LLMParseError
    with pytest.raises(LLMParseError):
        parse_suggestions("xin lỗi tôi không rõ", 2)


def test_parse_json_khong_phai_array_thi_parse_error():
    from ks.llm import LLMParseError
    with pytest.raises(LLMParseError):
        parse_suggestions('{"candidate": 0}', 2)


def test_parse_bo_trung_candidate():
    text = json.dumps([
        {"candidate": 0, "relation_type": "related", "reason": "a"},
        {"candidate": 0, "relation_type": "prerequisite", "reason": "b"},
    ])
    assert len(parse_suggestions(text, 2)) == 1


# ---------------------------------------------------------------- suggest


def test_suggest_ghi_canh_o_trang_thai_pending(graph):
    """LLM chỉ GỢI Ý — không auto-approve."""
    conn, a, b, _ = graph
    provider = FakeProvider(json.dumps([{"candidate": 0, "relation_type": "prerequisite", "reason": "r"}]))
    run = suggest_edges(conn, a, provider)
    assert run.outcome == "ok"
    assert len(run.suggestions) == 1
    with conn.cursor() as cur:
        cur.execute("SELECT status, suggested_by FROM ks.edges WHERE id = %s",
                    (run.suggestions[0].edge_id,))
        assert cur.fetchone() == ("pending", "llm")


def test_suggest_khong_co_candidate_thi_khong_goi_llm(conn):
    a = _mk(conn, "Quang hợp", "Sinh học")
    provider = FakeProvider()
    run = suggest_edges(conn, a, provider)
    assert run.outcome == "no_candidates"
    assert provider.calls == []


def test_suggest_llm_loi_thi_KHONG_raise(graph):
    """Nếu raise thì caller rollback và mất luôn dòng log instrumentation."""
    conn, a, _, _ = graph
    run = suggest_edges(conn, a, FakeProvider(error=LLMQuotaError("429")))
    assert run.outcome == "llm_error"
    assert run.suggestions == ()
    assert "429" in run.error


def test_suggest_parse_loi_tach_rieng_khoi_llm_loi(graph):
    conn, a, _, _ = graph
    run = suggest_edges(conn, a, FakeProvider("không phải json"))
    assert run.outcome == "parse_error"


def test_suggest_khong_de_len_canh_da_co_quyet_dinh(graph):
    conn, a, b, _ = graph
    edge_id = add_edge(conn, a, b, RelationType.RELATED)
    reject_edge(conn, edge_id)
    provider = FakeProvider(json.dumps([{"candidate": 0, "relation_type": "related", "reason": "r"}]))
    run = suggest_edges(conn, a, provider)
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM ks.edges WHERE id = %s", (edge_id,))
        assert cur.fetchone()[0] == "rejected"


def test_prompt_chua_title_cua_goc_va_ung_vien(graph):
    conn, a, b, _ = graph
    provider = FakeProvider()
    suggest_edges(conn, a, provider)
    prompt = provider.calls[0][1].content
    assert "Quang hợp" in prompt and "Hô hấp tế bào" in prompt


# ---------------------------------------------------------------- duyệt


def test_list_pending_chi_tra_pending(graph):
    conn, a, b, _ = graph
    provider = FakeProvider(json.dumps([{"candidate": 0, "relation_type": "related", "reason": "r"}]))
    suggest_edges(conn, a, provider)
    pending = list_pending(conn)
    assert len(pending) == 1
    assert pending[0].from_title == "Quang hợp"
    assert pending[0].to_title == "Hô hấp tế bào"


def test_approve_doi_status(graph):
    conn, a, b, _ = graph
    provider = FakeProvider(json.dumps([{"candidate": 0, "relation_type": "related", "reason": "r"}]))
    edge_id = suggest_edges(conn, a, provider).suggestions[0].edge_id
    approve_edge(conn, edge_id)
    assert list_pending(conn) == ()


def test_reject_giu_row_vinh_vien_khong_xoa(graph):
    conn, a, b, _ = graph
    provider = FakeProvider(json.dumps([{"candidate": 0, "relation_type": "related", "reason": "r"}]))
    edge_id = suggest_edges(conn, a, provider).suggestions[0].edge_id
    reject_edge(conn, edge_id)
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM ks.edges WHERE id = %s", (edge_id,))
        assert cur.fetchone()[0] == "rejected"


def test_edit_doi_relation_type_va_approve(graph):
    conn, a, b, _ = graph
    provider = FakeProvider(json.dumps([{"candidate": 0, "relation_type": "related", "reason": "r"}]))
    edge_id = suggest_edges(conn, a, provider).suggestions[0].edge_id
    edit_edge(conn, edge_id, RelationType.PREREQUISITE)
    with conn.cursor() as cur:
        cur.execute("SELECT relation_type, status FROM ks.edges WHERE id = %s", (edge_id,))
        assert cur.fetchone() == ("prerequisite", "approved")


def test_edge_khong_ton_tai_thi_raise(conn):
    import uuid
    with pytest.raises(EdgeNotFound):
        approve_edge(conn, uuid.uuid4())


def test_add_edge_vao_thang_approved(graph):
    conn, a, b, _ = graph
    edge_id = add_edge(conn, a, b, RelationType.CONTRASTS_WITH)
    with conn.cursor() as cur:
        cur.execute("SELECT status, suggested_by FROM ks.edges WHERE id = %s", (edge_id,))
        assert cur.fetchone() == ("approved", "manual")


def test_add_edge_lai_cap_da_reject_thi_hoi_sinh(graph):
    """Người dùng đổi ý — thao tác tay được quyền ghi đè quyết định cũ."""
    conn, a, b, _ = graph
    edge_id = add_edge(conn, a, b, RelationType.RELATED)
    reject_edge(conn, edge_id)
    again = add_edge(conn, a, b, RelationType.RELATED)
    assert again == edge_id
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM ks.edges WHERE id = %s", (edge_id,))
        assert cur.fetchone()[0] == "approved"


# ---------------------------------------------------------------- neighbors


def test_neighbors_chi_tra_canh_approved(graph):
    conn, a, b, _ = graph
    provider = FakeProvider(json.dumps([{"candidate": 0, "relation_type": "related", "reason": "r"}]))
    suggest_edges(conn, a, provider)
    assert neighbors(conn, a) == ()


def test_related_doi_xung_query_hai_chieu(graph):
    """Lưu một chiều a→b, nhưng neighbors(b) vẫn phải thấy a."""
    conn, a, b, _ = graph
    add_edge(conn, a, b, RelationType.RELATED)
    from_b = neighbors(conn, b)
    assert len(from_b) == 1
    assert from_b[0].node_id == a
    assert from_b[0].direction == "both"


def test_contrasts_with_doi_xung(graph):
    conn, a, b, _ = graph
    add_edge(conn, a, b, RelationType.CONTRASTS_WITH)
    assert neighbors(conn, b)[0].direction == "both"


def test_prerequisite_co_huong(graph):
    conn, a, b, _ = graph
    add_edge(conn, a, b, RelationType.PREREQUISITE)
    assert neighbors(conn, a)[0].direction == "out"
    assert neighbors(conn, b)[0].direction == "in"


def test_suggest_dung_ngan_sach_token_rong(graph):
    """Cắt ngang làm hỏng cả lô gợi ý, nên ngân sách phải rộng tay."""
    from ks import settings

    class Ghi(FakeProvider):
        def complete(self, messages, *, max_tokens=1000, temperature=0.0):
            self.max_tokens = max_tokens
            return "[]"

    conn, a, _, _ = graph
    provider = Ghi()
    suggest_edges(conn, a, provider)
    assert provider.max_tokens == settings.EDGE_SUGGESTION_MAX_TOKENS >= 4000


def test_phan_hoi_cut_duoc_ghi_la_llm_error_chu_khong_phai_parse_error(graph):
    """LLMTruncatedError là con của LLMTransientError → outcome 'llm_error',
    và transcript/run giữ được lượt retry thay vì bị coi là lỗi cấu trúc."""
    from ks.llm import LLMTruncatedError
    conn, a, _, _ = graph
    run = suggest_edges(conn, a, FakeProvider(error=LLMTruncatedError("cạn max_tokens")))
    assert run.outcome == "llm_error"
