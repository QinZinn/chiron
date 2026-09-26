"""Scanned notes: image/PDF → OCR → learner corrects → extract concepts → review.

There is no shortcut into ks.nodes. `extract` stores the corrected text as a
transcript with kind='note' and calls the same extract_concepts that study-session
transcripts use, so results land in ks.extracted_concepts as 'pending_review' and
only become nodes when the learner accepts them (ks/confirm.py). The two checks —
correcting the OCR text, then reviewing each concept — are deliberate, not
redundant: neither OCR misreadings nor LLM misunderstandings go straight into KS.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg

from ks.llm import LLMProvider
from ks.models import ExtractionResult
from ks.ocr_client import OcrClient, Upload
from ks.transcripts import extract_concepts

MAX_TITLE_CHARS = 200


class NoteNotFound(Exception):
    pass


class EmptyNote(ValueError):
    """No text left to extract — OCR read nothing, or the learner deleted it all."""


@dataclass(frozen=True)
class Note:
    id: UUID
    title: str
    filenames: tuple[str, ...]
    ocr_pages: list[dict[str, Any]]
    text: str
    status: str
    transcript_id: UUID | None
    created_at: datetime
    updated_at: datetime

    def as_dict(self, *, with_pages: bool = True) -> dict[str, Any]:
        out = {
            "id": str(self.id),
            "title": self.title,
            "filenames": list(self.filenames),
            "text": self.text,
            "status": self.status,
            "transcript_id": str(self.transcript_id) if self.transcript_id else None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "page_count": len(self.ocr_pages),
        }
        if with_pages:
            out["ocr_pages"] = self.ocr_pages
        return out


_COLUMNS = "id, title, filenames, ocr_pages, text, status, transcript_id, created_at, updated_at"


def _row(row) -> Note:
    return Note(
        id=row[0],
        title=row[1],
        filenames=tuple(row[2]),
        ocr_pages=row[3],
        text=row[4],
        status=row[5],
        transcript_id=row[6],
        created_at=row[7],
        updated_at=row[8],
    )


def _default_title(filenames: list[str]) -> str:
    first = filenames[0] if filenames else "Notes"
    stem = first.rsplit(".", 1)[0] or "Notes"
    return stem if len(filenames) == 1 else f"{stem} (+{len(filenames) - 1} file{'s' if len(filenames) > 2 else ''})"


def join_pages(pages: list[dict[str, Any]]) -> str:
    """Initial text for the learner to correct. Pages are separated by a blank line
    so the LLM does not join the last sentence of one page to the first of the next."""
    return "\n\n".join(p.get("text", "").strip() for p in pages if p.get("text", "").strip())


def create_from_uploads(
    conn: psycopg.Connection,
    uploads: list[Upload],
    ocr: OcrClient,
    *,
    title: str | None = None,
) -> Note:
    """OCR, then store the note as 'draft'. OcrError propagates to the HTTP layer."""
    result = ocr.recognise(uploads)
    pages = result.get("pages", [])
    filenames = [u.filename for u in uploads]
    clean_title = (title or "").strip()[:MAX_TITLE_CHARS] or _default_title(filenames)
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO ks.notes (title, filenames, ocr_pages, text)"
            f" VALUES (%s, %s, %s, %s) RETURNING {_COLUMNS}",
            (clean_title, filenames, json.dumps(pages, ensure_ascii=False), join_pages(pages)),
        )
        return _row(cur.fetchone())


def get(conn: psycopg.Connection, note_id: UUID) -> Note:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_COLUMNS} FROM ks.notes WHERE id = %s", (note_id,))
        row = cur.fetchone()
    if row is None:
        raise NoteNotFound(f"No note {note_id}")
    return _row(row)


def list_notes(conn: psycopg.Connection, *, limit: int = 50) -> list[Note]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_COLUMNS} FROM ks.notes ORDER BY created_at DESC LIMIT %s", (limit,))
        return [_row(r) for r in cur.fetchall()]


def update(conn: psycopg.Connection, note_id: UUID, *, title: str | None, text: str | None) -> Note:
    """Edit the title/text. Editing after extraction is fine — re-extracting uses the new text."""
    # A title that is empty after trimming counts as unchanged: a note without a
    # name cannot be found again in the list.
    new_title = title.strip()[:MAX_TITLE_CHARS] if title is not None else None
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE ks.notes SET title = COALESCE(%s, title), text = COALESCE(%s, text),"
            f" updated_at = now() WHERE id = %s RETURNING {_COLUMNS}",
            (new_title or None, text, note_id),
        )
        row = cur.fetchone()
    if row is None:
        raise NoteNotFound(f"No note {note_id}")
    return _row(row)


def extract(conn: psycopg.Connection, note_id: UUID, provider: LLMProvider) -> ExtractionResult:
    """Store the corrected text as a kind='note' transcript, then extract concepts.

    Re-extracting the same note reuses that transcript and overwrites its content
    with the current text: extract_concepts only clears old 'pending_review'
    results, so whatever the learner accepted/discarded last time is kept.
    """
    note = get(conn, note_id)
    if not note.text.strip():
        raise EmptyNote("The note has no text left to extract concepts from")
    content = json.dumps({"kind": "note", "note_id": str(note.id), "title": note.title, "text": note.text},
                         ensure_ascii=False)

    with conn.cursor() as cur:
        if note.transcript_id is None:
            cur.execute(
                "INSERT INTO ks.transcripts (session_ref, content) VALUES (%s, %s) RETURNING id",
                (f"note:{note.id}", content),
            )
            transcript_id = cur.fetchone()[0]
            cur.execute(
                "UPDATE ks.notes SET transcript_id = %s, updated_at = now() WHERE id = %s",
                (transcript_id, note.id),
            )
        else:
            transcript_id = note.transcript_id
            # attempts back to 0: this is a fresh extraction the learner asked for,
            # not a job retry — it must not die on the old version's attempts.
            cur.execute(
                "UPDATE ks.transcripts SET content = %s, status = 'pending', attempts = 0,"
                " last_error = NULL, updated_at = now() WHERE id = %s",
                (content, transcript_id),
            )

    result = extract_concepts(conn, transcript_id, provider)
    if result.ok:
        with conn.cursor() as cur:
            cur.execute("UPDATE ks.notes SET status = 'extracted', updated_at = now() WHERE id = %s", (note.id,))
    return result


def concepts_for(conn: psycopg.Connection, note_id: UUID) -> list[dict[str, Any]]:
    """Concepts extracted from this note, in every status — so the review screen also sees decided ones."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT c.id, c.title, c.subject, c.summary, c.status, c.node_id"
            " FROM ks.extracted_concepts c JOIN ks.notes n ON n.transcript_id = c.transcript_id"
            " WHERE n.id = %s ORDER BY c.created_at",
            (note_id,),
        )
        return [
            {"id": str(r[0]), "title": r[1], "subject": r[2], "summary": r[3], "status": r[4],
             "node_id": str(r[5]) if r[5] else None}
            for r in cur.fetchall()
        ]
