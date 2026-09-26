"""Job that pushes reviewed nodes to Mnemosyne as cards.

Scans nodes without a card, calls POST /cards/from_node for each, and records the
result in ks.card_sync_log. Scheduling is the systemd timer's job, not this module's.

ERRORS ARE HANDLED BY `reason`, NO BLIND RETRIES — see _decide().
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
    """Could not call Mnemosyne (network/config). Distinct from an error Mnemosyne RETURNS."""


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
    """Call POST /cards/from_node. Returns (http_status, body_json). Replaceable with a fake."""

    def create_card(self, node_id: UUID, study_set_id: UUID) -> tuple[int, Any]: ...


# ---------------------------------------------------------------- real client


class HttpCardClient:
    """The real HTTP client for Mnemosyne."""

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
        # Both fields are required and both are UUIDs. Sending the set's name instead
        # of its id is rejected by Mnemosyne's serde with a 400.
        body = json.dumps({"study_set_id": str(study_set_id), "node_id": str(node_id)})
        # Mnemosyne has NO auth layer on this route yet (a deliberate simplification
        # noted in its README) — /cards/from_node has no auth extractor. The header is
        # sent when a token exists, ready for when auth is added; without a token the
        # call still goes out rather than blocking itself.
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
            # Mnemosyne unreachable — infrastructure, not a decision about the node.
            raise CardClientError(f"could not reach Mnemosyne: {exc.reason}") from exc
        except TimeoutError as exc:
            raise CardClientError("timed out calling Mnemosyne") from exc


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
        raise CardClientError(f"Missing environment variable {settings.MNEMOSYNE_URL_ENV}")
    raw_timeout = env.get(settings.MNEMOSYNE_TIMEOUT_ENV, "")
    try:
        timeout = int(raw_timeout) if raw_timeout else settings.DEFAULT_MNEMOSYNE_TIMEOUT
    except ValueError as exc:
        raise CardClientError(
            f"{settings.MNEMOSYNE_TIMEOUT_ENV}='{raw_timeout}' is not an integer"
        ) from exc
    # The token is NOT required: Mnemosyne has no auth layer here yet.
    return HttpCardClient(url, env.get(settings.MNEMOSYNE_TOKEN_ENV, ""), timeout=timeout)


def study_set_id_from_env(env: dict[str, str] | None = None) -> UUID:
    """Id of the target study set. The set is created ONCE outside the job; its id lives in .env.

    The job does not create the set itself: Mnemosyne has no unique constraint on
    set names, so every run would add yet another "KS review" set.
    """
    env = os.environ if env is None else env
    raw = env.get(settings.CARD_SYNC_STUDY_SET_ID_ENV, "")
    if not raw:
        raise CardClientError(
            f"Missing environment variable {settings.CARD_SYNC_STUDY_SET_ID_ENV}."
            f" Create the '{settings.CARD_SYNC_STUDY_SET_NAME}' set in Mnemosyne once"
            f" (POST /study_sets) and put the returned id in .env."
        )
    try:
        return UUID(raw)
    except ValueError as exc:
        raise CardClientError(
            f"{settings.CARD_SYNC_STUDY_SET_ID_ENV}='{raw}' is not a UUID."
            f" Mnemosyne takes study_set_id as a UUID, not a set name."
        ) from exc


# ---------------------------------------------------------------- decision table


def _extract_reason(body: Any) -> str | None:
    if isinstance(body, dict):
        reason = body.get("reason")
        if isinstance(reason, str):
            return reason
    return None


def _decide(http_status: int, body: Any, attempts: int, max_attempts: int) -> tuple[str, str | None]:
    """(new status, error description). A decision table — NO blind retries.

    | code / reason            | decision                                          |
    |--------------------------|---------------------------------------------------|
    | 2xx                      | sent                                              |
    | 409                      | sent — card already exists, no LLM spent in Mnemosyne |
    | 404                      | skipped — wrong set/node, retrying will not help  |
    | 503                      | pending — Mnemosyne cannot reach KS yet, retry    |
    | 502 truncated            | failed AT ONCE, NO retry with the same input      |
    | 502 provider_error       | pending until attempts run out, then failed       |
    | 502 knowledge_store_error| pending — transient error, safe to retry          |
    """
    reason = _extract_reason(body)
    detail = json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body
    # NOT shortened: see the COMMENT on the last_error column in 0004.
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
        # The LLM was cut off mid-answer — calling again with the same input almost
        # certainly repeats it exactly. This branch has NEVER been verified against the real API (see NOTES.md).
        return ("failed", raw)
    if reason == "knowledge_store_error":
        return ("pending", raw)
    if reason == "provider_error":
        return ("failed" if attempts >= max_attempts else "pending", raw)

    # Unknown or missing reason → treated as provider_error, with a limit.
    return ("failed" if attempts >= max_attempts else "pending", raw)


# ---------------------------------------------------------------- scan & run


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
    """Reviewed nodes that have not been sent yet.

    A node only exists in ks.nodes AFTER `ks.cli accept` runs, so being here already
    means "reviewed". Merged nodes are skipped — the card belongs to the target node.

    'failed' and 'skipped' are NOT picked again: those are final decisions; retrying
    does not help and would burn Mnemosyne's LLM for nothing.
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
    """Push each node without a card to Mnemosyne.

    Does NOT raise because one node failed: each node records its own result and the
    run moves on. But CardClientError (Mnemosyne unreachable) stops the whole batch —
    calling 99 more nodes to get the same network error is pointless, and would burn
    their retry attempts for a reason that has nothing to do with the nodes.

    No commit — the transaction belongs to the caller.
    """
    if study_set_id is None:
        study_set_id = study_set_id_from_env()

    outcomes: list[CardSyncOutcome] = []
    for node_id in pending_nodes(conn, limit=limit):
        attempts = _current_attempts(conn, node_id) + 1
        try:
            http_status, body = client.create_card(node_id, study_set_id)
        except CardClientError as exc:
            # Infrastructure is down: keep the attempts already used, so the next tick retries.
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
