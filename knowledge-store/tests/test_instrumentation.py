"""RÀNG BUỘC BẮT BUỘC (§7): log nằm TRONG hàm nghiệp vụ, CÙNG transaction.

Không phải bảng phụ caller tự nhớ gọi. Khoá bằng test, không bằng quy ước:
  - gọi hàm đúng cách caller thường gọi (không cờ, không hàm log phụ) → log vẫn có
  - rollback → mất CẢ HAI (log lẫn data)
"""

from __future__ import annotations

import json

from ks.edges import add_edge, approve_edge, edit_edge, reject_edge, stats, suggest_edges
from ks.ingest import ingest_concepts
from ks.models import ConceptDraft, RelationType, SourceModule

from tests.test_edges import FakeProvider


def _count(conn, table, where="TRUE", params=()):
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM ks.{table} WHERE {where}", params)
        return cur.fetchone()[0]


def _draft(title, subject="Sinh học", summary="x"):
    return ConceptDraft(title, subject, summary, SourceModule.MNEMOSYNE)


def _mk(conn, title, subject="Sinh học", summary="x"):
    return ingest_concepts(conn, [_draft(title, subject, summary)]).ingested[0].node_id


# ---------------------------------------------------------------- ingest_log


def test_ingest_log_ghi_ma_KHONG_can_co_hay_ham_phu(conn):
    """Gọi đúng cách caller thường gọi. Không tham số bật log."""
    ingest_concepts(conn, [_draft("Quang hợp")])
    assert _count(conn, "ingest_log") == 1


def test_ingest_log_ghi_ca_khi_tao_moi(conn):
    ingest_concepts(conn, [_draft("Quang hợp")])
    with conn.cursor() as cur:
        cur.execute("SELECT decision FROM ks.ingest_log")
        assert cur.fetchone()[0] == "created"


def test_ingest_log_ghi_ca_khi_gop(conn):
    ingest_concepts(conn, [_draft("Quang hợp")])
    ingest_concepts(conn, [_draft("Quang hợp")])
    with conn.cursor() as cur:
        cur.execute("SELECT decision FROM ks.ingest_log ORDER BY created_at, id")
        assert {r[0] for r in cur.fetchall()} == {"created", "merged"}


def test_ingest_log_luu_candidate_set_va_nguong(conn):
    _mk(conn, "Định luật Ohm", "Vật lý", "U = I*R")
    ingest_concepts(conn, [_draft("Định luật Newton 2", "Vật lý", "F = m*a")])
    with conn.cursor() as cur:
        cur.execute(
            "SELECT threshold, top_score, candidates FROM ks.ingest_log"
            " WHERE draft_title = 'Định luật Newton 2'"
        )
        threshold, top_score, candidates = cur.fetchone()
    assert threshold == 0.6
    assert 0 < top_score < 0.6
    assert candidates[0]["title"] == "Định luật Ohm"


def test_ingest_log_moi_draft_mot_dong(conn):
    ingest_concepts(conn, [_draft("Quang hợp"), _draft("Chiến tranh Lạnh", "Lịch sử")])
    assert _count(conn, "ingest_log") == 2


def test_rollback_mat_CA_HAI_log_lan_data(conn):
    """Cùng transaction — không có chuyện log sống sót còn data thì không."""
    ingest_concepts(conn, [_draft("Quang hợp")])
    assert _count(conn, "nodes") == 1 and _count(conn, "ingest_log") == 1
    conn.rollback()
    assert _count(conn, "nodes") == 0 and _count(conn, "ingest_log") == 0


# ---------------------------------------------------------------- suggestion_run


def test_suggestion_run_ghi_candidate_set(conn):
    a = _mk(conn, "Quang hợp")
    b = _mk(conn, "Hô hấp tế bào")
    suggest_edges(conn, a, FakeProvider("[]"))
    with conn.cursor() as cur:
        cur.execute("SELECT candidate_node_ids, outcome, provider, model FROM ks.edge_suggestion_run")
        ids, outcome, provider, model = cur.fetchone()
    assert b in ids
    assert (outcome, provider, model) == ("ok", "fake", "fake-1")


def test_suggestion_run_ghi_ca_khi_llm_loi(conn):
    """Đây là lý do suggest_edges không raise: lần thất bại cũng phải đo được."""
    from ks.llm import LLMTransientError
    a = _mk(conn, "Quang hợp")
    _mk(conn, "Hô hấp tế bào")
    suggest_edges(conn, a, FakeProvider(error=LLMTransientError("503 upstream")))
    with conn.cursor() as cur:
        cur.execute("SELECT outcome, error FROM ks.edge_suggestion_run")
        outcome, error = cur.fetchone()
    assert outcome == "llm_error"
    assert "503" in error


def test_suggestion_run_ghi_ca_khi_khong_co_candidate(conn):
    a = _mk(conn, "Quang hợp")
    suggest_edges(conn, a, FakeProvider())
    assert _count(conn, "edge_suggestion_run", "outcome = 'no_candidates'") == 1


def test_rollback_mat_ca_canh_lan_suggestion_run(conn):
    a = _mk(conn, "Quang hợp")
    _mk(conn, "Hô hấp tế bào")
    suggest_edges(conn, a, FakeProvider(
        json.dumps([{"candidate": 0, "relation_type": "related", "reason": "r"}])))
    assert _count(conn, "edges") == 1 and _count(conn, "edge_suggestion_run") == 1
    conn.rollback()
    assert _count(conn, "edges") == 0 and _count(conn, "edge_suggestion_run") == 0


# ---------------------------------------------------------------- decision_log


def test_approve_reject_edit_deu_ghi_decision_log(conn):
    a = _mk(conn, "Quang hợp")
    _mk(conn, "Hô hấp tế bào")
    _mk(conn, "Chiến tranh Lạnh", "Lịch sử")
    run = suggest_edges(conn, a, FakeProvider(json.dumps([
        {"candidate": 0, "relation_type": "related", "reason": "r"},
        {"candidate": 1, "relation_type": "related", "reason": "r"},
    ])))
    approve_edge(conn, run.suggestions[0].edge_id)
    reject_edge(conn, run.suggestions[1].edge_id)
    with conn.cursor() as cur:
        cur.execute("SELECT decision, count(*) FROM ks.edge_decision_log GROUP BY decision")
        assert dict(cur.fetchall()) == {"approved": 1, "rejected": 1}


def test_edit_ghi_lai_relation_type_cu(conn):
    """Đo LLM đoán sai loại quan hệ ở đâu."""
    a = _mk(conn, "Quang hợp")
    _mk(conn, "Hô hấp tế bào")
    run = suggest_edges(conn, a, FakeProvider(
        json.dumps([{"candidate": 0, "relation_type": "related", "reason": "r"}])))
    edit_edge(conn, run.suggestions[0].edge_id, RelationType.CONTRASTS_WITH)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT previous_relation_type, relation_type FROM ks.edge_decision_log"
            " WHERE decision = 'edited'"
        )
        assert cur.fetchone() == ("related", "contrasts_with")


def test_add_edge_tay_ma_fulltext_KHONG_de_xuat_duoc(conn):
    """CHỈ SỐ QUAN TRỌNG NHẤT: căn cứ duy nhất để sau này quyết pgvector."""
    a = _mk(conn, "Quang hợp", "Sinh học", "Cây dùng ánh sáng.")
    b = _mk(conn, "Chiến tranh Lạnh", "Lịch sử", "Đối đầu Mỹ - Liên Xô.")
    add_edge(conn, a, b, RelationType.RELATED)
    assert _count(conn, "edge_decision_log",
                  "decision = 'manual_add' AND was_in_candidate_set = false") == 1
    assert stats(conn)["manual_add_missed_by_fulltext"] == 1


def test_add_edge_tay_nhung_fulltext_CO_de_xuat_thi_khong_tinh_la_bo_sot(conn):
    a = _mk(conn, "Quang hợp")
    b = _mk(conn, "Hô hấp tế bào")
    suggest_edges(conn, a, FakeProvider("[]"))  # b lọt vào candidate set nhưng LLM bỏ qua
    add_edge(conn, a, b, RelationType.RELATED)
    assert stats(conn)["manual_add_missed_by_fulltext"] == 0
    assert _count(conn, "edge_decision_log",
                  "decision = 'manual_add' AND was_in_candidate_set = true") == 1


def test_rollback_mat_ca_decision_log(conn):
    a = _mk(conn, "Quang hợp")
    b = _mk(conn, "Hô hấp tế bào")
    add_edge(conn, a, b, RelationType.RELATED)
    assert _count(conn, "edge_decision_log") == 1
    conn.rollback()
    assert _count(conn, "edge_decision_log") == 0


def test_stats_tong_hop_du_cac_muc(conn):
    a = _mk(conn, "Quang hợp")
    b = _mk(conn, "Hô hấp tế bào")
    add_edge(conn, a, b, RelationType.RELATED)
    s = stats(conn)
    assert s["nodes"] == 2
    assert s["edges_by_status"] == {"approved": 1}
    assert s["ingest_by_decision"] == {"created": 2}
    assert s["manual_add_total"] == 1
