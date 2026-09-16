"""HTTP layer: auth, /health, POST /transcripts, POST /ingest."""

from __future__ import annotations

import json
import uuid

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


def _auth(token=TOKEN):
    return {"Authorization": f"Bearer {token}"}


def _draft(title="Quang hợp", subject="Sinh học", summary="x", source="mnemosyne"):
    return {"title": title, "subject": subject, "summary": summary, "source_module": source}


# ---------------------------------------------------------------- auth


def test_health_khong_can_auth(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_thieu_token_thi_403(client):
    assert client.post("/transcripts", json={}).status_code == 403


def test_token_sai_thi_403(client):
    assert client.post("/transcripts", json={}, headers=_auth("sai")).status_code == 403


def test_header_khong_co_tien_to_Bearer_thi_403(client):
    resp = client.post("/transcripts", json={}, headers={"Authorization": TOKEN})
    assert resp.status_code == 403


def test_header_NON_ASCII_tra_403_chu_KHONG_crash_500(client):
    """BUG ĐÃ GẶP: hmac.compare_digest ném TypeError với str non-ASCII → 500.
    So trên bytes thì header dị dạng cỡ nào cũng chỉ là 403."""
    for bad in ("Bearer đây-là-tiếng-việt", "Bearer 🔑🔑🔑", "Bearer ünïcödé"):
        resp = client.post("/transcripts", json={}, headers={"Authorization": bad})
        assert resp.status_code == 403, f"header {bad!r} phải là 403"


def test_token_dung_do_dai_nhung_sai_noi_dung_thi_403(client):
    assert client.post("/transcripts", json={}, headers=_auth("x" * len(TOKEN))).status_code == 403


def test_chua_cau_hinh_token_thi_tu_choi_het_chu_khong_mo_toang(monkeypatch, migrated_url):
    monkeypatch.delenv("KS_HTTP_TOKEN", raising=False)
    monkeypatch.setenv("KS_DATABASE_URL", migrated_url)
    app = create_app()
    with app.test_client() as c:
        assert c.post("/ingest", json={"drafts": []}, headers=_auth("")).status_code == 403


# ---------------------------------------------------------------- /transcripts


def test_transcripts_ghi_thanh_cong(client):
    resp = client.post(
        "/transcripts",
        json={"session_ref": f"s-{uuid.uuid4()}", "content": [{"role": "user", "content": "hi"}]},
        headers=_auth(),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True and body["error"] is None
    uuid.UUID(body["transcript_id"])  # phải parse được


def test_transcripts_idempotent_that_theo_session_ref(client):
    ref = f"s-{uuid.uuid4()}"
    payload = {"session_ref": ref, "content": ["a"]}
    first = client.post("/transcripts", json=payload, headers=_auth()).get_json()
    second = client.post("/transcripts", json=payload, headers=_auth()).get_json()
    assert second["transcript_id"] == first["transcript_id"]


def test_transcripts_nhan_content_JSON_bat_ky(client):
    for content in ({"a": 1}, [1, 2], "chuỗi", 7, None):
        resp = client.post(
            "/transcripts",
            json={"session_ref": f"s-{uuid.uuid4()}", "content": content},
            headers=_auth(),
        )
        assert resp.get_json()["ok"] is True


def test_transcripts_thieu_session_ref_thi_400(client):
    resp = client.post("/transcripts", json={"content": []}, headers=_auth())
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_session_ref"


def test_transcripts_thieu_content_thi_400(client):
    resp = client.post("/transcripts", json={"session_ref": "s"}, headers=_auth())
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "missing_content"


def test_transcripts_body_khong_phai_json_thi_400(client):
    resp = client.post("/transcripts", data="không phải json",
                       content_type="application/json", headers=_auth())
    assert resp.status_code == 400


def test_transcripts_db_chet_tra_ok_false_kem_HTTP_200(monkeypatch, migrated_url):
    """ok:false + HTTP 200 = KS sống nhưng DB chết. Đúng thiết kế, không phải bug."""
    monkeypatch.setenv("KS_HTTP_TOKEN", TOKEN)
    monkeypatch.setenv("KS_DATABASE_URL", "postgresql://nobody@127.0.0.1:1/khong_co")
    app = create_app()
    with app.test_client() as c:
        resp = c.post("/transcripts", json={"session_ref": "s", "content": []}, headers=_auth())
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is False
    assert body["transcript_id"] is None
    assert body["error"]


def test_transcripts_KHONG_tu_trigger_extraction(client):
    """Chỉ ghi DB — không chạm LLM. Không có biến LLM nào được cấu hình ở đây."""
    import os

    import psycopg
    ref = f"s-{uuid.uuid4()}"
    client.post("/transcripts", json={"session_ref": ref, "content": []}, headers=_auth())
    with psycopg.connect(os.environ["KS_DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT status, attempts FROM ks.transcripts WHERE session_ref = %s", (ref,)
        )
        assert cur.fetchone() == ("pending", 0)
        cur.execute("SELECT count(*) FROM ks.extracted_concepts")
        assert cur.fetchone()[0] == 0


# ---------------------------------------------------------------- /ingest


def test_ingest_tao_node(client):
    resp = client.post("/ingest", json={"drafts": [_draft()]}, headers=_auth())
    assert resp.status_code == 200
    item = resp.get_json()["ingested"][0]
    assert item["created"] is True
    uuid.UUID(item["node_id"])


def test_ingest_tra_ve_candidates(client):
    client.post("/ingest", json={"drafts": [_draft("Định luật Ohm", "Vật lý")]}, headers=_auth())
    resp = client.post("/ingest",
                       json={"drafts": [_draft("Định luật Newton 2", "Vật lý")]},
                       headers=_auth())
    item = resp.get_json()["ingested"][0]
    assert item["created"] is True
    assert item["candidates"][0]["title"] == "Định luật Ohm"


def test_ingest_source_module_la_thao_tac_convert_kiem_validate(client):
    """SourceModule(value) — không có bước validate tách riêng."""
    resp = client.post("/ingest", json={"drafts": [_draft(source="horae")]}, headers=_auth())
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_draft"


def test_ingest_ALL_OR_NOTHING_mot_draft_loi_thi_khong_ghi_gi(client):
    import psycopg, os
    resp = client.post(
        "/ingest",
        json={"drafts": [_draft("Khái niệm hợp lệ"), _draft(source="module-lạ")]},
        headers=_auth(),
    )
    assert resp.status_code == 400
    with psycopg.connect(os.environ["KS_DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ks.nodes WHERE title = 'Khái niệm hợp lệ'")
        assert cur.fetchone()[0] == 0


def test_ingest_thieu_field_thi_400_kem_index(client):
    resp = client.post("/ingest", json={"drafts": [{"title": "X"}]}, headers=_auth())
    assert resp.status_code == 400
    assert "draft[0]" in resp.get_json()["detail"]


def test_ingest_drafts_khong_phai_mang_thi_400(client):
    resp = client.post("/ingest", json={"drafts": "không phải mảng"}, headers=_auth())
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_drafts"


def test_ingest_mang_rong_hop_le(client):
    resp = client.post("/ingest", json={"drafts": []}, headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json()["ingested"] == []


def test_ingest_retry_nguyen_van_title_thi_an_toan(client):
    """Idempotency là fuzzy match theo similarity, KHÔNG phải khoá định danh."""
    payload = {"drafts": [_draft("Quang hợp ở thực vật C4")]}
    first = client.post("/ingest", json=payload, headers=_auth()).get_json()["ingested"][0]
    second = client.post("/ingest", json=payload, headers=_auth()).get_json()["ingested"][0]
    assert second["created"] is False
    assert second["node_id"] == first["node_id"]


def test_ingest_db_chet_tra_503_chu_khong_phai_500(monkeypatch):
    monkeypatch.setenv("KS_HTTP_TOKEN", TOKEN)
    monkeypatch.setenv("KS_DATABASE_URL", "postgresql://nobody@127.0.0.1:1/khong_co")
    app = create_app()
    with app.test_client() as c:
        resp = c.post("/ingest", json={"drafts": [_draft()]}, headers=_auth())
    assert resp.status_code == 503
    assert resp.get_json()["error"] == "database_unavailable"


def test_ingest_KHONG_cham_LLM(client, monkeypatch):
    """Route này không cần biến LLM nào — xoá sạch env LLM vẫn phải chạy được."""
    for var in ("KS_LLM_PROVIDER", "KS_LLM_MODEL", "KS_LLM_BASE_URL",
                "KS_LLM_API_KEY_ENV", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert client.post("/ingest", json={"drafts": [_draft()]}, headers=_auth()).status_code == 200


# ---------------------------------------------------------------- cấu hình token


def test_token_non_ascii_bi_chan_ngay_luc_khoi_dong():
    """Đo được bằng curl: token non-ASCII làm MỌI request 403 vĩnh viễn, vì WSGI
    giải mã header bằng latin-1. Chết lúc khởi động tốt hơn im lặng hỏng."""
    from ks.http_app import validate_token_config
    with pytest.raises(RuntimeError, match="ASCII"):
        validate_token_config({"KS_HTTP_TOKEN": "token-tiếng-việt"})


def test_thieu_token_thi_chet_luc_khoi_dong():
    from ks.http_app import validate_token_config
    with pytest.raises(RuntimeError, match="KS_HTTP_TOKEN"):
        validate_token_config({})


def test_token_ascii_hop_le_thi_qua():
    from ks.http_app import validate_token_config
    assert validate_token_config({"KS_HTTP_TOKEN": "abc123"}) == "abc123"


# ---------------------------------------------------------------- GET /nodes


def _seed(client, *drafts):
    return client.post("/ingest", json={"drafts": list(drafts)}, headers=_auth())


def _nodes(client, qs=""):
    return client.get(f"/nodes{qs}", headers=_auth()).get_json()["nodes"]


def test_nodes_can_auth(client):
    assert client.get("/nodes").status_code == 403


def test_nodes_tra_ve_danh_sach(client):
    _seed(client, _draft("Quang hợp", "Sinh học", "Cây dùng ánh sáng."))
    resp = client.get("/nodes", headers=_auth())
    assert resp.status_code == 200
    nodes = resp.get_json()["nodes"]
    assert len(nodes) == 1
    assert set(nodes[0]) == {"id", "title", "subject", "summary"}


def test_nodes_KHONG_tra_edges(client):
    _seed(client, _draft("Quang hợp", "Sinh học"))
    assert "edges" not in _nodes(client)[0]


def test_nodes_loc_theo_subject(client):
    _seed(client, _draft("Quang hợp", "Sinh học"), _draft("Chiến tranh Lạnh", "Lịch sử"))
    nodes = client.get("/nodes?subject=Sinh học", headers=_auth()).get_json()["nodes"]
    assert [n["title"] for n in nodes] == ["Quang hợp"]


def test_nodes_loc_theo_source_module(client):
    _seed(client,
          _draft("Quang hợp", "Sinh học"),
          _draft("Từ vựng IELTS", "Tiếng Anh", source="lexiflash"))
    nodes = client.get("/nodes?source_module=lexiflash", headers=_auth()).get_json()["nodes"]
    assert [n["title"] for n in nodes] == ["Từ vựng IELTS"]


def test_nodes_source_module_la_thi_400(client):
    resp = client.get("/nodes?source_module=horae", headers=_auth())
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_source_module"


def test_nodes_limit_mac_dinh_50(client):
    from ks import settings
    assert settings.DEFAULT_NODE_LIMIT == 50


def test_nodes_limit_duoc_ton_trong(client):
    _seed(client, _draft("Quang hợp", "Sinh học"), _draft("Chiến tranh Lạnh", "Lịch sử"))
    nodes = client.get("/nodes?limit=1", headers=_auth()).get_json()["nodes"]
    assert len(nodes) == 1


def test_nodes_limit_bang_tran_500_van_hop_le(client):
    assert client.get("/nodes?limit=500", headers=_auth()).status_code == 200


def test_nodes_vuot_tran_thi_400_KHONG_am_tham_cat(client):
    """Client phải biết mình nhận thiếu."""
    resp = client.get("/nodes?limit=501", headers=_auth())
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error"] == "invalid_limit"
    assert "500" in body["detail"]


def test_nodes_limit_khong_phai_so_thi_400(client):
    resp = client.get("/nodes?limit=nhiều", headers=_auth())
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_limit"


def test_nodes_limit_khong_duong_thi_400(client):
    for bad in ("0", "-1"):
        assert client.get(f"/nodes?limit={bad}", headers=_auth()).status_code == 400


def test_nodes_merged_tra_ve_node_dich_va_dedup(client):
    import os

    import psycopg
    body = _seed(client,
                 _draft("Quang hợp", "Sinh học"),
                 _draft("Chiến tranh Lạnh", "Lịch sử")).get_json()["ingested"]
    src, dst = body[0]["node_id"], body[1]["node_id"]
    with psycopg.connect(os.environ["KS_DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE ks.nodes SET merged_into_id = %s WHERE id = %s", (dst, src))
        conn.commit()
    nodes = client.get("/nodes", headers=_auth()).get_json()["nodes"]
    assert [n["title"] for n in nodes] == ["Chiến tranh Lạnh"]


def test_nodes_db_chet_tra_503(monkeypatch):
    monkeypatch.setenv("KS_HTTP_TOKEN", TOKEN)
    monkeypatch.setenv("KS_DATABASE_URL", "postgresql://nobody@127.0.0.1:1/khong_co")
    app = create_app()
    with app.test_client() as c:
        assert c.get("/nodes", headers=_auth()).status_code == 503


def test_nodes_KHONG_cham_LLM(client, monkeypatch):
    for var in ("KS_LLM_PROVIDER", "KS_LLM_MODEL", "KS_LLM_BASE_URL",
                "KS_LLM_API_KEY_ENV", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert client.get("/nodes", headers=_auth()).status_code == 200


# ---------------------------------------------------------------- GET /nodes/{id}


def test_node_by_id_can_auth(client):
    import uuid
    assert client.get(f"/nodes/{uuid.uuid4()}").status_code == 403


def test_node_by_id_tra_ve_mot_object_khong_boc_them(client):
    """Shape đúng NodeSummary, trả thẳng object — không bọc trong {"node": ...}."""
    nid = _seed(client, _draft("Quang hợp", "Sinh học", "Cây dùng ánh sáng.")
                ).get_json()["ingested"][0]["node_id"]
    resp = client.get(f"/nodes/{nid}", headers=_auth())
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body) == {"id", "title", "subject", "summary"}
    assert body["id"] == nid
    assert body["title"] == "Quang hợp"


def test_node_by_id_khong_ton_tai_thi_404(client):
    import uuid
    resp = client.get(f"/nodes/{uuid.uuid4()}", headers=_auth())
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "node_not_found"


def test_node_by_id_khong_phai_uuid_thi_400(client):
    resp = client.get("/nodes/không-phải-uuid", headers=_auth())
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_node_id"


def test_node_by_id_da_merge_tra_node_dich_200_khong_404(client):
    """Nhất quán với GET /nodes. Client lưu ý: id trả về KHÁC id đã hỏi."""
    import os

    import psycopg
    body = _seed(client,
                 _draft("Quang hợp", "Sinh học"),
                 _draft("Chiến tranh Lạnh", "Lịch sử")).get_json()["ingested"]
    src, dst = body[0]["node_id"], body[1]["node_id"]
    with psycopg.connect(os.environ["KS_DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE ks.nodes SET merged_into_id = %s WHERE id = %s", (dst, src))
        conn.commit()
    resp = client.get(f"/nodes/{src}", headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json()["id"] == dst
    assert resp.get_json()["title"] == "Chiến tranh Lạnh"


def test_node_by_id_khai_niem_chua_accept_thi_404(client):
    """Brief nói '404 khi node pending_review'. Thực tế ks.nodes KHÔNG có cột
    status — khái niệm chưa accept chưa hề là node, nên 404 tự nhiên."""
    import json
    import os
    import uuid

    import psycopg
    with psycopg.connect(os.environ["KS_DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ks.transcripts (session_ref, content) VALUES (%s, '[]') RETURNING id",
                (f"s-{uuid.uuid4()}",),
            )
            tid = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO ks.extracted_concepts"
                " (transcript_id, title, subject, summary, source_module)"
                " VALUES (%s, 'Chưa duyệt', 'Vật lý', 'x', 'mnemosyne') RETURNING id",
                (tid,),
            )
            concept_id = cur.fetchone()[0]
        conn.commit()
    assert client.get(f"/nodes/{concept_id}", headers=_auth()).status_code == 404


def test_node_by_id_db_chet_tra_503(monkeypatch):
    import uuid
    monkeypatch.setenv("KS_HTTP_TOKEN", TOKEN)
    monkeypatch.setenv("KS_DATABASE_URL", "postgresql://nobody@127.0.0.1:1/khong_co")
    app = create_app()
    with app.test_client() as c:
        assert c.get(f"/nodes/{uuid.uuid4()}", headers=_auth()).status_code == 503


def test_node_by_id_KHONG_cham_LLM(client, monkeypatch):
    nid = _seed(client, _draft("Quang hợp", "Sinh học")).get_json()["ingested"][0]["node_id"]
    for var in ("KS_LLM_PROVIDER", "KS_LLM_MODEL", "KS_LLM_BASE_URL",
                "KS_LLM_API_KEY_ENV", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert client.get(f"/nodes/{nid}", headers=_auth()).status_code == 200
