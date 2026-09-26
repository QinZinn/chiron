"""HTTP layer: auth, /health, POST /transcripts, POST /ingest."""

from __future__ import annotations

import json
import uuid

import pytest

from ks.http_app import create_app

TOKEN = "real-test-token"


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


def test_health_needs_no_auth(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_missing_token_gives_403(client):
    assert client.post("/transcripts", json={}).status_code == 403


def test_wrong_token_gives_403(client):
    assert client.post("/transcripts", json={}, headers=_auth("sai")).status_code == 403


def test_header_without_Bearer_prefix_gives_403(client):
    resp = client.post("/transcripts", json={}, headers={"Authorization": TOKEN})
    assert resp.status_code == 403


def test_NON_ASCII_header_gives_403_and_does_NOT_crash_with_500(client):
    """A BUG WE HIT: hmac.compare_digest raises TypeError on a non-ASCII str → 500.
    Comparing bytes makes any malformed header just a 403."""
    for bad in ("Bearer đây-là-tiếng-việt", "Bearer 🔑🔑🔑", "Bearer ünïcödé"):
        resp = client.post("/transcripts", json={}, headers={"Authorization": bad})
        assert resp.status_code == 403, f"header {bad!r} must be 403"


def test_token_of_the_right_length_but_wrong_content_gives_403(client):
    assert client.post("/transcripts", json={}, headers=_auth("x" * len(TOKEN))).status_code == 403


def test_unconfigured_token_rejects_everything_rather_than_opening_wide(monkeypatch, migrated_url):
    monkeypatch.delenv("KS_HTTP_TOKEN", raising=False)
    monkeypatch.setenv("KS_DATABASE_URL", migrated_url)
    app = create_app()
    with app.test_client() as c:
        assert c.post("/ingest", json={"drafts": []}, headers=_auth("")).status_code == 403


# ---------------------------------------------------------------- /transcripts


def test_transcripts_write_succeeds(client):
    resp = client.post(
        "/transcripts",
        json={"session_ref": f"s-{uuid.uuid4()}", "content": [{"role": "user", "content": "hi"}]},
        headers=_auth(),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True and body["error"] is None
    uuid.UUID(body["transcript_id"])  # must parse


def test_transcripts_truly_idempotent_on_session_ref(client):
    ref = f"s-{uuid.uuid4()}"
    payload = {"session_ref": ref, "content": ["a"]}
    first = client.post("/transcripts", json=payload, headers=_auth()).get_json()
    second = client.post("/transcripts", json=payload, headers=_auth()).get_json()
    assert second["transcript_id"] == first["transcript_id"]


def test_transcripts_accept_any_JSON_content(client):
    for content in ({"a": 1}, [1, 2], "a string", 7, None):
        resp = client.post(
            "/transcripts",
            json={"session_ref": f"s-{uuid.uuid4()}", "content": content},
            headers=_auth(),
        )
        assert resp.get_json()["ok"] is True


def test_transcripts_missing_session_ref_gives_400(client):
    resp = client.post("/transcripts", json={"content": []}, headers=_auth())
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_session_ref"


def test_transcripts_missing_content_gives_400(client):
    resp = client.post("/transcripts", json={"session_ref": "s"}, headers=_auth())
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "missing_content"


def test_transcripts_non_json_body_gives_400(client):
    resp = client.post("/transcripts", data="not json",
                       content_type="application/json", headers=_auth())
    assert resp.status_code == 400


def test_transcripts_db_down_gives_ok_false_with_HTTP_200(monkeypatch, migrated_url):
    """ok:false + HTTP 200 = KS alive but the DB down. By design, not a bug."""
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


def test_transcripts_do_NOT_trigger_extraction(client):
    """DB write only — no LLM. No LLM variable is configured here."""
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


def test_ingest_creates_a_node(client):
    resp = client.post("/ingest", json={"drafts": [_draft()]}, headers=_auth())
    assert resp.status_code == 200
    item = resp.get_json()["ingested"][0]
    assert item["created"] is True
    uuid.UUID(item["node_id"])


def test_ingest_returns_candidates(client):
    client.post("/ingest", json={"drafts": [_draft("Định luật Ohm", "Vật lý")]}, headers=_auth())
    resp = client.post("/ingest",
                       json={"drafts": [_draft("Định luật Newton 2", "Vật lý")]},
                       headers=_auth())
    item = resp.get_json()["ingested"][0]
    assert item["created"] is True
    assert item["candidates"][0]["title"] == "Định luật Ohm"


def test_ingest_source_module_conversion_is_also_the_validation(client):
    """SourceModule(value) — there is no separate validation step."""
    resp = client.post("/ingest", json={"drafts": [_draft(source="horae")]}, headers=_auth())
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_draft"


def test_ingest_ALL_OR_NOTHING_one_bad_draft_writes_nothing(client):
    import psycopg, os
    resp = client.post(
        "/ingest",
        json={"drafts": [_draft("Khái niệm hợp lệ"), _draft(source="unknown-module")]},
        headers=_auth(),
    )
    assert resp.status_code == 400
    with psycopg.connect(os.environ["KS_DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ks.nodes WHERE title = 'Khái niệm hợp lệ'")
        assert cur.fetchone()[0] == 0


def test_ingest_missing_field_gives_400_with_the_index(client):
    resp = client.post("/ingest", json={"drafts": [{"title": "X"}]}, headers=_auth())
    assert resp.status_code == 400
    assert "draft[0]" in resp.get_json()["detail"]


def test_ingest_drafts_not_an_array_gives_400(client):
    resp = client.post("/ingest", json={"drafts": "not an array"}, headers=_auth())
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_drafts"


def test_ingest_empty_array_is_valid(client):
    resp = client.post("/ingest", json={"drafts": []}, headers=_auth())
    assert resp.status_code == 200
    assert resp.get_json()["ingested"] == []


def test_ingest_retry_with_the_verbatim_title_is_safe(client):
    """Idempotency is a fuzzy similarity match, NOT an identity key."""
    payload = {"drafts": [_draft("Quang hợp ở thực vật C4")]}
    first = client.post("/ingest", json=payload, headers=_auth()).get_json()["ingested"][0]
    second = client.post("/ingest", json=payload, headers=_auth()).get_json()["ingested"][0]
    assert second["created"] is False
    assert second["node_id"] == first["node_id"]


def test_ingest_db_down_gives_503_not_500(monkeypatch):
    monkeypatch.setenv("KS_HTTP_TOKEN", TOKEN)
    monkeypatch.setenv("KS_DATABASE_URL", "postgresql://nobody@127.0.0.1:1/khong_co")
    app = create_app()
    with app.test_client() as c:
        resp = c.post("/ingest", json={"drafts": [_draft()]}, headers=_auth())
    assert resp.status_code == 503
    assert resp.get_json()["error"] == "database_unavailable"


def test_ingest_does_NOT_touch_the_LLM(client, monkeypatch):
    """This route needs no LLM variable — it must work with every LLM env var removed."""
    for var in ("KS_LLM_PROVIDER", "KS_LLM_MODEL", "KS_LLM_BASE_URL",
                "KS_LLM_API_KEY_ENV", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert client.post("/ingest", json={"drafts": [_draft()]}, headers=_auth()).status_code == 200


# ---------------------------------------------------------------- token configuration


def test_non_ascii_token_is_rejected_at_startup():
    """Measured with curl: a non-ASCII token makes EVERY request 403 forever, because WSGI
    decodes headers as latin-1. Dying at startup beats failing silently."""
    from ks.http_app import validate_token_config
    with pytest.raises(RuntimeError, match="ASCII"):
        validate_token_config({"KS_HTTP_TOKEN": "token-tiếng-việt"})


def test_missing_token_dies_at_startup():
    from ks.http_app import validate_token_config
    with pytest.raises(RuntimeError, match="KS_HTTP_TOKEN"):
        validate_token_config({})


def test_valid_ascii_token_passes():
    from ks.http_app import validate_token_config
    assert validate_token_config({"KS_HTTP_TOKEN": "abc123"}) == "abc123"


# ---------------------------------------------------------------- GET /nodes


def _seed(client, *drafts):
    return client.post("/ingest", json={"drafts": list(drafts)}, headers=_auth())


def _nodes(client, qs=""):
    return client.get(f"/nodes{qs}", headers=_auth()).get_json()["nodes"]


def test_nodes_needs_auth(client):
    assert client.get("/nodes").status_code == 403


def test_nodes_returns_the_list(client):
    _seed(client, _draft("Quang hợp", "Sinh học", "Cây dùng ánh sáng."))
    resp = client.get("/nodes", headers=_auth())
    assert resp.status_code == 200
    nodes = resp.get_json()["nodes"]
    assert len(nodes) == 1
    assert set(nodes[0]) == {"id", "title", "subject", "summary"}


def test_nodes_does_NOT_return_edges(client):
    _seed(client, _draft("Quang hợp", "Sinh học"))
    assert "edges" not in _nodes(client)[0]


def test_nodes_filter_by_subject(client):
    _seed(client, _draft("Quang hợp", "Sinh học"), _draft("Chiến tranh Lạnh", "Lịch sử"))
    nodes = client.get("/nodes?subject=Sinh học", headers=_auth()).get_json()["nodes"]
    assert [n["title"] for n in nodes] == ["Quang hợp"]


def test_nodes_filter_by_source_module(client):
    _seed(client,
          _draft("Quang hợp", "Sinh học"),
          _draft("Từ vựng IELTS", "Tiếng Anh", source="lexiflash"))
    nodes = client.get("/nodes?source_module=lexiflash", headers=_auth()).get_json()["nodes"]
    assert [n["title"] for n in nodes] == ["Từ vựng IELTS"]


def test_nodes_unknown_source_module_gives_400(client):
    resp = client.get("/nodes?source_module=horae", headers=_auth())
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_source_module"


def test_nodes_limit_defaults_to_50(client):
    from ks import settings
    assert settings.DEFAULT_NODE_LIMIT == 50


def test_nodes_limit_is_respected(client):
    _seed(client, _draft("Quang hợp", "Sinh học"), _draft("Chiến tranh Lạnh", "Lịch sử"))
    nodes = client.get("/nodes?limit=1", headers=_auth()).get_json()["nodes"]
    assert len(nodes) == 1


def test_nodes_limit_equal_to_the_500_cap_is_valid(client):
    assert client.get("/nodes?limit=500", headers=_auth()).status_code == 200


def test_nodes_over_the_cap_gives_400_NOT_silent_truncation(client):
    """The client must know it got less."""
    resp = client.get("/nodes?limit=501", headers=_auth())
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error"] == "invalid_limit"
    assert "500" in body["detail"]


def test_nodes_non_numeric_limit_gives_400(client):
    resp = client.get("/nodes?limit=many", headers=_auth())
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_limit"


def test_nodes_non_positive_limit_gives_400(client):
    for bad in ("0", "-1"):
        assert client.get(f"/nodes?limit={bad}", headers=_auth()).status_code == 400


def test_nodes_merged_returns_the_target_and_dedups(client):
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


def test_nodes_db_down_gives_503(monkeypatch):
    monkeypatch.setenv("KS_HTTP_TOKEN", TOKEN)
    monkeypatch.setenv("KS_DATABASE_URL", "postgresql://nobody@127.0.0.1:1/khong_co")
    app = create_app()
    with app.test_client() as c:
        assert c.get("/nodes", headers=_auth()).status_code == 503


def test_nodes_does_NOT_touch_the_LLM(client, monkeypatch):
    for var in ("KS_LLM_PROVIDER", "KS_LLM_MODEL", "KS_LLM_BASE_URL",
                "KS_LLM_API_KEY_ENV", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert client.get("/nodes", headers=_auth()).status_code == 200


# ---------------------------------------------------------------- GET /nodes/{id}


def test_node_by_id_needs_auth(client):
    import uuid
    assert client.get(f"/nodes/{uuid.uuid4()}").status_code == 403


def test_node_by_id_returns_a_bare_object(client):
    """Exactly the NodeSummary shape, returned as the object itself — not wrapped in {"node": ...}."""
    nid = _seed(client, _draft("Quang hợp", "Sinh học", "Cây dùng ánh sáng.")
                ).get_json()["ingested"][0]["node_id"]
    resp = client.get(f"/nodes/{nid}", headers=_auth())
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body) == {"id", "title", "subject", "summary"}
    assert body["id"] == nid
    assert body["title"] == "Quang hợp"


def test_node_by_id_missing_gives_404(client):
    import uuid
    resp = client.get(f"/nodes/{uuid.uuid4()}", headers=_auth())
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "node_not_found"


def test_node_by_id_not_a_uuid_gives_400(client):
    resp = client.get("/nodes/not-a-uuid", headers=_auth())
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_node_id"


def test_node_by_id_merged_gives_the_target_with_200_not_404(client):
    """Consistent with GET /nodes. Note for clients: the returned id DIFFERS from the one requested."""
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


def test_node_by_id_unaccepted_concept_gives_404(client):
    """The brief says '404 for a pending_review node'. In fact ks.nodes has NO status
    column — an unaccepted concept is not a node at all, so the 404 comes naturally."""
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


def test_node_by_id_db_down_gives_503(monkeypatch):
    import uuid
    monkeypatch.setenv("KS_HTTP_TOKEN", TOKEN)
    monkeypatch.setenv("KS_DATABASE_URL", "postgresql://nobody@127.0.0.1:1/khong_co")
    app = create_app()
    with app.test_client() as c:
        assert c.get(f"/nodes/{uuid.uuid4()}", headers=_auth()).status_code == 503


def test_node_by_id_does_NOT_touch_the_LLM(client, monkeypatch):
    nid = _seed(client, _draft("Quang hợp", "Sinh học")).get_json()["ingested"][0]["node_id"]
    for var in ("KS_LLM_PROVIDER", "KS_LLM_MODEL", "KS_LLM_BASE_URL",
                "KS_LLM_API_KEY_ENV", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert client.get(f"/nodes/{nid}", headers=_auth()).status_code == 200
