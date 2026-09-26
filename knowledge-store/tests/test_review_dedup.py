"""Màn duyệt hỏi trước khi gộp: GET /extracted/{id}/candidates và accept có decision.

Tên dùng trong test là đúng các cặp đã bị gộp sai trên dữ liệu thật ngày
2026-09-26 (vở Sinh 11): "Quang tự dưỡng" → "Tự dưỡng", "Trao đổi chất ở sinh
vật đa bào" → "… đơn bào".
"""

from __future__ import annotations

import os

import psycopg
import pytest

from ks.http_app import create_app

TOKEN = "token-thật-để-test"


@pytest.fixture
def client(monkeypatch, migrated_url):
    monkeypatch.setenv("KS_HTTP_TOKEN", TOKEN)
    monkeypatch.setenv("KS_DATABASE_URL", migrated_url)
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def _sql(q, params=()):
    with psycopg.connect(os.environ["KS_DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            cur.execute(q, params)
            rows = cur.fetchall() if cur.description else None
        conn.commit()
    return rows


def _node(title, subject="Sinh học"):
    return _sql(
        "INSERT INTO ks.nodes (title, subject, summary, source_module) VALUES (%s, %s, 'x', 'note_scan') RETURNING id",
        (title, subject),
    )[0][0]


def _pending(title, summary="tóm tắt", subject="Sinh học"):
    tid = _sql(
        "INSERT INTO ks.transcripts (session_ref, content) VALUES (gen_random_uuid()::text, '{}') RETURNING id"
    )[0][0]
    return _sql(
        "INSERT INTO ks.extracted_concepts (transcript_id, title, subject, summary, source_module)"
        " VALUES (%s, %s, %s, %s, 'note_scan') RETURNING id",
        (tid, title, subject, summary),
    )[0][0]


def _log_for(title):
    return _sql(
        "SELECT decision::text, chosen_by, top_score FROM ks.ingest_log WHERE draft_title = %s", (title,)
    )


def test_candidates_bao_truoc_node_ma_nguong_se_gop_vao(client):
    tu_duong = _node("Tự dưỡng")
    _node("Phân bón", "Hóa học")
    cid = _pending("Quang tự dưỡng")
    body = client.get(f"/extracted/{cid}/candidates", headers=_auth()).get_json()
    assert body["threshold"] == 0.6
    assert body["suggested_node_id"] == str(tu_duong)
    top = body["candidates"][0]
    assert top["title"] == "Tự dưỡng" and top["score"] >= 0.6 and top["subject"] == "Sinh học"
    # Chỉ xem, không ghi gì.
    assert _sql("SELECT status::text FROM ks.extracted_concepts WHERE id = %s", (cid,))[0][0] == "pending_review"
    assert _sql("SELECT count(*) FROM ks.nodes")[0][0] == 2


def test_candidates_khong_co_ai_gan_thi_suggested_null(client):
    _node("Phân bón", "Hóa học")
    cid = _pending("Chu trình Krebs")
    body = client.get(f"/extracted/{cid}/candidates", headers=_auth()).get_json()
    assert body["suggested_node_id"] is None


def test_nguoi_hoc_chon_tao_moi_du_candidate_vuot_nguong(client):
    don_bao = _node("Trao đổi chất ở sinh vật đơn bào")
    cid = _pending("Trao đổi chất ở sinh vật đa bào")
    r = client.post(f"/extracted/{cid}/accept", json={"decision": "create"}, headers=_auth()).get_json()
    assert r["created"] is True and r["node_id"] != str(don_bao)
    assert r["candidates"][0]["score"] >= 0.6, "đúng là ca ngưỡng sẽ gộp sai"
    decision, chosen_by, top = _log_for("Trao đổi chất ở sinh vật đa bào")[0]
    assert (decision, chosen_by) == ("created", "learner") and top >= 0.6
    assert _sql("SELECT count(*) FROM ks.nodes WHERE merged_into_id IS NULL")[0][0] == 2


def test_nguoi_hoc_chon_gop_vao_node_bat_ky_trong_danh_sach(client):
    target = _node("Phân bón")
    cid = _pending("Phân bón hóa học", subject="Hóa học")
    r = client.post(f"/extracted/{cid}/accept", json={"decision": "merge", "node_id": str(target)}, headers=_auth()).get_json()
    assert r == {**r, "node_id": str(target), "created": False}
    assert _log_for("Phân bón hóa học")[0][:2] == ("merged", "learner")
    assert _sql("SELECT node_id FROM ks.extracted_concepts WHERE id = %s", (cid,))[0][0] == target


def test_gop_vao_node_da_bi_gop_hoac_khong_ton_tai_thi_400_va_khong_ghi(client):
    a, b = _node("A gốc"), _node("A trùng")
    _sql("UPDATE ks.nodes SET merged_into_id = %s WHERE id = %s", (a, b))
    cid = _pending("A khác")
    r = client.post(f"/extracted/{cid}/accept", json={"decision": "merge", "node_id": str(b)}, headers=_auth())
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_merge_target"
    r = client.post(
        f"/extracted/{cid}/accept",
        json={"decision": "merge", "node_id": "00000000-0000-0000-0000-000000000000"},
        headers=_auth(),
    )
    assert r.status_code == 400
    assert _sql("SELECT status::text FROM ks.extracted_concepts WHERE id = %s", (cid,))[0][0] == "pending_review"
    assert _log_for("A khác") == []


def test_decision_sai_hoac_merge_thieu_node_thi_400(client):
    cid = _pending("X")
    assert client.post(f"/extracted/{cid}/accept", json={"decision": "maybe"}, headers=_auth()).status_code == 400
    r = client.post(f"/extracted/{cid}/accept", json={"decision": "merge"}, headers=_auth())
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_node_id"


def test_khong_co_body_van_dung_quy_tac_tu_dong_nhu_cu(client):
    tu_duong = _node("Tự dưỡng")
    cid = _pending("Quang tự dưỡng")
    r = client.post(f"/extracted/{cid}/accept", headers=_auth()).get_json()
    assert r["created"] is False and r["node_id"] == str(tu_duong)
    assert _log_for("Quang tự dưỡng")[0][:2] == ("merged", "rule")


# ---------------------------------------------------------------- split


def _accepted_merged(title, into):
    """An accepted concept the rule merged into node `into`."""
    cid = _pending(title)
    _sql("UPDATE ks.extracted_concepts SET status = 'accepted', node_id = %s WHERE id = %s", (into, cid))
    return cid


def test_split_tao_node_rieng_va_tro_khai_niem_sang_do(client):
    from ks import confirm

    tu_duong = _node("Tự dưỡng")
    cid = _accepted_merged("Quang tự dưỡng", tu_duong)
    with psycopg.connect(os.environ["KS_DATABASE_URL"]) as conn:
        item = confirm.split(conn, cid)
        conn.commit()
    assert item.created is True and item.node_id != tu_duong
    row = _sql(
        "SELECT n.title, n.summary FROM ks.extracted_concepts c JOIN ks.nodes n ON n.id = c.node_id WHERE c.id = %s",
        (cid,),
    )[0]
    assert row == ("Quang tự dưỡng", "tóm tắt")
    assert _sql("SELECT title FROM ks.nodes WHERE id = %s", (tu_duong,))[0][0] == "Tự dưỡng", "old node untouched"
    assert _log_for("Quang tự dưỡng")[0][:2] == ("created", "learner")


def test_split_tu_choi_khai_niem_chua_gop_hoac_chua_chap_nhan(client):
    from ks import confirm

    own = _node("Chu trình Calvin")
    cid_own = _accepted_merged("Chu trình Calvin", own)  # its node carries its own title
    cid_pending = _pending("Pha sáng")
    with psycopg.connect(os.environ["KS_DATABASE_URL"]) as conn:
        with pytest.raises(confirm.NotMerged):
            confirm.split(conn, cid_own)
        with pytest.raises(confirm.AlreadyDecided):
            confirm.split(conn, cid_pending)
    assert _sql("SELECT count(*) FROM ks.nodes")[0][0] == 1
