"""Confirming extracted concepts: list / accept / discard.

accept pushes the concept through ingest_concepts, so it is subject to EXACTLY
the same duplicate rule and instrumentation as every other write path — no back door.
discard keeps the row with status='discarded', it does NOT delete — same logic as a 'rejected' edge.
"""

from __future__ import annotations

from uuid import UUID

import psycopg

from ks.ingest import find_candidates, ingest_concepts, ingest_decided, pick_duplicate
from ks.models import ConceptDraft, DuplicateCandidate, ExtractedConcept, IngestedConcept, SourceModule
from ks.transcripts import _row_to_concept

_SELECT = (
    "SELECT id, transcript_id, title, subject, summary, source_module, status, node_id"
    " FROM ks.extracted_concepts"
)


class ExtractedConceptNotFound(Exception):
    """No extracted_concept with this id."""


class AlreadyDecided(Exception):
    """The concept is already accepted/discarded — a question already answered is not asked again."""


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
        raise ExtractedConceptNotFound(f"No extracted_concept {concept_id}")
    return _row_to_concept(row)


def accept(
    conn: psycopg.Connection,
    concept_id: UUID,
    *,
    decision: str | None = None,
    merge_into: UUID | None = None,
) -> IngestedConcept:
    """Write the concept into the graph.

    - `decision=None`: ingest_concepts' automatic rule (threshold 0.6). The CLI and
      every older caller take this path.
    - `decision="create"`: the learner chose a new node, even with a candidate over the threshold.
    - `decision="merge"`: the learner chose to merge into `merge_into`.
    """
    concept = _load(conn, concept_id)
    if concept.status != "pending_review":
        raise AlreadyDecided(f"{concept_id} is already {concept.status}")

    draft = ConceptDraft(
        title=concept.title,
        subject=concept.subject,
        summary=concept.summary,
        source_module=concept.source_module,
    )
    if decision is None:
        item = ingest_concepts(conn, [draft]).ingested[0]
    elif decision == "create":
        item = ingest_decided(conn, draft, merge_into=None)
    elif decision == "merge":
        if merge_into is None:
            raise ValueError("decision=merge needs a node_id")
        item = ingest_decided(conn, draft, merge_into=merge_into)
    else:
        raise ValueError(f"decision must be create or merge, not {decision!r}")
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE ks.extracted_concepts SET status = 'accepted', node_id = %s,"
            " updated_at = now() WHERE id = %s",
            (item.node_id, concept_id),
        )
    return item


def candidates_for(
    conn: psycopg.Connection, concept_id: UUID
) -> tuple[ExtractedConcept, tuple[DuplicateCandidate, ...], DuplicateCandidate | None]:
    """Duplicate candidates for a pending concept, and the candidate the automatic
    rule would merge into (or None) — so the review screen can ask BEFORE writing."""
    concept = _load(conn, concept_id)
    candidates = find_candidates(conn, concept.title)
    return concept, candidates, pick_duplicate(candidates)


class NotMerged(Exception):
    """The concept created its own node; there is nothing to split off."""


def split(conn: psycopg.Connection, concept_id: UUID) -> IngestedConcept:
    """Undo a wrong merge: give an accepted concept a node of its own.

    For a concept the duplicate rule merged into an existing node that is really
    a different concept (a parent, or a near-namesake like "multicellular" vs
    "unicellular"). A new node is created from the concept's own title, subject
    and summary, the concept is pointed at it, and the decision is logged as the
    learner's. The node it had been merged into is left untouched.
    """
    concept = _load(conn, concept_id)
    if concept.status != "accepted" or concept.node_id is None:
        raise AlreadyDecided(f"{concept_id} is {concept.status}, not an accepted concept")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM ks.extracted_concepts c JOIN ks.nodes n ON n.id = c.node_id"
            " WHERE c.id = %s AND n.title = c.title",
            (concept_id,),
        )
        if cur.fetchone()[0]:
            raise NotMerged(f"{concept_id} already has its own node {concept.node_id}")
    draft = ConceptDraft(
        title=concept.title,
        subject=concept.subject,
        summary=concept.summary,
        source_module=concept.source_module,
    )
    item = ingest_decided(conn, draft, merge_into=None)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE ks.extracted_concepts SET node_id = %s, updated_at = now() WHERE id = %s",
            (item.node_id, concept_id),
        )
    return item


def discard(conn: psycopg.Connection, concept_id: UUID) -> None:
    """Keep the row forever with status='discarded'. Nothing is deleted."""
    concept = _load(conn, concept_id)
    if concept.status != "pending_review":
        raise AlreadyDecided(f"{concept_id} is already {concept.status}")
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE ks.extracted_concepts SET status = 'discarded', updated_at = now()"
            " WHERE id = %s",
            (concept_id,),
        )


def edit_pending(
    conn: psycopg.Connection,
    concept_id: UUID,
    *,
    title: str | None = None,
    subject: str | None = None,
    summary: str | None = None,
) -> ExtractedConcept:
    """Edit a concept BEFORE deciding on it. Once accepted/discarded it can no longer be edited.

    The cheapest place to fix an OCR or LLM mistake: fix it here and then accept,
    instead of accepting a misspelled title the duplicate check will never match.
    """
    concept = _load(conn, concept_id)
    if concept.status != "pending_review":
        raise AlreadyDecided(f"{concept_id} is already {concept.status}")
    fields = {"title": title, "subject": subject, "summary": summary}
    for name, value in fields.items():
        if value is not None and not value.strip():
            raise ValueError(f"{name} must not be empty")
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE ks.extracted_concepts SET title = COALESCE(%s, title),"
            " subject = COALESCE(%s, subject), summary = COALESCE(%s, summary),"
            " updated_at = now() WHERE id = %s",
            tuple(v.strip() if v is not None else None for v in fields.values()) + (concept_id,),
        )
    return _load(conn, concept_id)
