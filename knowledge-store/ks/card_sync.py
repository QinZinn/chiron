"""Job đẩy node đã duyệt sang Mnemosyne thành card.

Quét node chưa có card, gọi POST /cards/from_node cho từng node, ghi kết quả
vào ks.card_sync_log. Scheduler là việc của systemd timer, không nằm trong đây.

XỬ LÝ LỖI THEO `reason`, KHÔNG RETRY MÙ — xem _decide().
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

import psycopg

from ks import settings


class CardClientError(Exception):
    """Không gọi được Mnemosyne (mạng/cấu hình). Khác với lỗi Mnemosyne TRẢ VỀ."""


@dataclass(frozen=True)
class CardSyncOutcome:
    node_id: UUID
    status: str  # 'sent' | 'failed' | 'skipped' | 'pending'
    http_status: int | None
    reason: str | None
    error: str | None
    attempts: int


@dataclass(frozen=True)
class CardSyncResult:
    outcomes: tuple[CardSyncOutcome, ...]

    def by_status(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for o in self.outcomes:
            out[o.status] = out.get(o.status, 0) + 1
        return out


class CardClient(Protocol):
    """Gọi POST /cards/from_node. Trả (http_status, body_json). Fake thay được."""

    def create_card(self, node_id: UUID, study_set_id: UUID) -> tuple[int, Any]: ...


# ---------------------------------------------------------------- client thật


class HttpCardClient:
    """Client HTTP thật tới Mnemosyne."""

    def __init__(
        self,
        base_url: str,
        token: str = "",
        *,
        timeout: int = settings.DEFAULT_MNEMOSYNE_TIMEOUT,
    ):
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout

    def create_card(self, node_id: UUID, study_set_id: UUID) -> tuple[int, Any]:
        url = f"{self._base_url}/cards/from_node"
        # Cả hai field bắt buộc và đều là UUID. Gửi tên set thay cho id sẽ bị
        # serde phía Mnemosyne từ chối bằng 400.
        body = json.dumps({"study_set_id": str(study_set_id), "node_id": str(node_id)})
        # Mnemosyne CHƯA có auth layer (simplification có chủ ý, ghi trong README
        # của họ) — /cards/from_node không có extractor auth nào. Gửi header khi
        # có token để sẵn sàng cho lúc họ thêm auth, còn thiếu token thì vẫn gọi
        # được chứ không tự chặn mình.
        headers = {"content-type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        req = urllib.request.Request(
            url, data=body.encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return (resp.status, _read_json(resp.read()))
        except urllib.error.HTTPError as exc:
            return (exc.code, _read_json(exc.read()))
        except urllib.error.URLError as exc:
            # Không tới được Mnemosyne — hạ tầng, không phải quyết định của node.
            raise CardClientError(f"không gọi được Mnemosyne: {exc.reason}") from exc
        except TimeoutError as exc:
            raise CardClientError("timeout khi gọi Mnemosyne") from exc


def _read_json(raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def client_from_env(env: dict[str, str] | None = None) -> HttpCardClient:
    env = os.environ if env is None else env
    url = env.get(settings.MNEMOSYNE_URL_ENV, "")
    if not url:
        raise CardClientError(f"Thiếu biến môi trường {settings.MNEMOSYNE_URL_ENV}")
    raw_timeout = env.get(settings.MNEMOSYNE_TIMEOUT_ENV, "")
    try:
        timeout = int(raw_timeout) if raw_timeout else settings.DEFAULT_MNEMOSYNE_TIMEOUT
    except ValueError as exc:
        raise CardClientError(
            f"{settings.MNEMOSYNE_TIMEOUT_ENV}='{raw_timeout}' không phải số nguyên"
        ) from exc
    # Token KHÔNG bắt buộc: Mnemosyne chưa có auth layer.
    return HttpCardClient(url, env.get(settings.MNEMOSYNE_TOKEN_ENV, ""), timeout=timeout)


def study_set_id_from_env(env: dict[str, str] | None = None) -> UUID:
    """Id của study set đích. Set được tạo MỘT LẦN ngoài job, id nằm trong .env.

    Không tự tạo set trong job: Mnemosyne không có unique constraint trên tên
    set, nên mỗi lần chạy sẽ đẻ thêm một set "KS review" mới.
    """
    env = os.environ if env is None else env
    raw = env.get(settings.CARD_SYNC_STUDY_SET_ID_ENV, "")
    if not raw:
        raise CardClientError(
            f"Thiếu biến môi trường {settings.CARD_SYNC_STUDY_SET_ID_ENV}."
            f" Tạo set '{settings.CARD_SYNC_STUDY_SET_NAME}' bên Mnemosyne một lần"
            f" (POST /study_sets) rồi điền id trả về vào .env."
        )
    try:
        return UUID(raw)
    except ValueError as exc:
        raise CardClientError(
            f"{settings.CARD_SYNC_STUDY_SET_ID_ENV}='{raw}' không phải UUID."
            f" Mnemosyne nhận study_set_id là UUID, không phải tên set."
        ) from exc


# ---------------------------------------------------------------- bảng quyết định


def _extract_reason(body: Any) -> str | None:
    if isinstance(body, dict):
        reason = body.get("reason")
        if isinstance(reason, str):
            return reason
    return None


def _decide(http_status: int, body: Any, attempts: int, max_attempts: int) -> tuple[str, str | None]:
    """(status mới, mô tả lỗi). Bảng quyết định — KHÔNG retry mù.

    | mã / reason              | quyết định                                    |
    |--------------------------|-----------------------------------------------|
    | 2xx                      | sent                                          |
    | 409                      | sent — đã có card, không tốn LLM bên Mnemosyne |
    | 404                      | skipped — set/node sai, retry không giúp gì    |
    | 503                      | pending — Mnemosyne chưa nối được KS, thử lại  |
    | 502 truncated            | failed NGAY, KHÔNG retry cùng input            |
    | 502 provider_error       | pending tới khi cạn lượt, rồi failed           |
    | 502 knowledge_store_error| pending — lỗi tạm thời, retry an toàn          |
    """
    reason = _extract_reason(body)
    detail = json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body
    # KHÔNG rút gọn: xem COMMENT trên cột last_error trong 0004.
    raw = f"http={http_status} reason={reason!r} body={detail}"

    if 200 <= http_status < 300:
        return ("sent", None)
    if http_status == 409:
        return ("sent", None)
    if http_status == 404:
        return ("skipped", raw)
    if http_status == 503:
        return ("pending", raw)

    if reason == "truncated":
        # LLM bị cắt giữa chừng — gọi lại cùng input gần như chắc chắn lặp lại y
        # hệt. Nhánh này CHƯA từng verify qua API thật (xem NOTES.md).
        return ("failed", raw)
    if reason == "knowledge_store_error":
        return ("pending", raw)
    if reason == "provider_error":
        return ("failed" if attempts >= max_attempts else "pending", raw)

    # reason lạ hoặc thiếu → coi như provider_error, có giới hạn.
    return ("failed" if attempts >= max_attempts else "pending", raw)


# ---------------------------------------------------------------- quét & chạy


_PENDING_SQL = """
SELECT n.id
FROM ks.nodes n
LEFT JOIN ks.card_sync_log l ON l.node_id = n.id
WHERE n.merged_into_id IS NULL
  AND (l.node_id IS NULL OR l.status = 'pending')
ORDER BY n.created_at
LIMIT %s
"""


def pending_nodes(
    conn: psycopg.Connection, *, limit: int = settings.CARD_SYNC_BATCH_LIMIT
) -> tuple[UUID, ...]:
    """Node đã duyệt, chưa gửi xong.

    Node chỉ tồn tại trong ks.nodes SAU khi `ks.cli accept` chạy, nên có mặt ở
    đây đã đồng nghĩa "đã duyệt". Bỏ qua node đã merge — card thuộc về node đích.

    'failed' và 'skipped' KHÔNG được chọn lại: đó là quyết định cuối, retry
    không giúp gì và sẽ đốt LLM bên Mnemosyne vô ích.
    """
    with conn.cursor() as cur:
        cur.execute(_PENDING_SQL, (limit,))
        return tuple(r[0] for r in cur.fetchall())


def _record(conn, node_id, status, attempts, error) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ks.card_sync_log (node_id, status, attempts, attempted_at, last_error)"
            " VALUES (%s, %s, %s, now(), %s)"
            " ON CONFLICT (node_id) DO UPDATE SET"
            "   status = EXCLUDED.status,"
            "   attempts = EXCLUDED.attempts,"
            "   attempted_at = EXCLUDED.attempted_at,"
            "   last_error = EXCLUDED.last_error",
            (node_id, status, attempts, error),
        )


def _current_attempts(conn, node_id) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT attempts FROM ks.card_sync_log WHERE node_id = %s", (node_id,))
        row = cur.fetchone()
    return row[0] if row else 0


def sync_cards(
    conn: psycopg.Connection,
    client: CardClient,
    *,
    study_set_id: UUID | None = None,
    limit: int = settings.CARD_SYNC_BATCH_LIMIT,
    max_attempts: int = settings.MAX_CARD_SYNC_ATTEMPTS,
) -> CardSyncResult:
    """Đẩy từng node chưa có card sang Mnemosyne.

    KHÔNG raise vì một node lỗi: mỗi node ghi kết quả riêng rồi đi tiếp. Nhưng
    CardClientError (không gọi nổi Mnemosyne) thì dừng cả lô — gọi tiếp 99 node
    nữa để nhận cùng một lỗi mạng là vô nghĩa, và sẽ đốt hết lượt retry của
    chúng vì lý do không liên quan gì tới node.

    Không commit — transaction thuộc về caller.
    """
    if study_set_id is None:
        study_set_id = study_set_id_from_env()

    outcomes: list[CardSyncOutcome] = []
    for node_id in pending_nodes(conn, limit=limit):
        attempts = _current_attempts(conn, node_id) + 1
        try:
            http_status, body = client.create_card(node_id, study_set_id)
        except CardClientError as exc:
            # Hạ tầng hỏng: giữ nguyên lượt đã dùng, để tick sau thử lại.
            _record(conn, node_id, "pending", attempts - 1, f"CardClientError: {exc}")
            outcomes.append(
                CardSyncOutcome(node_id, "pending", None, None, str(exc), attempts - 1)
            )
            break

        status, error = _decide(http_status, body, attempts, max_attempts)
        _record(conn, node_id, status, attempts, error)
        outcomes.append(
            CardSyncOutcome(
                node_id=node_id,
                status=status,
                http_status=http_status,
                reason=_extract_reason(body),
                error=error,
                attempts=attempts,
            )
        )

    return CardSyncResult(outcomes=tuple(outcomes))
