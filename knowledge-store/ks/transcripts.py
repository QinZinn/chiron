"""Storing raw transcripts and the extraction job (runs separately, retryable).

save_transcript NEVER raises — deliberately the opposite of ingest_concepts.
A study session must not break just because KS is down; Mnemosyne is the system
of record and keeps its own transcript.
"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

import psycopg

from ks import settings
from ks.llm import LLMError, LLMParseError, LLMProvider, Message
from ks.models import ExtractedConcept, ExtractionResult, SaveResult, SourceModule

_SYSTEM_PROMPT = (
    "You extract academic concepts from the record of a study session. "
    "Only concepts the student ACTUALLY studied in the session. "
    "Write in English. Return only JSON, no explanation outside the JSON."
)


class TranscriptNotFound(Exception):
    """No transcript_id or session_ref with this value."""


# ---------------------------------------------------------------- save


def save_transcript(
    session_ref: str,
    content: Any,
    *,
    url: str | None = None,
) -> SaveResult:
    """Store a raw transcript. **NEVER raises.**

    Opens its own connection and swallows every error, including a lost DB
    connection → SaveResult(ok=False). Truly idempotent on session_ref: ON CONFLICT
    DO NOTHING, then read back the existing id, so retrying the same session_ref is
    completely safe and does NOT overwrite stored content.
    """
    try:
        # local import: settings.database_url() may raise, so it must be inside the try
        from ks.db import connect

        with connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO ks.transcripts (session_ref, content)"
                    " VALUES (%s, %s) ON CONFLICT (session_ref) DO NOTHING RETURNING id",
                    (session_ref, json.dumps(content, ensure_ascii=False)),
                )
                row = cur.fetchone()
                if row is None:  # already stored — return the existing id, do not overwrite
                    cur.execute(
                        "SELECT id FROM ks.transcripts WHERE session_ref = %s", (session_ref,)
                    )
                    row = cur.fetchone()
                transcript_id = row[0]
            conn.commit()
        return SaveResult(ok=True, transcript_id=transcript_id, error=None)
    except BaseException as exc:  # noqa: BLE001 — deliberate, see the docstring
        return SaveResult(ok=False, transcript_id=None, error=f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------- render


def render_transcript(content: Any) -> str:
    """Build the text for the prompt.

    Recognises the [{"role":..., "content":...}] shape — what Mnemosyne sends. Any
    other shape is dumped as raw JSON: still usable, no data thrown away.
    """
    if isinstance(content, list) and all(
        isinstance(m, dict) and "role" in m and "content" in m for m in content
    ):
        return "\n".join(f"{m['role']}: {m['content']}" for m in content)
    return json.dumps(content, ensure_ascii=False, indent=2)


_NOTE_SYSTEM_PROMPT = (
    "You extract academic concepts from a student's notes, digitised with OCR "
    "and then corrected by the student. Only concepts the notes ACTUALLY present. "
    "Write in English. Return only JSON, no explanation outside the JSON."
)


def is_note(content: Any) -> bool:
    """A transcript made from scanned notes (ks/notes.py) rather than a Mnemosyne study session."""
    return isinstance(content, dict) and content.get("kind") == "note"


def build_prompt(content: Any) -> list[Message]:
    if is_note(content):
        # A separate prompt: notes have no question/answer turns, and OCR may have
        # left spelling errors — the LLM must go by meaning, not copy errors into titles.
        body = "\n".join([
            f"NOTES: {content.get('title', '')}",
            str(content.get("text", "")),
            "",
            "Return a JSON array. Each element:",
            '{"title": "<concept name>", "subject": "<subject>", "summary": "<1-2 sentences>"}',
            "",
            "title is the NAME OF ONE CONCEPT (e.g. \"Newton's second law\"), not a page heading.",
            "The text may still contain recognition errors: spell title and summary correctly,"
            " but do NOT add knowledge the notes do not contain.",
            "summary states exactly what the notes say, with formulas if any.",
            "subject is free text, written the way the learner would name it.",
            "If there is no clear concept, return [].",
        ])
        return [Message("system", _NOTE_SYSTEM_PROMPT), Message("user", body)]

    body = "\n".join([
        "STUDY SESSION TRANSCRIPT:",
        render_transcript(content),
        "",
        "Return a JSON array. Each element:",
        '{"title": "<concept name>", "subject": "<subject>", "summary": "<1-2 sentences>"}',
        "",
        "title is the NAME OF ONE CONCEPT (e.g. \"Newton's second law\"), not the session name.",
        "subject is free text, written the way the learner would name it.",
        "If there is no clear concept, return [].",
    ])
    return [Message("system", _SYSTEM_PROMPT), Message("user", body)]


def _strip_json_fence(text: str) -> str:
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else text.strip()


def parse_extraction(text: str) -> tuple[tuple[str, str, str], ...]:
    """Parse the response → (title, subject, summary). Wrong structure → LLMParseError.

    Elements missing a field are silently skipped — one bad row does not void the batch.
    """
    try:
        parsed = json.loads(_strip_json_fence(text))
    except (json.JSONDecodeError, ValueError) as exc:
        raise LLMParseError(f"Response is not JSON: {text[:200]}") from exc
    if not isinstance(parsed, list):
        raise LLMParseError(f"Response is not a JSON array: {text[:200]}")

    out: list[tuple[str, str, str]] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title", "")).strip()
        subject = str(entry.get("subject", "")).strip()
        summary = str(entry.get("summary", "")).strip()
        if not (title and subject and summary):
            continue
        out.append((title, subject, summary))
    return tuple(out)


# ---------------------------------------------------------------- extract


def pending_transcripts(
    conn: psycopg.Connection,
    *,
    limit: int = 50,
    max_attempts: int = settings.MAX_EXTRACTION_ATTEMPTS,
) -> tuple[UUID, ...]:
    """Transcripts waiting for extraction that still have attempts left."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM ks.transcripts"
            " WHERE status = 'pending' AND attempts < %s"
            " ORDER BY created_at LIMIT %s",
            (max_attempts, limit),
        )
        return tuple(r[0] for r in cur.fetchall())


def _load_transcript(conn: psycopg.Connection, transcript_id: UUID):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT content, attempts FROM ks.transcripts WHERE id = %s",
            (transcript_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise TranscriptNotFound(f"No transcript {transcript_id}")
    return row


def extract_concepts(
    conn: psycopg.Connection,
    transcript_id: UUID,
    provider: LLMProvider,
    *,
    max_attempts: int = settings.MAX_EXTRACTION_ATTEMPTS,
) -> ExtractionResult:
    """Extract concepts from a transcript → ks.extracted_concepts (awaiting confirmation).

    RETRYABLE: every run increments `attempts`. Out of attempts → status='failed'.
    Clears old results still in 'pending_review', KEEPS whatever was accepted/discarded —
    a question the user already answered is not asked again.

    Does NOT raise on LLM errors: records last_error and returns, so this log survives.
    DB errors still propagate.
    """
    content, attempts = _load_transcript(conn, transcript_id)
    attempts += 1

    try:
        text = provider.complete(
            build_prompt(content), max_tokens=settings.EXTRACTION_MAX_TOKENS
        )
        parsed = parse_extraction(text)
    except LLMError as exc:
        status = "failed" if attempts >= max_attempts else "pending"
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ks.transcripts SET attempts = %s, last_error = %s,"
                " status = %s, updated_at = now() WHERE id = %s",
                (attempts, str(exc), status, transcript_id),
            )
        return ExtractionResult(transcript_id, (), False, attempts, str(exc))

    with conn.cursor() as cur:
        # Clear old results NOT yet handled. accepted/discarded stay as they are.
        cur.execute(
            "DELETE FROM ks.extracted_concepts"
            " WHERE transcript_id = %s AND status = 'pending_review'",
            (transcript_id,),
        )
        rows = []
        # The source follows the transcript kind: concepts extracted from scanned
        # notes must stay recognisable as notes, not blend into 'mnemosyne'.
        source = SourceModule.NOTE_SCAN if is_note(content) else SourceModule.MNEMOSYNE
        for title, subject, summary in parsed:
            cur.execute(
                "INSERT INTO ks.extracted_concepts"
                " (transcript_id, title, subject, summary, source_module)"
                " VALUES (%s, %s, %s, %s, %s)"
                " RETURNING id, transcript_id, title, subject, summary, source_module,"
                "           status, node_id",
                (transcript_id, title, subject, summary, source.value),
            )
            rows.append(cur.fetchone())
        cur.execute(
            "UPDATE ks.transcripts SET attempts = %s, last_error = NULL,"
            " status = 'done', updated_at = now() WHERE id = %s",
            (attempts, transcript_id),
        )

    concepts = tuple(_row_to_concept(r) for r in rows)
    return ExtractionResult(transcript_id, concepts, True, attempts, None)


def _row_to_concept(row) -> ExtractedConcept:
    return ExtractedConcept(
        id=row[0],
        transcript_id=row[1],
        title=row[2],
        subject=row[3],
        summary=row[4],
        source_module=SourceModule(row[5]),
        status=row[6],
        node_id=row[7],
    )
