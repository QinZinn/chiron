"""card_sync: scan nodes, call Mnemosyne, handle errors BY `reason` — no blind retries."""

from __future__ import annotations

from uuid import UUID

import pytest

from ks import settings
from ks.card_sync import (
    CardClientError,
    CardSyncOutcome,
    client_from_env,
    pending_nodes,
    study_set_id_from_env,
    sync_cards,
)
from ks.ingest import ingest_concepts
from ks.models import ConceptDraft, SourceModule

# Mnemosyne takes study_set_id as a UUID, not a set name.
SET_ID = UUID("ae2c0db6-4a7e-4956-9bb9-ffe25eeaf151")

# The VERBATIM 502 body Mnemosyne returns on the real wire (they forced truncation through
# the real API by temporarily patching max_tokens=200). Note there is NO "message" field — the
# old test here made that field up. Anchor tests on the real wire, not on imagination.
TRUNCATED_BODY = {
    "error": (
        "DeepSeek stopped mid-answer at its token limit (length); nothing was "
        "parsed. Retrying, or requesting fewer items, may succeed."
    ),
    "reason": "truncated",
}


class FakeCardClient:
    """Returns scripted (status, body) pairs. Records every call."""

    def __init__(self, *responses, error: Exception | None = None):
        self._responses = list(responses)
        self._error = error
        self.calls: list[tuple[UUID, str]] = []

    def create_card(self, node_id, study_set_id):
        self.calls.append((node_id, study_set_id))
        if self._error is not None:
            raise self._error
        if not self._responses:
            return (200, {"card_id": "x"})
        return self._responses.pop(0)


def _mk(conn, title, subject="Vật lý", summary="x"):
    draft = ConceptDraft(title, subject, summary, SourceModule.MNEMOSYNE)
    return ingest_concepts(conn, [draft]).ingested[0].node_id


def _log(conn, node_id):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT status, attempts, last_error, attempted_at IS NOT NULL"
            " FROM ks.card_sync_log WHERE node_id = %s",
            (node_id,),
        )
        return cur.fetchone()


@pytest.fixture
def node(conn):
    return conn, _mk(conn, "Quang hợp", "Sinh học", "Cây dùng ánh sáng.")


# ---------------------------------------------------------------- scan


def test_never_sent_node_is_picked(node):
    conn, nid = node
    assert pending_nodes(conn) == (nid,)


def test_sent_node_is_not_picked_again(node):
    conn, nid = node
    sync_cards(conn, study_set_id=SET_ID, client=FakeCardClient((201, {"card_id": "c1"})))
    assert pending_nodes(conn) == ()


def test_failed_node_is_NOT_picked_again(node):
    """failed is final — a retry would burn Mnemosyne's LLM for nothing."""
    conn, nid = node
    sync_cards(conn, study_set_id=SET_ID, client=FakeCardClient((502, TRUNCATED_BODY)))
    assert _log(conn, nid)[0] == "failed"
    assert pending_nodes(conn) == ()


def test_skipped_node_is_NOT_picked_again(node):
    conn, nid = node
    sync_cards(conn, study_set_id=SET_ID, client=FakeCardClient((404, {"error": "set not found"})))
    assert pending_nodes(conn) == ()


def test_pending_node_IS_picked_again(node):
    conn, nid = node
    sync_cards(conn, study_set_id=SET_ID, client=FakeCardClient((503, {"error": "KS not configured"})))
    assert pending_nodes(conn) == (nid,)


def test_merged_node_is_skipped(conn):
    """The card belongs to the target node, not to the node that is gone."""
    a = _mk(conn, "Quang hợp", "Sinh học")
    b = _mk(conn, "Chiến tranh Lạnh", "Lịch sử")
    with conn.cursor() as cur:
        cur.execute("UPDATE ks.nodes SET merged_into_id = %s WHERE id = %s", (b, a))
    assert pending_nodes(conn) == (b,)


def test_respects_limit(conn):
    for t in ("Quang hợp", "Chiến tranh Lạnh", "Phương trình bậc hai"):
        _mk(conn, t, t)
    assert len(pending_nodes(conn, limit=2)) == 2


# ---------------------------------------------------------------- decision table


@pytest.mark.parametrize(
    "http_status, body, expected",
    [
        (200, {"card_id": "c1"}, "sent"),
        (201, {"card_id": "c1"}, "sent"),
        # 409 is NOT an error: the card exists; Mnemosyne confirms no LLM was spent.
        (409, {"error": "card already exists"}, "sent"),
        # 404: wrong set or node — bad data, retrying will not help.
        (404, {"error": "study set not found"}, "skipped"),
        # 503: Mnemosyne cannot reach KS yet — their side's error, try again later.
        (503, {"error": "knowledge store not configured"}, "pending"),
        # 502 + truncated: NO retry with the same input.
        (502, TRUNCATED_BODY, "failed"),
        # 502 + knowledge_store_error: safe to retry.
        (502, {"reason": "knowledge_store_error", "message": "GET /nodes failed"}, "pending"),
        # 502 + provider_error: limited retries; the first time it is still pending.
        (502, {"reason": "provider_error", "message": "network"}, "pending"),
    ],
)
def test_decision_table_by_reason(node, http_status, body, expected):
    conn, nid = node
    result = sync_cards(conn, study_set_id=SET_ID, client=FakeCardClient((http_status, body)))
    assert result.outcomes[0].status == expected
    assert _log(conn, nid)[0] == expected


def test_provider_error_out_of_attempts_becomes_failed(node):
    """LIMITED retries — not blind escalating retries."""
    conn, nid = node
    body = (502, {"reason": "provider_error", "message": "network"})
    for _ in range(settings.MAX_CARD_SYNC_ATTEMPTS):
        sync_cards(conn, study_set_id=SET_ID, client=FakeCardClient(body))
    status, attempts, _, _ = _log(conn, nid)
    assert (status, attempts) == ("failed", settings.MAX_CARD_SYNC_ATTEMPTS)
    assert pending_nodes(conn) == ()


def test_truncated_fails_AT_ONCE_on_the_first_try_without_using_up_attempts(node):
    """Unlike provider_error: truncated gets no retries at all."""
    conn, nid = node
    sync_cards(conn, study_set_id=SET_ID, client=FakeCardClient((502, TRUNCATED_BODY)))
    status, attempts, _, _ = _log(conn, nid)
    assert status == "failed"
    assert attempts == 1, "truncated must fail on the FIRST attempt"


def test_knowledge_store_error_retries_are_not_limited_by_the_provider_budget(node):
    """A transient infrastructure error — the timer retries; attempts do not run out as with provider_error."""
    conn, nid = node
    body = (502, {"reason": "knowledge_store_error", "message": "KS timeout"})
    for _ in range(settings.MAX_CARD_SYNC_ATTEMPTS + 2):
        sync_cards(conn, study_set_id=SET_ID, client=FakeCardClient(body))
    assert _log(conn, nid)[0] == "pending"
    assert pending_nodes(conn) == (nid,)


def test_unknown_reason_is_handled_like_provider_error(node):
    conn, nid = node
    sync_cards(conn, study_set_id=SET_ID, client=FakeCardClient((502, {"reason": "never seen before"})))
    assert _log(conn, nid)[0] == "pending"


def test_502_without_reason_does_not_crash(node):
    conn, nid = node
    result = sync_cards(conn, study_set_id=SET_ID, client=FakeCardClient((502, "a plain-text error")))
    assert result.outcomes[0].status == "pending"


# ---------------------------------------------------------------- log


def test_last_error_stores_reason_and_message_VERBATIM(node):
    """REQUIRED: the truncated branch has never been verified against the real API, so this
    log row is the only evidence for checking the behaviour the first time it happens for real."""
    conn, nid = node
    sync_cards(conn, study_set_id=SET_ID, client=FakeCardClient((502, TRUNCATED_BODY)))
    _, _, last_error, _ = _log(conn, nid)
    assert "truncated" in last_error
    # Mnemosyne's message must survive verbatim in the log, not shortened.
    assert "stopped mid-answer at its token limit" in last_error
    assert "502" in last_error


def test_records_attempted_at(node):
    conn, nid = node
    sync_cards(conn, study_set_id=SET_ID, client=FakeCardClient((201, {})))
    assert _log(conn, nid)[3] is True


def test_sent_leaves_last_error_None(node):
    conn, nid = node
    sync_cards(conn, study_set_id=SET_ID, client=FakeCardClient((201, {})))
    assert _log(conn, nid)[2] is None


# ---------------------------------------------------------------- study_set_id


def test_uses_ONE_fixed_study_set_NOT_mapped_by_subject(conn):
    """subject is free TEXT; there is no evidence about its distribution to design a mapping."""
    _mk(conn, "Quang hợp", "Sinh học")
    _mk(conn, "Chiến tranh Lạnh", "Lịch sử")
    client = FakeCardClient((201, {}), (201, {}))
    sync_cards(conn, client, study_set_id=SET_ID)
    assert {s for _, s in client.calls} == {SET_ID}, "every node goes into the same set"


def test_missing_study_set_id_reports_how_to_fix_it():
    with pytest.raises(CardClientError, match="POST /study_sets"):
        study_set_id_from_env({})


def test_study_set_id_given_as_a_name_reports_a_clear_error(conn):
    """KS originally assumed it should send the name "KS review" — Mnemosyne returns 400 because
    serde cannot parse it as a Uuid. Stop it in KS with a clear message."""
    with pytest.raises(CardClientError, match="is not a UUID"):
        study_set_id_from_env({"KS_CARD_SYNC_STUDY_SET_ID": "KS review"})


def test_valid_study_set_id_parses():
    got = study_set_id_from_env(
        {"KS_CARD_SYNC_STUDY_SET_ID": "ae2c0db6-4a7e-4956-9bb9-ffe25eeaf151"})
    assert got == SET_ID


def test_request_body_has_exactly_two_uuid_fields(conn):
    """Both fields are required, both are UUIDs, no path/query parameters."""
    import json
    nid = _mk(conn, "Quang hợp", "Sinh học")
    sent = {}

    class Ghi:
        def create_card(self, node_id, study_set_id):
            sent["body"] = json.dumps(
                {"study_set_id": str(study_set_id), "node_id": str(node_id)})
            return (201, {})

    sync_cards(conn, Ghi(), study_set_id=SET_ID)
    assert json.loads(sent["body"]) == {
        "study_set_id": str(SET_ID), "node_id": str(nid)
    }


# ---------------------------------------------------------------- infrastructure errors


def test_unreachable_mnemosyne_STOPS_the_batch(conn):
    """Calling 99 more nodes to get the same network error is pointless, and would burn
    their retry attempts for a reason unrelated to the nodes."""
    for t in ("Quang hợp", "Chiến tranh Lạnh", "Phương trình bậc hai"):
        _mk(conn, t, t)
    client = FakeCardClient(error=CardClientError("connection refused"))
    result = sync_cards(conn, client, study_set_id=SET_ID)
    assert len(client.calls) == 1, "must stop after the first node"
    assert result.outcomes[0].status == "pending"


def test_infrastructure_errors_do_NOT_burn_retry_attempts(node):
    conn, nid = node
    sync_cards(conn, study_set_id=SET_ID, client=FakeCardClient(error=CardClientError("connection refused")))
    status, attempts, last_error, _ = _log(conn, nid)
    assert (status, attempts) == ("pending", 0)
    assert "connection refused" in last_error


def test_one_failing_node_does_not_block_the_next(conn):
    a = _mk(conn, "Quang hợp", "Sinh học")
    b = _mk(conn, "Chiến tranh Lạnh", "Lịch sử")
    client = FakeCardClient((404, {"error": "x"}), (201, {"card_id": "c"}))
    result = sync_cards(conn, client, study_set_id=SET_ID)
    assert [o.status for o in result.outcomes] == ["skipped", "sent"]


def test_does_not_commit(node):
    conn, nid = node
    sync_cards(conn, study_set_id=SET_ID, client=FakeCardClient((201, {})))
    conn.rollback()
    assert _log(conn, nid) is None


# ---------------------------------------------------------------- configuration


def test_missing_url_reports_a_clear_error():
    with pytest.raises(CardClientError, match="KS_MNEMOSYNE_URL"):
        client_from_env({})


def test_token_is_NOT_required(monkeypatch):
    """Mnemosyne has no auth layer here — /cards/from_node has no auth extractor.
    Requiring a token on the KS side would block ourselves for nothing."""
    client = client_from_env({"KS_MNEMOSYNE_URL": "http://127.0.0.1:8081"})
    assert client is not None


# ---------------------------------------------------------------- timeout


def test_default_timeout_is_wide_enough_for_a_reasoning_model():
    """MEASURED: a node with a ~1900-character summary took 11 s; a ~2900-character node took over 30 s.

    A timeout that is too short is WORSE than slow — if KS gives up before Mnemosyne answers,
    the real classification is lost (truncated / provider_error / knowledge_store_error),
    all overwritten as CardClientError. It hid exactly one case we needed to see.
    """
    assert settings.DEFAULT_MNEMOSYNE_TIMEOUT >= 120


def test_timeout_is_read_from_env():
    client = client_from_env({
        "KS_MNEMOSYNE_URL": "http://127.0.0.1:8081",
        "KS_MNEMOSYNE_TIMEOUT": "240",
    })
    assert client._timeout == 240


def test_non_numeric_timeout_reports_a_clear_error():
    with pytest.raises(CardClientError, match="KS_MNEMOSYNE_TIMEOUT"):
        client_from_env({
            "KS_MNEMOSYNE_URL": "http://127.0.0.1:8081",
            "KS_MNEMOSYNE_TIMEOUT": "long",
        })


# ---------------------------------------------------------------- orphaned results


def test_timeout_then_409_reconciles_an_orphaned_result(node):
    """KEY INVARIANT: a timeout on the KS side does NOT cancel the work on Mnemosyne's side.

    Mnemosyne confirmed by experiment: kill the client after 2 seconds and their handler
    still runs to completion and CREATES THE CARD NORMALLY 3 seconds later. So a timeout
    produces an **orphaned result** — they have a card, KS thinks it failed, the two sides
    believe different things about the same node.

    What reconciles it is the two-step chain below, and both steps are required:
      1. a timeout records `pending` (NOT `failed`) → the node can be picked again
      2. the next run gets 409 → records `sent` (409 is NOT an error)

    Changing either step leaves the orphan stuck forever.
    """
    conn, nid = node

    # Run 1: KS times out. Mnemosyne (invisible from here) still creates the card.
    sync_cards(conn, FakeCardClient(error=CardClientError("timed out calling Mnemosyne")),
               study_set_id=SET_ID)
    status, attempts, last_error, _ = _log(conn, nid)
    assert status == "pending", "a timeout must leave the node for next time"
    assert attempts == 0, "infrastructure errors must not burn retry attempts"
    assert pending_nodes(conn) == (nid,)

    # Run 2: the card exists on the other side → 409 with existing_card_id.
    result = sync_cards(
        conn,
        FakeCardClient((409, {"error": "card already exists",
                              "existing_card_id": "c0ffee00-0000-4000-8000-000000000000"})),
        study_set_id=SET_ID,
    )
    assert result.outcomes[0].status == "sent"
    assert _log(conn, nid)[0] == "sent"
    assert pending_nodes(conn) == (), "reconciled, not repeated"
