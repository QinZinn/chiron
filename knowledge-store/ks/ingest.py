"""Writing concepts into KS + duplicate detection with trigram full-text.

FAIL-LOUD: DB errors propagate as psycopg.Error. Deliberately the opposite of
save_transcript (which never raises). The HTTP wrapper catches them → 503.
"""

from __future__ import annotations

import json
from uuid import UUID

import psycopg

from ks import settings
from ks.models import (
    ConceptDraft,
    DuplicateCandidate,
    IngestedConcept,
    IngestResult,
)

# similarity() rather than the `%` operator, so the threshold does not depend on
# the session's pg_trgm.similarity_threshold GUC. The price is no GIN index —
# acceptable at single-user scale, recorded as technical debt.
_CANDIDATE_SQL = """
SELECT id, title, similarity(title, %(title)s) AS score
FROM ks.nodes
WHERE merged_into_id IS NULL
  AND similarity(title, %(title)s) >= %(floor)s
ORDER BY score DESC, title ASC
LIMIT %(limit)s
"""

_INSERT_SQL = """
INSERT INTO ks.nodes (title, subject, summary, source_module)
VALUES (%s, %s, %s, %s)
RETURNING id
"""

# The log is written INSIDE the business function, in the same transaction — not
# a side table the caller has to remember. A rollback must lose both log and data.
_LOG_SQL = """
INSERT INTO ks.ingest_log
  (node_id, draft_title, draft_subject, source_module, decision, threshold, top_score, candidates, chosen_by)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def _log_ingest(
    conn: psycopg.Connection,
    item: IngestedConcept,
    threshold: float,
    chosen_by: str = "rule",
) -> None:
    payload = json.dumps(
        [
            {"node_id": str(c.node_id), "title": c.title, "score": c.score}
            for c in item.candidates
        ],
        ensure_ascii=False,
    )
    top_score = item.candidates[0].score if item.candidates else None
    with conn.cursor() as cur:
        cur.execute(
            _LOG_SQL,
            (
                item.node_id,
                item.draft.title,
                item.draft.subject,
                item.draft.source_module.value,
                "created" if item.created else "merged",
                threshold,
                top_score,
                payload,
                chosen_by,
            ),
        )


def find_candidates(
    conn: psycopg.Connection,
    title: str,
    *,
    floor: float = settings.CANDIDATE_FLOOR,
    limit: int = settings.CANDIDATE_LIMIT,
) -> tuple[DuplicateCandidate, ...]:
    """Existing nodes similar to `title`, sorted by similarity, highest first.

    Skips merged nodes (merged_into_id not NULL) — never suggest merging into a
    node that is gone.
    """
    with conn.cursor() as cur:
        cur.execute(_CANDIDATE_SQL, {"title": title, "floor": floor, "limit": limit})
        rows = cur.fetchall()
    return tuple(
        DuplicateCandidate(node_id=row[0], title=row[1], score=float(row[2]))
        for row in rows
    )


def _insert_node(conn: psycopg.Connection, draft: ConceptDraft) -> UUID:
    with conn.cursor() as cur:
        cur.execute(
            _INSERT_SQL,
            (draft.title, draft.subject, draft.summary, draft.source_module.value),
        )
        return cur.fetchone()[0]


class MergeTargetInvalid(ValueError):
    """The merge target does not exist, or has itself been merged into another node."""


def pick_duplicate(
    candidates: tuple[DuplicateCandidate, ...],
    threshold: float = settings.DUPLICATE_THRESHOLD,
) -> DuplicateCandidate | None:
    """The node the automatic rule would merge into — so the review screen can say so up front."""
    return _pick_duplicate(candidates, threshold)


def ingest_decided(
    conn: psycopg.Connection,
    draft: ConceptDraft,
    *,
    merge_into: UUID | None,
    threshold: float = settings.DUPLICATE_THRESHOLD,
) -> IngestedConcept:
    """Write one draft by the LEARNER's choice, not by the threshold.

    `merge_into=None` → create a new node, even when a candidate is over the threshold.
    Otherwise → match that node; it must exist and not be merged. Candidates are
    still looked up and logged as in ingest_concepts, with `chosen_by='learner'`,
    so the learner's decision can be compared with what the threshold would have done.
    """
    candidates = find_candidates(conn, draft.title)
    if merge_into is None:
        node_id = _insert_node(conn, draft)
        created = True
    else:
        with conn.cursor() as cur:
            cur.execute("SELECT merged_into_id FROM ks.nodes WHERE id = %s", (merge_into,))
            row = cur.fetchone()
        if row is None:
            raise MergeTargetInvalid(f"node {merge_into} does not exist")
        if row[0] is not None:
            raise MergeTargetInvalid(f"node {merge_into} was merged into {row[0]}; choose that node")
        node_id = merge_into
        created = False
    item = IngestedConcept(draft=draft, node_id=node_id, created=created, candidates=candidates)
    _log_ingest(conn, item, threshold, chosen_by="learner")
    return item


def _pick_duplicate(
    candidates: tuple[DuplicateCandidate, ...],
    threshold: float,
) -> DuplicateCandidate | None:
    """The first candidate at or over the merge threshold, or None."""
    for cand in candidates:
        if cand.score >= threshold:
            return cand
    return None


def ingest_concepts(
    conn: psycopg.Connection,
    drafts: list[ConceptDraft] | tuple[ConceptDraft, ...],
    *,
    threshold: float = settings.DUPLICATE_THRESHOLD,
) -> IngestResult:
    """Write each draft: match an existing node if similarity >= threshold, otherwise create one.

    No commit — the transaction belongs to the caller. Drafts in the same batch see
    each other: a second draft duplicating the first matches the node just created.
    """
    ingested: list[IngestedConcept] = []
    for draft in drafts:
        candidates = find_candidates(conn, draft.title)
        match = _pick_duplicate(candidates, threshold)
        if match is not None:
            node_id: UUID = match.node_id
            created = False
        else:
            node_id = _insert_node(conn, draft)
            created = True

        item = IngestedConcept(
            draft=draft,
            node_id=node_id,
            created=created,
            candidates=candidates,
        )
        _log_ingest(conn, item, threshold)
        ingested.append(item)

    return IngestResult(ingested=tuple(ingested))
