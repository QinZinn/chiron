"""Edge suggestion (top-K → LLM → pending) and the review operations.

The LLM only SUGGESTS. Every edge enters the DB as 'pending' and must be reviewed
by a person — no auto-approve. 'rejected' rows are kept forever and excluded from
the next candidate set: a question the user already answered is not asked again.

INSTRUMENTATION: every log here is written inside the business function itself, in the same transaction.
"""

from __future__ import annotations

import json
import re
from uuid import UUID

import psycopg

from ks import settings
from ks.llm import LLMError, LLMParseError, LLMProvider, Message
from ks.models import (
    EdgeSuggestion,
    Neighbor,
    NeighborCandidate,
    PendingEdge,
    RelationType,
    SuggestedBy,
    SuggestionRun,
)

# Candidate set: same subject first, then the highest trigram similarity of
# title-to-title and summary-to-summary. Every pair that ALREADY has an edge in ANY
# status is excluded up front — 'rejected' included.
_CANDIDATE_SQL = """
SELECT n.id, n.title, n.subject, n.summary,
       GREATEST(similarity(n.title, %(title)s), similarity(n.summary, %(summary)s)) AS score
FROM ks.nodes n
WHERE n.id <> %(node_id)s
  AND n.merged_into_id IS NULL
  AND NOT EXISTS (
        SELECT 1 FROM ks.edges e
        WHERE (e.from_node_id = %(node_id)s AND e.to_node_id = n.id)
           OR (e.from_node_id = n.id AND e.to_node_id = %(node_id)s)
      )
ORDER BY (n.subject = %(subject)s) DESC, score DESC, n.created_at DESC
LIMIT %(k)s
"""

_RUN_LOG_SQL = """
INSERT INTO ks.edge_suggestion_run
  (node_id, candidate_node_ids, suggested_count, outcome, provider, model, error)
VALUES (%s, %s, %s, %s, %s, %s, %s)
"""

_DECISION_LOG_SQL = """
INSERT INTO ks.edge_decision_log
  (edge_id, from_node_id, to_node_id, relation_type, previous_relation_type,
   decision, suggested_by, was_in_candidate_set)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""

_SYSTEM_PROMPT = (
    "You help build a concept graph for a student. "
    "Given a root concept and a list of candidate concepts, name only the RELIABLE relations. "
    "Return only JSON, no explanation outside the JSON."
)


class EdgeNotFound(Exception):
    """No edge with this edge_id."""


class NodeNotFound(Exception):
    """No node with this node_id."""


# ---------------------------------------------------------------- reading nodes


def _load_node(conn: psycopg.Connection, node_id: UUID) -> tuple[str, str, str]:
    with conn.cursor() as cur:
        cur.execute("SELECT title, subject, summary FROM ks.nodes WHERE id = %s", (node_id,))
        row = cur.fetchone()
    if row is None:
        raise NodeNotFound(f"No node {node_id}")
    return row


def candidate_neighbors(
    conn: psycopg.Connection,
    node_id: UUID,
    *,
    k: int = settings.EDGE_SUGGESTION_TOP_K,
) -> tuple[NeighborCandidate, ...]:
    title, subject, summary = _load_node(conn, node_id)
    with conn.cursor() as cur:
        cur.execute(
            _CANDIDATE_SQL,
            {"node_id": node_id, "title": title, "subject": subject, "summary": summary, "k": k},
        )
        rows = cur.fetchall()
    return tuple(
        NeighborCandidate(node_id=r[0], title=r[1], subject=r[2], summary=r[3], score=float(r[4]))
        for r in rows
    )


# ---------------------------------------------------------------- prompt


def build_prompt(
    node: tuple[str, str, str], candidates: tuple[NeighborCandidate, ...]
) -> list[Message]:
    title, subject, summary = node
    lines = [
        f"ROOT CONCEPT: {title}",
        f"Subject: {subject}",
        f"Summary: {summary}",
        "",
        "CANDIDATES:",
    ]
    for i, cand in enumerate(candidates):
        lines.append(f"[{i}] {cand.title} (subject: {cand.subject}) — {cand.summary}")
    lines += [
        "",
        "Return a JSON array. Each element:",
        '{"candidate": <number in square brackets>, '
        '"relation_type": "prerequisite" | "related" | "contrasts_with", '
        '"reason": "<one English sentence>"}',
        "",
        "prerequisite = the root concept must be understood BEFORE the candidate (directed).",
        "related = connected but not dependent. contrasts_with = easily confused with each other.",
        "If unsure, leave it out. If no relation is reliable, return [].",
    ]
    return [Message("system", _SYSTEM_PROMPT), Message("user", "\n".join(lines))]


def _strip_json_fence(text: str) -> str:
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else text.strip()


def parse_suggestions(text: str, candidate_count: int) -> tuple[tuple[int, RelationType, str], ...]:
    """Parse the LLM response. Wrong structure → LLMParseError.

    Stray elements (index out of range, unknown relation_type) are silently SKIPPED —
    one bad row should not void the whole batch of suggestions.
    """
    try:
        parsed = json.loads(_strip_json_fence(text))
    except (json.JSONDecodeError, ValueError) as exc:
        raise LLMParseError(f"Response is not JSON: {text[:200]}") from exc
    if not isinstance(parsed, list):
        raise LLMParseError(f"Response is not a JSON array: {text[:200]}")

    out: list[tuple[int, RelationType, str]] = []
    seen: set[int] = set()
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        idx = entry.get("candidate")
        if not isinstance(idx, int) or not 0 <= idx < candidate_count or idx in seen:
            continue
        try:
            relation = RelationType(entry.get("relation_type"))
        except ValueError:
            continue
        seen.add(idx)
        out.append((idx, relation, str(entry.get("reason", ""))[:500]))
    return tuple(out)


# ---------------------------------------------------------------- suggest


def suggest_edges(
    conn: psycopg.Connection,
    node_id: UUID,
    provider: LLMProvider,
    *,
    k: int = settings.EDGE_SUGGESTION_TOP_K,
) -> SuggestionRun:
    """Top-K candidates → LLM → write 'pending' edges.

    Does NOT raise on LLM errors: catches LLMError, records the outcome in
    ks.edge_suggestion_run and returns. Raising would make the caller roll back and
    lose the log row — and the whole point of instrumentation is to measure failures
    too. DB errors still propagate.
    """
    node = _load_node(conn, node_id)
    candidates = candidate_neighbors(conn, node_id, k=k)
    candidate_ids = [c.node_id for c in candidates]

    if not candidates:
        _log_run(conn, node_id, candidate_ids, 0, "no_candidates", provider, None)
        return SuggestionRun(node_id, (), (), "no_candidates")

    try:
        text = provider.complete(
            build_prompt(node, candidates),
            max_tokens=settings.EDGE_SUGGESTION_MAX_TOKENS,
        )
        parsed = parse_suggestions(text, len(candidates))
    except LLMParseError as exc:
        _log_run(conn, node_id, candidate_ids, 0, "parse_error", provider, str(exc))
        return SuggestionRun(node_id, candidates, (), "parse_error", str(exc))
    except LLMError as exc:
        _log_run(conn, node_id, candidate_ids, 0, "llm_error", provider, str(exc))
        return SuggestionRun(node_id, candidates, (), "llm_error", str(exc))

    suggestions: list[EdgeSuggestion] = []
    with conn.cursor() as cur:
        for idx, relation, reason in parsed:
            cand = candidates[idx]
            cur.execute(
                "INSERT INTO ks.edges (from_node_id, to_node_id, relation_type, suggested_by)"
                " VALUES (%s, %s, %s, 'llm')"
                " ON CONFLICT (from_node_id, to_node_id, relation_type) DO NOTHING"
                " RETURNING id",
                (node_id, cand.node_id, relation.value),
            )
            row = cur.fetchone()
            if row is None:  # the edge already exists — never override an earlier decision
                continue
            suggestions.append(
                EdgeSuggestion(
                    edge_id=row[0],
                    from_node_id=node_id,
                    to_node_id=cand.node_id,
                    to_title=cand.title,
                    relation_type=relation,
                    reason=reason,
                )
            )

    _log_run(conn, node_id, candidate_ids, len(suggestions), "ok", provider, None)
    return SuggestionRun(node_id, candidates, tuple(suggestions), "ok")


def _log_run(conn, node_id, candidate_ids, count, outcome, provider, error) -> None:
    with conn.cursor() as cur:
        cur.execute(
            _RUN_LOG_SQL,
            (
                node_id,
                candidate_ids,
                count,
                outcome,
                getattr(provider, "name", None),
                getattr(provider, "model", None),
                error,
            ),
        )


# ---------------------------------------------------------------- review


def list_pending(conn: psycopg.Connection, *, limit: int = 100) -> tuple[PendingEdge, ...]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.id, e.from_node_id, f.title, e.to_node_id, t.title,
                   e.relation_type, e.suggested_by
            FROM ks.edges e
            JOIN ks.nodes f ON f.id = e.from_node_id
            JOIN ks.nodes t ON t.id = e.to_node_id
            WHERE e.status = 'pending'
            ORDER BY e.created_at
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
    return tuple(
        PendingEdge(
            edge_id=r[0],
            from_node_id=r[1],
            from_title=r[2],
            to_node_id=r[3],
            to_title=r[4],
            relation_type=RelationType(r[5]),
            suggested_by=SuggestedBy(r[6]),
        )
        for r in rows
    )


def _load_edge(conn: psycopg.Connection, edge_id: UUID):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT from_node_id, to_node_id, relation_type, suggested_by, status"
            " FROM ks.edges WHERE id = %s",
            (edge_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise EdgeNotFound(f"No edge {edge_id}")
    return row


def was_in_candidate_set(conn: psycopg.Connection, a: UUID, b: UUID) -> bool:
    """COULD full-text have surfaced this pair?

    True if b was ever in the candidate set of a suggest run on a (or the other way
    round). This is the denominator of the most important metric: edges the user
    adds by hand that full-text could not suggest.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM ks.edge_suggestion_run
            WHERE (node_id = %(a)s AND %(b)s = ANY(candidate_node_ids))
               OR (node_id = %(b)s AND %(a)s = ANY(candidate_node_ids))
            LIMIT 1
            """,
            {"a": a, "b": b},
        )
        return cur.fetchone() is not None


def _log_decision(
    conn, edge_id, from_id, to_id, relation, previous, decision, suggested_by, in_set
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            _DECISION_LOG_SQL,
            (
                edge_id,
                from_id,
                to_id,
                relation.value,
                previous.value if previous else None,
                decision,
                suggested_by.value,
                in_set,
            ),
        )


def approve_edge(conn: psycopg.Connection, edge_id: UUID) -> None:
    from_id, to_id, relation, suggested_by, _ = _load_edge(conn, edge_id)
    with conn.cursor() as cur:
        cur.execute("UPDATE ks.edges SET status = 'approved' WHERE id = %s", (edge_id,))
    _log_decision(
        conn, edge_id, from_id, to_id, RelationType(relation), None,
        "approved", SuggestedBy(suggested_by), was_in_candidate_set(conn, from_id, to_id),
    )


def reject_edge(conn: psycopg.Connection, edge_id: UUID) -> None:
    """Keep the row forever with status='rejected' — nothing is deleted. The pair is
    excluded from the candidate set of the next suggest run."""
    from_id, to_id, relation, suggested_by, _ = _load_edge(conn, edge_id)
    with conn.cursor() as cur:
        cur.execute("UPDATE ks.edges SET status = 'rejected' WHERE id = %s", (edge_id,))
    _log_decision(
        conn, edge_id, from_id, to_id, RelationType(relation), None,
        "rejected", SuggestedBy(suggested_by), was_in_candidate_set(conn, from_id, to_id),
    )


def edit_edge(conn: psycopg.Connection, edge_id: UUID, relation_type: RelationType) -> None:
    """The user corrected the relation type, then accepted. The old type is recorded to measure what the LLM got wrong."""
    from_id, to_id, old_relation, suggested_by, _ = _load_edge(conn, edge_id)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE ks.edges SET relation_type = %s, status = 'approved' WHERE id = %s",
            (relation_type.value, edge_id),
        )
    _log_decision(
        conn, edge_id, from_id, to_id, relation_type, RelationType(old_relation),
        "edited", SuggestedBy(suggested_by), was_in_candidate_set(conn, from_id, to_id),
    )


def add_edge(
    conn: psycopg.Connection,
    from_node_id: UUID,
    to_node_id: UUID,
    relation_type: RelationType,
) -> UUID:
    """The user adds an edge by hand. Goes straight to 'approved' — the user asserted
    it; there is no need to review their own decision.

    was_in_candidate_set=False here is exactly the evidence that full-text missed it.
    """
    _load_node(conn, from_node_id)
    _load_node(conn, to_node_id)
    in_set = was_in_candidate_set(conn, from_node_id, to_node_id)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ks.edges (from_node_id, to_node_id, relation_type, suggested_by, status)"
            " VALUES (%s, %s, %s, 'manual', 'approved')"
            " ON CONFLICT (from_node_id, to_node_id, relation_type)"
            " DO UPDATE SET status = 'approved'"
            " RETURNING id",
            (from_node_id, to_node_id, relation_type.value),
        )
        edge_id = cur.fetchone()[0]
    _log_decision(
        conn, edge_id, from_node_id, to_node_id, relation_type, None,
        "manual_add", SuggestedBy.MANUAL, in_set,
    )
    return edge_id


# ---------------------------------------------------------------- reading the graph


def neighbors(conn: psycopg.Connection, node_id: UUID) -> tuple[Neighbor, ...]:
    """Nodes adjacent through an approved edge.

    'related' and 'contrasts_with' are symmetric: stored one way, queried BOTH ways.
    'prerequisite' is directed: direction 'out' = this node is a prerequisite of the other.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.id, t.title, e.relation_type, 'out' AS direction
            FROM ks.edges e JOIN ks.nodes t ON t.id = e.to_node_id
            WHERE e.from_node_id = %(id)s AND e.status = 'approved'
            UNION ALL
            SELECT f.id, f.title, e.relation_type, 'in' AS direction
            FROM ks.edges e JOIN ks.nodes f ON f.id = e.from_node_id
            WHERE e.to_node_id = %(id)s AND e.status = 'approved'
            ORDER BY 3, 2
            """,
            {"id": node_id},
        )
        rows = cur.fetchall()
    out: list[Neighbor] = []
    for node, title, relation, direction in rows:
        rel = RelationType(relation)
        # Symmetric → direction carries no meaning, normalised to 'both'.
        if rel in (RelationType.RELATED, RelationType.CONTRASTS_WITH):
            direction = "both"
        out.append(Neighbor(node_id=node, title=title, relation_type=rel, direction=direction))
    return tuple(out)


def stats(conn: psycopg.Connection) -> dict:
    """Instrumentation numbers. The most important row: manual_add_missed_by_fulltext."""
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM ks.nodes WHERE merged_into_id IS NULL")
        nodes = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM ks.nodes WHERE merged_into_id IS NOT NULL")
        merged = cur.fetchone()[0]
        cur.execute("SELECT status, count(*) FROM ks.edges GROUP BY status")
        edges_by_status = {r[0]: r[1] for r in cur.fetchall()}
        cur.execute("SELECT decision, count(*) FROM ks.ingest_log GROUP BY decision")
        ingest_by_decision = {r[0]: r[1] for r in cur.fetchall()}
        cur.execute("SELECT outcome, count(*) FROM ks.edge_suggestion_run GROUP BY outcome")
        runs_by_outcome = {r[0]: r[1] for r in cur.fetchall()}
        cur.execute("SELECT decision, count(*) FROM ks.edge_decision_log GROUP BY decision")
        decisions = {r[0]: r[1] for r in cur.fetchall()}
        cur.execute(
            "SELECT count(*) FROM ks.edge_decision_log"
            " WHERE decision = 'manual_add' AND was_in_candidate_set = false"
        )
        missed = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM ks.edge_decision_log WHERE decision = 'manual_add'")
        manual_total = cur.fetchone()[0]
        cur.execute("SELECT status, count(*) FROM ks.card_sync_log GROUP BY status")
        card_sync = {r[0]: r[1] for r in cur.fetchall()}

    return {
        # nodes_total is a general operational metric (how big KS data is).
        # Originally added because of Mnemosyne's limit=500; that limit is gone
        # thanks to GET /nodes/{id}, but the metric is still useful, so it stays.
        "nodes_total": nodes + merged,
        "nodes": nodes,
        "nodes_merged": merged,
        "card_sync_by_status": card_sync,
        "edges_by_status": edges_by_status,
        "ingest_by_decision": ingest_by_decision,
        "suggestion_runs_by_outcome": runs_by_outcome,
        "edge_decisions": decisions,
        "manual_add_total": manual_total,
        # The ONLY evidence for a future decision on pgvector.
        "manual_add_missed_by_fulltext": missed,
    }
