"""Lưu transcript raw và job extraction (chạy riêng, retry được).

save_transcript KHÔNG BAO GIỜ raise — đối lập có chủ đích với ingest_concepts.
Phiên học không được hỏng chỉ vì KS chết; Mnemosyne là system of record và giữ
transcript của chính nó.
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
    "Bạn rút khái niệm học thuật từ bản ghi một phiên học. "
    "Chỉ những khái niệm học sinh THỰC SỰ đã học trong phiên. "
    "Chỉ trả JSON, không giải thích ngoài JSON."
)


class TranscriptNotFound(Exception):
    """transcript_id hoặc session_ref không tồn tại."""


# ---------------------------------------------------------------- save


def save_transcript(
    session_ref: str,
    content: Any,
    *,
    url: str | None = None,
) -> SaveResult:
    """Ghi transcript raw. **KHÔNG BAO GIỜ raise.**

    Tự mở connection và nuốt mọi lỗi, kể cả mất kết nối DB → SaveResult(ok=False).
    Idempotent thật theo session_ref: ON CONFLICT DO NOTHING rồi đọc lại id cũ,
    nên retry cùng session_ref an toàn tuyệt đối và KHÔNG đè content đã lưu.
    """
    try:
        # import cục bộ: settings.database_url() có thể raise, phải nằm trong try
        from ks.db import connect

        with connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO ks.transcripts (session_ref, content)"
                    " VALUES (%s, %s) ON CONFLICT (session_ref) DO NOTHING RETURNING id",
                    (session_ref, json.dumps(content, ensure_ascii=False)),
                )
                row = cur.fetchone()
                if row is None:  # đã có từ trước — trả lại id cũ, không đè
                    cur.execute(
                        "SELECT id FROM ks.transcripts WHERE session_ref = %s", (session_ref,)
                    )
                    row = cur.fetchone()
                transcript_id = row[0]
            conn.commit()
        return SaveResult(ok=True, transcript_id=transcript_id, error=None)
    except BaseException as exc:  # noqa: BLE001 — có chủ đích, xem docstring
        return SaveResult(ok=False, transcript_id=None, error=f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------- render


def render_transcript(content: Any) -> str:
    """Dựng text cho prompt.

    Nhận diện dạng [{"role":..., "content":...}] — dạng Mnemosyne gửi. Dạng khác
    thì dump JSON thô, vẫn dùng được chứ không vứt dữ liệu đi.
    """
    if isinstance(content, list) and all(
        isinstance(m, dict) and "role" in m and "content" in m for m in content
    ):
        return "\n".join(f"{m['role']}: {m['content']}" for m in content)
    return json.dumps(content, ensure_ascii=False, indent=2)


def build_prompt(content: Any) -> list[Message]:
    body = "\n".join([
        "BẢN GHI PHIÊN HỌC:",
        render_transcript(content),
        "",
        "Trả về JSON array. Mỗi phần tử:",
        '{"title": "<tên khái niệm>", "subject": "<môn học>", "summary": "<1-2 câu>"}',
        "",
        "title là TÊN MỘT KHÁI NIỆM (ví dụ: 'Định luật Newton 2'), không phải tên phiên học.",
        "subject là chuỗi tự do, viết theo cách người học hay gọi.",
        "Không có khái niệm nào rõ ràng thì trả [].",
    ])
    return [Message("system", _SYSTEM_PROMPT), Message("user", body)]


def _strip_json_fence(text: str) -> str:
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else text.strip()


def parse_extraction(text: str) -> tuple[tuple[str, str, str], ...]:
    """Parse phản hồi → (title, subject, summary). Sai cấu trúc → LLMParseError.

    Phần tử thiếu field bị bỏ qua lặng lẽ — một dòng hỏng không huỷ cả lô.
    """
    try:
        parsed = json.loads(_strip_json_fence(text))
    except (json.JSONDecodeError, ValueError) as exc:
        raise LLMParseError(f"Phản hồi không phải JSON: {text[:200]}") from exc
    if not isinstance(parsed, list):
        raise LLMParseError(f"Phản hồi không phải JSON array: {text[:200]}")

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
    """Transcript chờ extract, còn lượt thử."""
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
        raise TranscriptNotFound(f"Không có transcript {transcript_id}")
    return row


def extract_concepts(
    conn: psycopg.Connection,
    transcript_id: UUID,
    provider: LLMProvider,
    *,
    max_attempts: int = settings.MAX_EXTRACTION_ATTEMPTS,
) -> ExtractionResult:
    """Rút khái niệm từ transcript → ks.extracted_concepts (chờ xác nhận).

    RETRY ĐƯỢC: mỗi lần chạy tăng `attempts`. Cạn lượt → status='failed'.
    Dọn kết quả cũ còn 'pending_review', GIỮ NGUYÊN thứ đã accepted/discarded —
    không hỏi lại câu người dùng đã trả lời.

    KHÔNG raise khi LLM lỗi: ghi last_error rồi trả về, để lần log này sống sót.
    Lỗi DB vẫn văng ra.
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
        # Dọn kết quả cũ CHƯA được xử lý. accepted/discarded giữ nguyên.
        cur.execute(
            "DELETE FROM ks.extracted_concepts"
            " WHERE transcript_id = %s AND status = 'pending_review'",
            (transcript_id,),
        )
        rows = []
        for title, subject, summary in parsed:
            cur.execute(
                "INSERT INTO ks.extracted_concepts"
                " (transcript_id, title, subject, summary, source_module)"
                " VALUES (%s, %s, %s, %s, 'mnemosyne')"
                " RETURNING id, transcript_id, title, subject, summary, source_module,"
                "           status, node_id",
                (transcript_id, title, subject, summary),
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
