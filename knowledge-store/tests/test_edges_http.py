"""GET /edges: chỉ cạnh approved, resolve merge đúng một bước như GET /nodes."""

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


def _node(title):
    return _sql(
        "INSERT INTO ks.nodes (title, subject, summary, source_module)"
        " VALUES (%s, 'Sinh học', 'x', 'note_scan') RETURNING id",
        (title,),
    )[0][0]


def _edge(a, b, relation="prerequisite", status="approved"):
    _sql(
        "INSERT INTO ks.edges (from_node_id, to_node_id, relation_type, suggested_by, status)"
        " VALUES (%s, %s, %s, 'llm', %s)",
        (a, b, relation, status),
    )


def _edges(client, query=""):
    resp = client.get(f"/edges{query}", headers=_auth())
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["edges"]


def test_edges_chi_tra_canh_approved(client):
    a, b, c, d = (_node(t) for t in ("Quang hợp", "Pha sáng", "Pha tối", "Hô hấp"))
    _edge(a, b, "prerequisite", "approved")
    _edge(a, c, "related", "pending")
    _edge(a, d, "contrasts_with", "rejected")
    edges = _edges(client)
    assert [(e["from"], e["to"], e["relation_type"]) for e in edges] == [(str(a), str(b), "prerequisite")]
    assert edges[0]["symmetric"] is False


def test_edges_quan_he_doi_xung_duoc_danh_dau(client):
    a, b = _node("Tự dưỡng"), _node("Dị dưỡng")
    _edge(a, b, "contrasts_with")
    assert _edges(client)[0]["symmetric"] is True


def test_edges_resolve_merge_mot_buoc_va_bo_vong_va_gop_trung(client):
    a, a2, b, c = (_node(t) for t in ("Quang hợp", "Quang hợp (bản trùng)", "Pha sáng", "Diệp lục"))
    _edge(a, b)          # A → B
    _edge(a2, b)         # A2 → B, sau khi A2 merge vào A thì trùng cạnh trên
    _edge(a2, a, "related")  # A2 → A, sau merge thành A → A: bỏ
    _edge(c, a2, "related")  # C → A2, sau merge thành C → A
    _sql("UPDATE ks.nodes SET merged_into_id = %s WHERE id = %s", (a, a2))
    got = sorted((e["from"], e["to"], e["relation_type"]) for e in _edges(client))
    assert got == sorted([(str(a), str(b), "prerequisite"), (str(c), str(a), "related")])
    # Mọi id trong cạnh đều là id mà GET /nodes trả.
    node_ids = {n["id"] for n in client.get("/nodes?limit=500", headers=_auth()).get_json()["nodes"]}
    assert {x for e in _edges(client) for x in (e["from"], e["to"])} <= node_ids


def test_edges_loc_theo_node_ap_len_id_da_resolve(client):
    a, a2, b, c = (_node(t) for t in ("A", "A trùng", "B", "C"))
    _edge(a2, b)
    _edge(b, c)
    _sql("UPDATE ks.nodes SET merged_into_id = %s WHERE id = %s", (a, a2))
    got = [(e["from"], e["to"]) for e in _edges(client, f"?node_id={a}")]
    assert got == [(str(a), str(b))]


def test_edges_tham_so_sai_thi_400(client):
    assert client.get("/edges?limit=0", headers=_auth()).status_code == 400
    assert client.get("/edges?limit=abc", headers=_auth()).status_code == 400
    assert client.get("/edges?limit=999999", headers=_auth()).status_code == 400
    r = client.get("/edges?node_id=khong-phai-uuid", headers=_auth())
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_node_id"


def test_edges_can_token(client):
    assert client.get("/edges").status_code == 403


def test_edges_rong_thi_tra_mang_rong(client):
    assert _edges(client) == []
