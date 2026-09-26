"""save_transcript (never raises) + extract_concepts (a retryable job)."""

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
    """save_transcript opens its own connection, so the test uses the fixture's URL."""
    result = save_transcript(session_ref, content, url=conn.info.dsn)
    return result


# ---------------------------------------------------------------- save


def test_save_returns_ok_and_transcript_id(migrated_url):
    result = save_transcript(f"s-{uuid.uuid4()}", CONTENT, url=migrated_url)
    assert result.ok is True
    assert result.transcript_id is not None
    assert result.error is None


def test_save_NEVER_raises_when_the_db_is_down():
    """A study session must not break because KS is down."""
    result = save_transcript("s-x", CONTENT, url="postgresql://nobody@127.0.0.1:1/khong_co")
    assert result.ok is False
    assert result.transcript_id is None
    assert result.error


def test_save_does_not_raise_even_with_a_nonsense_url():
    result = save_transcript("s-x", CONTENT, url="this is not a dsn")
    assert result.ok is False


def test_save_does_not_raise_when_content_cannot_be_serialised():
    result = save_transcript("s-x", {"f": object()}, url="postgresql://x@127.0.0.1:1/y")
    assert result.ok is False


def test_save_is_truly_idempotent_on_session_ref(migrated_url):
    ref = f"s-{uuid.uuid4()}"
    first = save_transcript(ref, CONTENT, url=migrated_url)
    second = save_transcript(ref, CONTENT, url=migrated_url)
    assert second.ok is True
    assert second.transcript_id == first.transcript_id


def test_saving_again_does_NOT_overwrite_stored_content(migrated_url):
    ref = f"s-{uuid.uuid4()}"
    save_transcript(ref, CONTENT, url=migrated_url)
    save_transcript(ref, [{"role": "user", "content": "nội dung khác"}], url=migrated_url)
    import psycopg
    with psycopg.connect(migrated_url) as conn, conn.cursor() as cur:
        cur.execute("SELECT content FROM ks.transcripts WHERE session_ref = %s", (ref,))
        assert cur.fetchone()[0] == CONTENT


def test_save_accepts_any_JSON_content(migrated_url):
    for content in ({"a": 1}, [1, 2, 3], "chuỗi", 42, None):
        assert save_transcript(f"s-{uuid.uuid4()}", content, url=migrated_url).ok is True


# ---------------------------------------------------------------- render / parse


def test_render_recognises_the_role_content_shape():
    assert render_transcript(CONTENT) == (
        "assistant: Định luật Newton 2 nói gì?\nuser: F bằng m nhân a."
    )


def test_render_other_shapes_keep_the_data():
    out = render_transcript({"gì đó": "khác"})
    assert "gì đó" in out


def test_prompt_contains_the_transcript():
    prompt = build_prompt(CONTENT)[1].content
    assert "F bằng m nhân a." in prompt


def test_parse_skips_elements_missing_a_field():
    text = json.dumps([{"title": "X"}, {"title": "Y", "subject": "S", "summary": "M"}])
    assert parse_extraction(text) == (("Y", "S", "M"),)


def test_parse_non_json_gives_parse_error():
    with pytest.raises(LLMParseError):
        parse_extraction("sorry")


def test_parse_empty_array_is_valid():
    assert parse_extraction("[]") == ()


# ---------------------------------------------------------------- extract


def _new_transcript(conn, content=CONTENT):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ks.transcripts (session_ref, content) VALUES (%s, %s) RETURNING id",
            (f"s-{uuid.uuid4()}", json.dumps(content, ensure_ascii=False)),
        )
        return cur.fetchone()[0]


def test_extract_writes_concepts_awaiting_confirmation(conn):
    tid = _new_transcript(conn)
    result = extract_concepts(conn, tid, FakeProvider(TWO_CONCEPTS))
    assert result.ok is True
    assert len(result.concepts) == 2
    assert {c.status for c in result.concepts} == {"pending_review"}


def test_extract_does_NOT_write_to_nodes(conn):
    """Extraction only proposes; entering the graph is accept's job."""
    tid = _new_transcript(conn)
    extract_concepts(conn, tid, FakeProvider(TWO_CONCEPTS))
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ks.nodes")
        assert cur.fetchone()[0] == 0


def test_successful_extract_sets_status_done(conn):
    tid = _new_transcript(conn)
    extract_concepts(conn, tid, FakeProvider("[]"))
    with conn.cursor() as cur:
        cur.execute("SELECT status, attempts, last_error FROM ks.transcripts WHERE id = %s", (tid,))
        assert cur.fetchone() == ("done", 1, None)


def test_extract_llm_error_does_NOT_raise_and_increments_attempts(conn):
    tid = _new_transcript(conn)
    result = extract_concepts(conn, tid, FakeProvider(error=LLMTransientError("503")))
    assert result.ok is False and result.attempts == 1
    with conn.cursor() as cur:
        cur.execute("SELECT status, attempts, last_error FROM ks.transcripts WHERE id = %s", (tid,))
        status, attempts, error = cur.fetchone()
    assert (status, attempts) == ("pending", 1)
    assert "503" in error


def test_extract_out_of_attempts_sets_status_failed(conn):
    tid = _new_transcript(conn)
    for _ in range(3):
        extract_concepts(conn, tid, FakeProvider(error=LLMQuotaError("429")), max_attempts=3)
    with conn.cursor() as cur:
        cur.execute("SELECT status, attempts FROM ks.transcripts WHERE id = %s", (tid,))
        assert cur.fetchone() == ("failed", 3)


def test_retry_clears_old_results_still_pending_review(conn):
    tid = _new_transcript(conn)
    extract_concepts(conn, tid, FakeProvider(TWO_CONCEPTS))
    extract_concepts(conn, tid, FakeProvider(json.dumps(
        [{"title": "Chỉ một", "subject": "Toán", "summary": "m"}])))
    with conn.cursor() as cur:
        cur.execute("SELECT title FROM ks.extracted_concepts WHERE transcript_id = %s", (tid,))
        assert [r[0] for r in cur.fetchall()] == ["Chỉ một"]


def test_retry_KEEPS_what_was_accepted_or_discarded(conn):
    """Never ask again a question the user already answered."""
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
        # the enum sorts in declaration order: pending_review → accepted → discarded
        assert cur.fetchall() == [
            ("Mới toanh", "pending_review"),
            ("Định luật Newton 2", "accepted"),
            ("Quang hợp", "discarded"),
        ]


def test_extract_missing_transcript_raises(conn):
    with pytest.raises(TranscriptNotFound):
        extract_concepts(conn, uuid.uuid4(), FakeProvider())


def test_pending_transcripts_skip_done_ones(conn):
    a = _new_transcript(conn)
    b = _new_transcript(conn)
    extract_concepts(conn, a, FakeProvider("[]"))
    assert pending_transcripts(conn) == (b,)


def test_pending_transcripts_skip_ones_out_of_attempts(conn):
    tid = _new_transcript(conn)
    with conn.cursor() as cur:
        cur.execute("UPDATE ks.transcripts SET attempts = 5 WHERE id = %s", (tid,))
    assert pending_transcripts(conn, max_attempts=5) == ()
