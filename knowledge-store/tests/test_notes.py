"""Scanned notes: OCR → correct → extract concepts → review. OCR and LLM are fakes; the DB is real."""

from __future__ import annotations

import io
import json
import uuid

import pytest

from ks import confirm, notes
from ks.http_app import create_app
from ks.llm import LLMTransientError
from ks.models import SourceModule
from ks.ocr_client import OcrError, Upload, _multipart
from ks.transcripts import build_prompt, is_note

from tests.test_edges import FakeProvider

TOKEN = "real-test-token"

PAGES = [
    {"source": "vo-ly.jpg", "number": 1, "text": "Từ thông\nΦ = B·S·cosα", "lines": [], "mean_confidence": 0.93,
     "low_confidence_count": 0},
    {"source": "vo-ly.jpg", "number": 2, "text": "Định luật Lenz", "lines": [], "mean_confidence": 0.71,
     "low_confidence_count": 1},
]

CONCEPTS = json.dumps([
    {"title": "Từ thông", "subject": "Vật lý", "summary": "Φ = B·S·cosα."},
    {"title": "Định luật Lenz", "subject": "Vật lý", "summary": "Dòng cảm ứng chống lại biến thiên từ thông."},
])


class FakeOcr:
    def __init__(self, pages=PAGES, error: Exception | None = None):
        self.pages = pages
        self.error = error
        self.calls: list[list[Upload]] = []

    def recognise(self, uploads):
        self.calls.append(uploads)
        if self.error:
            raise self.error
        return {"pages": self.pages, "page_count": len(self.pages), "elapsed_ms": 1}


def upload(name="vo-ly.jpg"):
    return Upload(name, "image/jpeg", b"\xff\xd8fake")


# ---------------------------------------------------------------- notes module


def test_ocr_gives_a_draft_note_with_pages_joined_by_blank_lines(conn):
    note = notes.create_from_uploads(conn, [upload()], FakeOcr())
    assert note.status == "draft"
    assert note.text == "Từ thông\nΦ = B·S·cosα\n\nĐịnh luật Lenz"
    assert note.title == "vo-ly", "without a title, the file name is used"
    assert note.ocr_pages == PAGES, "the raw OCR result is kept to compare with the corrected text"


def test_several_files_give_a_default_title_with_the_file_count(conn):
    note = notes.create_from_uploads(conn, [upload("a.jpg"), upload("b.png")], FakeOcr())
    assert note.title == "a (+1 file)"


def test_edit_text_and_title(conn):
    note = notes.create_from_uploads(conn, [upload()], FakeOcr())
    edited = notes.update(conn, note.id, title="Chương 5", text="Từ thông Φ = B·S·cosα")
    assert (edited.title, edited.text) == ("Chương 5", "Từ thông Φ = B·S·cosα")


def test_empty_title_keeps_the_old_title(conn):
    note = notes.create_from_uploads(conn, [upload()], FakeOcr())
    assert notes.update(conn, note.id, title="   ", text=None).title == note.title


def test_extraction_goes_through_the_review_queue_not_straight_into_nodes(conn):
    note = notes.create_from_uploads(conn, [upload()], FakeOcr())
    result = notes.extract(conn, note.id, FakeProvider(CONCEPTS))

    assert result.ok and len(result.concepts) == 2
    assert all(c.status == "pending_review" for c in result.concepts)
    assert all(c.source_module == SourceModule.NOTE_SCAN for c in result.concepts), \
        "concepts from notes must keep a recognisable source"
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ks.nodes")
        assert cur.fetchone()[0] == 0, "no node exists before accept"
    assert notes.get(conn, note.id).status == "extracted"


def test_llm_reads_the_corrected_text_not_the_ocr_text(conn):
    note = notes.create_from_uploads(conn, [upload()], FakeOcr())
    notes.update(conn, note.id, title=None, text="Văn bản đã sửa tay")
    provider = FakeProvider(CONCEPTS)
    notes.extract(conn, note.id, provider)
    prompt = provider.calls[0][1].content
    assert "Văn bản đã sửa tay" in prompt
    assert "Φ = B·S·cosα" not in prompt


def test_re_extracting_keeps_decided_concepts_and_replaces_pending_ones(conn):
    note = notes.create_from_uploads(conn, [upload()], FakeOcr())
    first = notes.extract(conn, note.id, FakeProvider(CONCEPTS))
    kept = first.concepts[0]
    confirm.accept(conn, kept.id)

    second = notes.extract(conn, note.id, FakeProvider(CONCEPTS))
    assert second.ok
    statuses = {c["id"]: c["status"] for c in notes.concepts_for(conn, note.id)}
    assert statuses[str(kept.id)] == "accepted", "re-extracting must not ask an answered question again"
    assert notes.get(conn, note.id).transcript_id == first.transcript_id, "reuses the old transcript"


def test_re_extracting_does_not_die_on_the_old_versions_attempts(conn):
    note = notes.create_from_uploads(conn, [upload()], FakeOcr())
    for _ in range(6):  # more than MAX_EXTRACTION_ATTEMPTS
        notes.extract(conn, note.id, FakeProvider(error=LLMTransientError("timeout")))
    assert notes.extract(conn, note.id, FakeProvider(CONCEPTS)).ok


def test_empty_note_does_not_call_the_llm(conn):
    note = notes.create_from_uploads(conn, [upload()], FakeOcr(pages=[]))
    provider = FakeProvider(CONCEPTS)
    with pytest.raises(notes.EmptyNote):
        notes.extract(conn, note.id, provider)
    assert provider.calls == []


def test_missing_note(conn):
    with pytest.raises(notes.NoteNotFound):
        notes.get(conn, uuid.uuid4())


# ---------------------------------------------------------------- prompt


def test_note_prompt_differs_from_session_prompt():
    note_content = {"kind": "note", "title": "Chương 5", "text": "Từ thông"}
    assert is_note(note_content) and not is_note([{"role": "user", "content": "x"}])
    note_prompt = build_prompt(note_content)[1].content
    session_prompt = build_prompt([{"role": "user", "content": "x"}])[1].content
    assert "NOTES: Chương 5" in note_prompt
    assert "recognition errors" in note_prompt, "the LLM must know the text may still contain OCR errors"
    assert "STUDY SESSION TRANSCRIPT" in session_prompt, "the session prompt is unchanged"


def test_multipart_keeps_non_ascii_file_names():
    body, content_type = _multipart([Upload("vở lý.jpg", "image/jpeg", b"abc")])
    assert content_type.startswith("multipart/form-data; boundary=")
    assert b"filename*=UTF-8''v%E1%BB%9F%20l%C3%BD.jpg" in body


def test_confirm_edit_concept_before_accept(conn):
    note = notes.create_from_uploads(conn, [upload()], FakeOcr())
    concept = notes.extract(conn, note.id, FakeProvider(CONCEPTS)).concepts[0]
    edited = confirm.edit_pending(conn, concept.id, title="Từ thông (sửa)")
    assert edited.title == "Từ thông (sửa)"
    item = confirm.accept(conn, concept.id)
    with conn.cursor() as cur:
        cur.execute("SELECT title FROM ks.nodes WHERE id = %s", (item.node_id,))
        assert cur.fetchone()[0] == "Từ thông (sửa)", "the node carries the corrected version"
    with pytest.raises(confirm.AlreadyDecided):
        confirm.edit_pending(conn, concept.id, title="muộn rồi")


# ---------------------------------------------------------------- HTTP


@pytest.fixture
def make_client(monkeypatch, migrated_url):
    monkeypatch.setenv("KS_HTTP_TOKEN", TOKEN)
    monkeypatch.setenv("KS_DATABASE_URL", migrated_url)

    def _make(ocr=None, provider=None):
        app = create_app(
            ocr_factory=lambda: ocr if ocr is not None else FakeOcr(),
            provider_factory=lambda: provider if provider is not None else FakeProvider(CONCEPTS),
        )
        app.config["TESTING"] = True
        return app.test_client()

    return _make


def _auth():
    return {"Authorization": f"Bearer {TOKEN}"}


def _post_note(client, name="vở lý.jpg"):
    return client.post(
        "/notes",
        data={"files": (io.BytesIO(b"\xff\xd8fake"), name), "title": "Chương 5"},
        headers=_auth(),
        content_type="multipart/form-data",
    )


def test_http_full_flow_from_upload_to_node(make_client):
    client = make_client()
    resp = _post_note(client)
    assert resp.status_code == 201
    note = resp.get_json()
    assert note["title"] == "Chương 5" and note["status"] == "draft" and note["page_count"] == 2

    assert client.patch(f"/notes/{note['id']}", json={"text": "Từ thông Φ = B·S·cosα"},
                        headers=_auth()).status_code == 200

    extracted = client.post(f"/notes/{note['id']}/extract", headers=_auth())
    assert extracted.status_code == 200
    concepts = extracted.get_json()["concepts"]
    assert [c["status"] for c in concepts] == ["pending_review", "pending_review"]

    pending = client.get("/extracted", headers=_auth()).get_json()["concepts"]
    assert {c["source_module"] for c in pending} == {"note_scan"}

    accepted = client.post(f"/extracted/{concepts[0]['id']}/accept", headers=_auth())
    assert accepted.status_code == 200 and accepted.get_json()["created"] is True
    assert client.post(f"/extracted/{concepts[1]['id']}/discard", headers=_auth()).status_code == 200
    assert client.post(f"/extracted/{concepts[1]['id']}/accept", headers=_auth()).status_code == 409

    detail = client.get(f"/notes/{note['id']}", headers=_auth()).get_json()
    assert sorted(c["status"] for c in detail["concepts"]) == ["accepted", "discarded"]


def test_http_needs_a_token(make_client):
    assert make_client().post("/notes").status_code == 403


def test_http_no_files(make_client):
    resp = make_client().post("/notes", data={}, headers=_auth(), content_type="multipart/form-data")
    assert resp.status_code == 400


@pytest.mark.parametrize("error, status", [
    (OcrError("Only images or PDFs are accepted", status=400, code="invalid_document"), 400),
    (OcrError("Could not reach the OCR service", status=502, code="ocr_unreachable"), 502),
    (OcrError("The OCR model is loading", status=503, code="not_ready"), 503),
])
def test_http_ocr_errors_are_passed_on_with_the_right_code(make_client, error, status):
    resp = _post_note(make_client(ocr=FakeOcr(error=error)))
    assert resp.status_code == status
    assert resp.get_json()["detail"] == str(error)


def test_http_ocr_not_configured_gives_503_and_ks_stays_up(monkeypatch, migrated_url):
    monkeypatch.setenv("KS_HTTP_TOKEN", TOKEN)
    monkeypatch.setenv("KS_DATABASE_URL", migrated_url)
    monkeypatch.delenv("KS_OCR_URL", raising=False)
    client = create_app().test_client()
    assert _post_note(client).status_code == 503
    assert client.get("/health").status_code == 200


def test_http_llm_error_gives_502_with_the_cause(make_client):
    client = make_client(provider=FakeProvider(error=LLMTransientError("DeepSeek timeout")))
    note = _post_note(client).get_json()
    resp = client.post(f"/notes/{note['id']}/extract", headers=_auth())
    assert resp.status_code == 502
    assert "DeepSeek timeout" in resp.get_json()["detail"]


def test_http_edit_concept_then_accept(make_client):
    client = make_client()
    note = _post_note(client).get_json()
    concept = client.post(f"/notes/{note['id']}/extract", headers=_auth()).get_json()["concepts"][0]
    resp = client.patch(f"/extracted/{concept['id']}", json={"summary": "Đã sửa"}, headers=_auth())
    assert resp.status_code == 200 and resp.get_json()["summary"] == "Đã sửa"
    assert client.patch(f"/extracted/{concept['id']}", json={"title": "  "}, headers=_auth()).status_code == 400


def test_http_malformed_and_missing_ids(make_client):
    client = make_client()
    assert client.get("/notes/khong-phai-uuid", headers=_auth()).status_code == 400
    assert client.get(f"/notes/{uuid.uuid4()}", headers=_auth()).status_code == 404
    assert client.post(f"/extracted/{uuid.uuid4()}/accept", headers=_auth()).status_code == 404
