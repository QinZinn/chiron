"""Gợi ý cạnh (top-K → LLM → pending) và các thao tác duyệt.

LLM chỉ GỢI Ý. Mọi cạnh vào DB ở trạng thái 'pending' và phải qua người duyệt —
không auto-approve. Row 'rejected' giữ vĩnh viễn và bị loại khỏi candidate set
lần sau: không hỏi lại câu người dùng đã trả lời.

INSTRUMENTATION: mọi log ở đây nằm trong chính hàm nghiệp vụ, cùng transaction.
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

# Candidate set: cùng subject trước, rồi tới similarity trigram cao nhất giữa
# title-với-title và summary-với-summary. Loại sẵn mọi cặp ĐÃ có cạnh ở BẤT KỲ
# trạng thái nào — kể cả 'rejected'.
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
    "Bạn là trợ lý xây dựng đồ thị khái niệm cho học sinh. "
    "Cho một khái niệm gốc và danh sách khái niệm ứng viên, hãy chỉ ra quan hệ ĐÁNG TIN. "
    "Chỉ trả JSON, không giải thích ngoài JSON."
)


class EdgeNotFound(Exception):
    """edge_id không tồn tại."""


class NodeNotFound(Exception):
    """node_id không tồn tại."""


# ---------------------------------------------------------------- đọc node


def _load_node(conn: psycopg.Connection, node_id: UUID) -> tuple[str, str, str]:
    with conn.cursor() as cur:
        cur.execute("SELECT title, subject, summary FROM ks.nodes WHERE id = %s", (node_id,))
        row = cur.fetchone()
    if row is None:
        raise NodeNotFound(f"Không có node {node_id}")
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
        f"KHÁI NIỆM GỐC: {title}",
        f"Môn: {subject}",
        f"Tóm tắt: {summary}",
        "",
        "ỨNG VIÊN:",
    ]
    for i, cand in enumerate(candidates):
        lines.append(f"[{i}] {cand.title} (môn: {cand.subject}) — {cand.summary}")
    lines += [
        "",
        "Trả về JSON array. Mỗi phần tử:",
        '{"candidate": <số trong ngoặc vuông>, '
        '"relation_type": "prerequisite" | "related" | "contrasts_with", '
        '"reason": "<một câu>"}',
        "",
        "prerequisite = phải hiểu khái niệm gốc TRƯỚC khi hiểu ứng viên (có hướng).",
        "related = liên quan nhưng không phụ thuộc. contrasts_with = dễ nhầm lẫn với nhau.",
        "Không chắc thì bỏ qua. Không có quan hệ nào đáng tin thì trả [].",
    ]
    return [Message("system", _SYSTEM_PROMPT), Message("user", "\n".join(lines))]


def _strip_json_fence(text: str) -> str:
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else text.strip()


def parse_suggestions(text: str, candidate_count: int) -> tuple[tuple[int, RelationType, str], ...]:
    """Parse phản hồi LLM. Sai cấu trúc → LLMParseError.

    Phần tử lẻ (index ngoài phạm vi, relation_type lạ) bị BỎ QUA lặng lẽ —
    một dòng hỏng không nên huỷ cả lô gợi ý.
    """
    try:
        parsed = json.loads(_strip_json_fence(text))
    except (json.JSONDecodeError, ValueError) as exc:
        raise LLMParseError(f"Phản hồi không phải JSON: {text[:200]}") from exc
    if not isinstance(parsed, list):
        raise LLMParseError(f"Phản hồi không phải JSON array: {text[:200]}")

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
    """Top-K ứng viên → LLM → ghi cạnh 'pending'.

    KHÔNG raise khi LLM lỗi: bắt LLMError, ghi outcome vào ks.edge_suggestion_run
    rồi trả về. Nếu raise thì caller rollback và mất luôn dòng log — mà cả điểm
    của instrumentation là đo được cả những lần thất bại. Lỗi DB vẫn văng ra.
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
            if row is None:  # cạnh đã tồn tại — không đè lên quyết định cũ
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


# ---------------------------------------------------------------- duyệt


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
        raise EdgeNotFound(f"Không có edge {edge_id}")
    return row


def was_in_candidate_set(conn: psycopg.Connection, a: UUID, b: UUID) -> bool:
    """Full-text CÓ đưa được cặp này ra không?

    True nếu b từng nằm trong candidate set của một lần suggest trên a (hoặc
    ngược lại). Đây là mẫu số của chỉ số quan trọng nhất: số cạnh người dùng
    thêm tay mà full-text không đề xuất nổi.
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
    """Giữ row vĩnh viễn với status='rejected' — không xoá. Cặp này bị loại
    khỏi candidate set lần suggest sau."""
    from_id, to_id, relation, suggested_by, _ = _load_edge(conn, edge_id)
    with conn.cursor() as cur:
        cur.execute("UPDATE ks.edges SET status = 'rejected' WHERE id = %s", (edge_id,))
    _log_decision(
        conn, edge_id, from_id, to_id, RelationType(relation), None,
        "rejected", SuggestedBy(suggested_by), was_in_candidate_set(conn, from_id, to_id),
    )


def edit_edge(conn: psycopg.Connection, edge_id: UUID, relation_type: RelationType) -> None:
    """Người dùng sửa loại quan hệ rồi chấp nhận. Ghi lại loại cũ để đo LLM sai gì."""
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
    """Người dùng tự thêm cạnh. Vào thẳng 'approved' — người dùng khẳng định,
    không cần tự duyệt lại chính mình.

    was_in_candidate_set=False ở đây chính là bằng chứng full-text bỏ sót.
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


# ---------------------------------------------------------------- đọc đồ thị


def neighbors(conn: psycopg.Connection, node_id: UUID) -> tuple[Neighbor, ...]:
    """Node kề qua cạnh đã approved.

    'related' và 'contrasts_with' đối xứng: lưu một chiều, query HAI chiều.
    'prerequisite' có hướng: direction 'out' = node này là tiền đề của kia.
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
        # Đối xứng → hướng không mang nghĩa, chuẩn hoá thành 'both'.
        if rel in (RelationType.RELATED, RelationType.CONTRASTS_WITH):
            direction = "both"
        out.append(Neighbor(node_id=node, title=title, relation_type=rel, direction=direction))
    return tuple(out)


def stats(conn: psycopg.Connection) -> dict:
    """Số liệu instrumentation. Dòng quan trọng nhất: manual_add_missed_by_fulltext."""
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
        # nodes_total là chỉ số vận hành chung (quy mô dữ liệu KS đang ở đâu).
        # Ban đầu thêm vì giới hạn limit=500 phía Mnemosyne; giới hạn đó đã hết
        # nhờ GET /nodes/{id}, nhưng chỉ số vẫn hữu ích nên giữ.
        "nodes_total": nodes + merged,
        "nodes": nodes,
        "nodes_merged": merged,
        "card_sync_by_status": card_sync,
        "edges_by_status": edges_by_status,
        "ingest_by_decision": ingest_by_decision,
        "suggestion_runs_by_outcome": runs_by_outcome,
        "edge_decisions": decisions,
        "manual_add_total": manual_total,
        # Căn cứ DUY NHẤT để sau này quyết pgvector.
        "manual_add_missed_by_fulltext": missed,
    }
