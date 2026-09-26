"""Reading nodes and edges for HTTP consumers. Pure DB reads, NO LLM.

`list_nodes` / `get_node` do not return edges (a settled decision); edges have
their own route, `list_edges`.
"""

from __future__ import annotations

from uuid import UUID

import psycopg

from ks import settings
from ks.models import EdgeSummary, NodeSummary

# merged_into_id not NULL → return the TARGET node, exactly ONE STEP (no chain walk).
# DISTINCT ON collapses several nodes with the same target into one row. LEFT JOIN +
# DISTINCT ON does it all in ONE round-trip.
#
# Filters apply to the RESOLVED node, not the original: the result always matches
# what the caller asked for — filtering subject='Physics' never returns a Biology
# node.
_LIST_SQL = """
SELECT id, title, subject, summary
FROM (
  SELECT DISTINCT ON (COALESCE(t.id, n.id))
         COALESCE(t.id, n.id)                       AS id,
         COALESCE(t.title, n.title)                 AS title,
         COALESCE(t.subject, n.subject)             AS subject,
         COALESCE(t.summary, n.summary)             AS summary,
         COALESCE(t.created_at, n.created_at)       AS created_at
  FROM ks.nodes n
  LEFT JOIN ks.nodes t ON t.id = n.merged_into_id
  -- ::text / ::ks.source_module are required: without the cast Postgres cannot infer
  -- the type of a bare parameter in an IS NULL clause and raises AmbiguousParameter.
  -- (Do not write a sample placeholder in a comment: psycopg still parses comments.)
  WHERE (%(subject)s::text IS NULL OR COALESCE(t.subject, n.subject) = %(subject)s::text)
    AND (%(source_module)s::ks.source_module IS NULL
         OR COALESCE(t.source_module, n.source_module) = %(source_module)s::ks.source_module)
  ORDER BY COALESCE(t.id, n.id), n.created_at
) resolved
ORDER BY title, id
LIMIT %(limit)s
"""


def list_nodes(
    conn: psycopg.Connection,
    *,
    subject: str | None = None,
    source_module: str | None = None,
    limit: int = settings.DEFAULT_NODE_LIMIT,
) -> tuple[NodeSummary, ...]:
    """Nodes with merges resolved. No edges — deliberately."""
    with conn.cursor() as cur:
        cur.execute(
            _LIST_SQL,
            {"subject": subject, "source_module": source_module, "limit": limit},
        )
        rows = cur.fetchall()
    return tuple(
        NodeSummary(id=r[0], title=r[1], subject=r[2], summary=r[3]) for r in rows
    )


# Same merge-resolution rule as _LIST_SQL: return the TARGET node, exactly ONE STEP.
# Two routes reading the same data must not behave differently.
#
# Note for callers: if node A was merged into B, this returns B — the `id` in the
# result DIFFERS from the `node_id` passed in. That is intended, not a bug.
_GET_SQL = """
SELECT COALESCE(t.id, n.id)             AS id,
       COALESCE(t.title, n.title)       AS title,
       COALESCE(t.subject, n.subject)   AS subject,
       COALESCE(t.summary, n.summary)   AS summary
FROM ks.nodes n
LEFT JOIN ks.nodes t ON t.id = n.merged_into_id
WHERE n.id = %(node_id)s
"""


def get_node(conn: psycopg.Connection, node_id: UUID) -> NodeSummary | None:
    """One node by id, merges resolved. None if there is none.

    There is NO such thing as an "unreviewed node": `ks.nodes` has no status column;
    a node only exists AFTER `ks.cli accept` runs (see ks/confirm.py). The id of an
    extracted_concept that was never accepted is simply not a node id → None.
    """
    with conn.cursor() as cur:
        cur.execute(_GET_SQL, {"node_id": node_id})
        row = cur.fetchone()
    if row is None:
        return None
    return NodeSummary(id=row[0], title=row[1], subject=row[2], summary=row[3])


# GET /edges: `approved` edges only — `pending` is an LLM suggestion nobody has
# reviewed, `rejected` is the reviewer's "no"; drawing either on the map would
# misstate what the learner decided.
#
# Both ends are resolved through merges EXACTLY ONE STEP, same rule as _LIST_SQL, so
# every id in the result is an id GET /nodes returns. After resolving:
# - an edge that loops back to itself (A→B once A is merged into B) is dropped;
# - duplicate edges (from, to, relation) collapse into one, keeping the smallest id.
# The node_id filter also applies to the RESOLVED ids.
_EDGES_SQL = """
SELECT DISTINCT ON (from_id, to_id, relation_type)
       id, from_id, to_id, relation_type
FROM (
  SELECT e.id,
         COALESCE(fm.id, f.id) AS from_id,
         COALESCE(tm.id, t.id) AS to_id,
         e.relation_type::text  AS relation_type
  FROM ks.edges e
  JOIN ks.nodes f ON f.id = e.from_node_id
  LEFT JOIN ks.nodes fm ON fm.id = f.merged_into_id
  JOIN ks.nodes t ON t.id = e.to_node_id
  LEFT JOIN ks.nodes tm ON tm.id = t.merged_into_id
  WHERE e.status = 'approved'
) resolved
WHERE from_id <> to_id
  AND (%(node_id)s::uuid IS NULL OR from_id = %(node_id)s::uuid OR to_id = %(node_id)s::uuid)
ORDER BY from_id, to_id, relation_type, id
LIMIT %(limit)s
"""


def list_edges(
    conn: psycopg.Connection,
    *,
    node_id: UUID | None = None,
    limit: int = settings.MAX_EDGE_LIMIT,
) -> tuple[EdgeSummary, ...]:
    """Approved edges, both ends resolved through merges. Never pending/rejected."""
    with conn.cursor() as cur:
        cur.execute(_EDGES_SQL, {"node_id": node_id, "limit": limit})
        rows = cur.fetchall()
    return tuple(
        EdgeSummary(id=r[0], from_node_id=r[1], to_node_id=r[2], relation_type=r[3]) for r in rows
    )
