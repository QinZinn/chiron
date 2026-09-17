"""Ghi chép scan: ảnh/PDF → OCR → người học sửa → rút khái niệm → duyệt.

Không có đường tắt vào ks.nodes. `extract` lưu văn bản đã sửa thành một
transcript kind='note' rồi gọi đúng extract_concepts mà transcript phiên học
dùng, nên kết quả rơi vào ks.extracted_concepts ở trạng thái 'pending_review' và
chỉ thành node khi người học accept (ks/confirm.py). Hai lớp kiểm tra — sửa text
OCR, rồi duyệt từng khái niệm — là chủ đích, không phải thừa: OCR sai dấu tiếng
Việt và LLM hiểu sai đều không được lọt thẳng vào KS.
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
    """Không còn chữ nào để rút — OCR không đọc được gì, hoặc người học xoá hết."""


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
    first = filenames[0] if filenames else "Ghi chép"
    stem = first.rsplit(".", 1)[0] or "Ghi chép"
    return stem if len(filenames) == 1 else f"{stem} (+{len(filenames) - 1} tệp)"


def join_pages(pages: list[dict[str, Any]]) -> str:
    """Văn bản khởi tạo cho người học sửa. Trang cách nhau bằng dòng trống để
    LLM không nối câu cuối trang này với câu đầu trang sau."""
    return "\n\n".join(p.get("text", "").strip() for p in pages if p.get("text", "").strip())


def create_from_uploads(
    conn: psycopg.Connection,
    uploads: list[Upload],
    ocr: OcrClient,
    *,
    title: str | None = None,
) -> Note:
    """OCR rồi lưu note ở trạng thái 'draft'. OcrError văng ra cho HTTP layer."""
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
        raise NoteNotFound(f"Không có ghi chép {note_id}")
    return _row(row)


def list_notes(conn: psycopg.Connection, *, limit: int = 50) -> list[Note]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {_COLUMNS} FROM ks.notes ORDER BY created_at DESC LIMIT %s", (limit,))
        return [_row(r) for r in cur.fetchall()]


def update(conn: psycopg.Connection, note_id: UUID, *, title: str | None, text: str | None) -> Note:
    """Sửa tiêu đề/văn bản. Sửa sau khi đã rút vẫn được — rút lại sẽ dùng bản mới."""
    # Tiêu đề rỗng sau khi trim coi như không đổi: một note không có tên thì
    # không tìm lại được trong danh sách.
    new_title = title.strip()[:MAX_TITLE_CHARS] if title is not None else None
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE ks.notes SET title = COALESCE(%s, title), text = COALESCE(%s, text),"
            f" updated_at = now() WHERE id = %s RETURNING {_COLUMNS}",
            (new_title or None, text, note_id),
        )
        row = cur.fetchone()
    if row is None:
        raise NoteNotFound(f"Không có ghi chép {note_id}")
    return _row(row)


def extract(conn: psycopg.Connection, note_id: UUID, provider: LLMProvider) -> ExtractionResult:
    """Lưu văn bản đã sửa thành transcript kind='note' rồi rút khái niệm.

    Rút lại cùng một note dùng lại transcript đó và ghi đè content bằng văn bản
    hiện tại: extract_concepts chỉ dọn kết quả 'pending_review' cũ, nên thứ
    người học đã accept/discard ở lần trước được giữ nguyên.
    """
    note = get(conn, note_id)
    if not note.text.strip():
        raise EmptyNote("Ghi chép không còn chữ nào để rút khái niệm")
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
            # attempts về 0: đây là lần rút mới do người học yêu cầu, không phải
            # một lần retry của job — không được chết vì lượt thử của bản cũ.
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
    """Khái niệm đã rút từ note này, mọi trạng thái — để màn duyệt thấy cả thứ đã quyết."""
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
