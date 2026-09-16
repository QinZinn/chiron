"""Xác nhận khái niệm đã rút: list / accept / discard.

accept đẩy khái niệm qua ingest_concepts, nên nó chịu ĐÚNG luật dò trùng và
instrumentation như mọi đường ghi khác — không có cửa sau.
discard giữ row status='discarded', KHÔNG xoá — cùng logic với edge 'rejected'.
"""

from __future__ import annotations

from uuid import UUID

import psycopg

from ks.ingest import ingest_concepts
from ks.models import ConceptDraft, ExtractedConcept, IngestedConcept, SourceModule
from ks.transcripts import _row_to_concept

_SELECT = (
    "SELECT id, transcript_id, title, subject, summary, source_module, status, node_id"
    " FROM ks.extracted_concepts"
)


class ExtractedConceptNotFound(Exception):
    """extracted_concept id không tồn tại."""


class AlreadyDecided(Exception):
    """Khái niệm đã accepted/discarded — không hỏi lại câu đã trả lời."""


def list_extracted(
    conn: psycopg.Connection,
    *,
    status: str = "pending_review",
    limit: int = 100,
) -> tuple[ExtractedConcept, ...]:
    with conn.cursor() as cur:
        cur.execute(
            f"{_SELECT} WHERE status = %s ORDER BY created_at LIMIT %s",
            (status, limit),
        )
        return tuple(_row_to_concept(r) for r in cur.fetchall())


def _load(conn: psycopg.Connection, concept_id: UUID) -> ExtractedConcept:
    with conn.cursor() as cur:
        cur.execute(f"{_SELECT} WHERE id = %s", (concept_id,))
        row = cur.fetchone()
    if row is None:
        raise ExtractedConceptNotFound(f"Không có extracted_concept {concept_id}")
    return _row_to_concept(row)


def accept(conn: psycopg.Connection, concept_id: UUID) -> IngestedConcept:
    """Ghi khái niệm vào đồ thị qua ingest_concepts (dò trùng + log đầy đủ)."""
    concept = _load(conn, concept_id)
    if concept.status != "pending_review":
        raise AlreadyDecided(f"{concept_id} đã ở trạng thái {concept.status}")

    draft = ConceptDraft(
        title=concept.title,
        subject=concept.subject,
        summary=concept.summary,
        source_module=concept.source_module,
    )
    item = ingest_concepts(conn, [draft]).ingested[0]
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE ks.extracted_concepts SET status = 'accepted', node_id = %s,"
            " updated_at = now() WHERE id = %s",
            (item.node_id, concept_id),
        )
    return item


def discard(conn: psycopg.Connection, concept_id: UUID) -> None:
    """Giữ row vĩnh viễn với status='discarded'. Không xoá."""
    concept = _load(conn, concept_id)
    if concept.status != "pending_review":
        raise AlreadyDecided(f"{concept_id} đã ở trạng thái {concept.status}")
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE ks.extracted_concepts SET status = 'discarded', updated_at = now()"
            " WHERE id = %s",
            (concept_id,),
        )
