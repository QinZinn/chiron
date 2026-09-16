"""Đọc node cho consumer HTTP. Thuần đọc DB, KHÔNG chạm LLM, KHÔNG trả edges."""

from __future__ import annotations

from uuid import UUID

import psycopg

from ks import settings
from ks.models import NodeSummary

# merged_into_id khác NULL → trả node ĐÍCH, đúng MỘT BƯỚC (không walk chain).
# DISTINCT ON gộp nhiều node cùng đích về một dòng. LEFT JOIN + DISTINCT ON làm
# tất cả trong MỘT round-trip.
#
# Bộ lọc áp lên node ĐÃ RESOLVE, không phải node gốc: kết quả trả về luôn khớp
# điều kiện người gọi hỏi, không có chuyện lọc subject='Vật lý' mà nhận về node
# môn Sinh học.
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
  -- ::text / ::ks.source_module là bắt buộc: không có cast, Postgres không suy
  -- ra được kiểu của tham số trần trong mệnh đề IS NULL và báo AmbiguousParameter.
  -- (Đừng viết placeholder mẫu vào comment: psycopg vẫn parse comment.)
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
    """Danh sách node đã resolve merge. Không có edges — đó là chủ đích."""
    with conn.cursor() as cur:
        cur.execute(
            _LIST_SQL,
            {"subject": subject, "source_module": source_module, "limit": limit},
        )
        rows = cur.fetchall()
    return tuple(
        NodeSummary(id=r[0], title=r[1], subject=r[2], summary=r[3]) for r in rows
    )


# Cùng luật resolve merge như _LIST_SQL: trả node ĐÍCH, đúng MỘT BƯỚC. Hai route
# đọc cùng dữ liệu thì không được hành xử khác nhau.
#
# Lưu ý cho caller: node A đã merge vào B thì hàm này trả về B — `id` trong kết
# quả KHÁC `node_id` truyền vào. Đó là chủ đích, không phải bug.
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
    """Một node theo id, đã resolve merge. None nếu không có.

    KHÔNG có khái niệm "node chưa duyệt": `ks.nodes` không mang cột status, node
    chỉ tồn tại SAU khi `ks.cli accept` chạy (xem ks/confirm.py). Id của một
    extracted_concept chưa accept đơn giản là không phải node id → None.
    """
    with conn.cursor() as cur:
        cur.execute(_GET_SQL, {"node_id": node_id})
        row = cur.fetchone()
    if row is None:
        return None
    return NodeSummary(id=row[0], title=row[1], subject=row[2], summary=row[3])
