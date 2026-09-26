"""Edge suggestion, review, and reading the graph."""

from __future__ import annotations

import json

import pytest

from ks import edges
from ks.edges import (
    EdgeNotFound,
    NodeNotFound,
    add_edge,
    approve_edge,
    candidate_neighbors,
    edit_edge,
    list_pending,
    neighbors,
    parse_suggestions,
    reject_edge,
    suggest_edges,
)
from ks.llm import LLMQuotaError, LLMTransientError
from ks.models import ConceptDraft, RelationType, SourceModule
from ks.ingest import ingest_concepts


class FakeProvider:
    """A fake provider: returns preset text or raises a preset error."""

    name = "fake"
    model = "fake-1"

    def __init__(self, text: str = "[]", error: Exception | None = None):
        self.text = text
        self.error = error
        self.calls: list[list] = []

    def complete(self, messages, *, max_tokens=1000, temperature=0.0) -> str:
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        return self.text


def _mk(conn, title, subject="Vật lý", summary="x"):
    draft = ConceptDraft(title, subject, summary, SourceModule.MNEMOSYNE)
    return ingest_concepts(conn, [draft]).ingested[0].node_id


@pytest.fixture
def graph(conn):
    """Three concepts with very different names — §12: avoid near-identical fixtures."""
    a = _mk(conn, "Quang hợp", "Sinh học", "Cây dùng ánh sáng tạo chất hữu cơ.")
    b = _mk(conn, "Hô hấp tế bào", "Sinh học", "Tế bào giải phóng năng lượng từ glucose.")
    c = _mk(conn, "Chiến tranh Lạnh", "Lịch sử", "Đối đầu Mỹ - Liên Xô.")
    return conn, a, b, c


# ---------------------------------------------------------------- candidate set


def test_candidate_set_excludes_the_node_itself(graph):
    conn, a, _, _ = graph
    assert all(c.node_id != a for c in candidate_neighbors(conn, a))


def test_candidate_set_prefers_the_same_subject(graph):
    conn, a, b, c = graph
    ids = [x.node_id for x in candidate_neighbors(conn, a)]
    assert ids[0] == b, "the same subject (Biology) must come before History"


def test_candidate_set_skips_merged_nodes(graph):
    conn, a, b, c = graph
    with conn.cursor() as cur:
        cur.execute("UPDATE ks.nodes SET merged_into_id = %s WHERE id = %s", (c, b))
    assert b not in [x.node_id for x in candidate_neighbors(conn, a)]


def test_candidate_set_excludes_rejected_pairs(graph):
    """Never ask again a question the user already answered."""
    conn, a, b, _ = graph
    edge_id = add_edge(conn, a, b, RelationType.RELATED)
    reject_edge(conn, edge_id)
    assert b not in [x.node_id for x in candidate_neighbors(conn, a)]


def test_candidate_set_excludes_approved_pairs(graph):
    conn, a, b, _ = graph
    add_edge(conn, a, b, RelationType.RELATED)
    assert b not in [x.node_id for x in candidate_neighbors(conn, a)]


def test_candidate_set_respects_k(graph):
    conn, a, _, _ = graph
    assert len(candidate_neighbors(conn, a, k=1)) == 1


def test_missing_node_raises(conn):
    import uuid
    with pytest.raises(NodeNotFound):
        candidate_neighbors(conn, uuid.uuid4())


# ---------------------------------------------------------------- parse


def test_parse_skips_out_of_range_indexes():
    text = json.dumps([{"candidate": 99, "relation_type": "related", "reason": "r"}])
    assert parse_suggestions(text, 2) == ()


def test_parse_skips_unknown_relation_types():
    text = json.dumps([{"candidate": 0, "relation_type": "something", "reason": "r"}])
    assert parse_suggestions(text, 2) == ()


def test_parse_unwraps_a_code_fence():
    text = '```json\n[{"candidate": 0, "relation_type": "related", "reason": "r"}]\n```'
    assert parse_suggestions(text, 2) == ((0, RelationType.RELATED, "r"),)


def test_parse_empty_array_is_valid():
    assert parse_suggestions("[]", 3) == ()


def test_parse_non_json_gives_parse_error():
    from ks.llm import LLMParseError
    with pytest.raises(LLMParseError):
        parse_suggestions("sorry, I am not sure", 2)


def test_parse_json_that_is_not_an_array_gives_parse_error():
    from ks.llm import LLMParseError
    with pytest.raises(LLMParseError):
        parse_suggestions('{"candidate": 0}', 2)


def test_parse_drops_duplicate_candidates():
    text = json.dumps([
        {"candidate": 0, "relation_type": "related", "reason": "a"},
        {"candidate": 0, "relation_type": "prerequisite", "reason": "b"},
    ])
    assert len(parse_suggestions(text, 2)) == 1


# ---------------------------------------------------------------- suggest


def test_suggest_writes_edges_as_pending(graph):
    """The LLM only SUGGESTS — no auto-approve."""
    conn, a, b, _ = graph
    provider = FakeProvider(json.dumps([{"candidate": 0, "relation_type": "prerequisite", "reason": "r"}]))
    run = suggest_edges(conn, a, provider)
    assert run.outcome == "ok"
    assert len(run.suggestions) == 1
    with conn.cursor() as cur:
        cur.execute("SELECT status, suggested_by FROM ks.edges WHERE id = %s",
                    (run.suggestions[0].edge_id,))
        assert cur.fetchone() == ("pending", "llm")


def test_suggest_without_candidates_does_not_call_the_llm(conn):
    a = _mk(conn, "Quang hợp", "Sinh học")
    provider = FakeProvider()
    run = suggest_edges(conn, a, provider)
    assert run.outcome == "no_candidates"
    assert provider.calls == []


def test_suggest_llm_error_does_NOT_raise(graph):
    """Raising would make the caller roll back and lose the instrumentation row."""
    conn, a, _, _ = graph
    run = suggest_edges(conn, a, FakeProvider(error=LLMQuotaError("429")))
    assert run.outcome == "llm_error"
    assert run.suggestions == ()
    assert "429" in run.error


def test_suggest_parse_errors_are_separate_from_llm_errors(graph):
    conn, a, _, _ = graph
    run = suggest_edges(conn, a, FakeProvider("not json"))
    assert run.outcome == "parse_error"


def test_suggest_does_not_overwrite_decided_edges(graph):
    conn, a, b, _ = graph
    edge_id = add_edge(conn, a, b, RelationType.RELATED)
    reject_edge(conn, edge_id)
    provider = FakeProvider(json.dumps([{"candidate": 0, "relation_type": "related", "reason": "r"}]))
    run = suggest_edges(conn, a, provider)
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM ks.edges WHERE id = %s", (edge_id,))
        assert cur.fetchone()[0] == "rejected"


def test_prompt_contains_root_and_candidate_titles(graph):
    conn, a, b, _ = graph
    provider = FakeProvider()
    suggest_edges(conn, a, provider)
    prompt = provider.calls[0][1].content
    assert "Quang hợp" in prompt and "Hô hấp tế bào" in prompt


# ---------------------------------------------------------------- review


def test_list_pending_returns_only_pending(graph):
    conn, a, b, _ = graph
    provider = FakeProvider(json.dumps([{"candidate": 0, "relation_type": "related", "reason": "r"}]))
    suggest_edges(conn, a, provider)
    pending = list_pending(conn)
    assert len(pending) == 1
    assert pending[0].from_title == "Quang hợp"
    assert pending[0].to_title == "Hô hấp tế bào"


def test_approve_changes_status(graph):
    conn, a, b, _ = graph
    provider = FakeProvider(json.dumps([{"candidate": 0, "relation_type": "related", "reason": "r"}]))
    edge_id = suggest_edges(conn, a, provider).suggestions[0].edge_id
    approve_edge(conn, edge_id)
    assert list_pending(conn) == ()


def test_reject_keeps_the_row_forever_no_delete(graph):
    conn, a, b, _ = graph
    provider = FakeProvider(json.dumps([{"candidate": 0, "relation_type": "related", "reason": "r"}]))
    edge_id = suggest_edges(conn, a, provider).suggestions[0].edge_id
    reject_edge(conn, edge_id)
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM ks.edges WHERE id = %s", (edge_id,))
        assert cur.fetchone()[0] == "rejected"


def test_edit_changes_relation_type_and_approves(graph):
    conn, a, b, _ = graph
    provider = FakeProvider(json.dumps([{"candidate": 0, "relation_type": "related", "reason": "r"}]))
    edge_id = suggest_edges(conn, a, provider).suggestions[0].edge_id
    edit_edge(conn, edge_id, RelationType.PREREQUISITE)
    with conn.cursor() as cur:
        cur.execute("SELECT relation_type, status FROM ks.edges WHERE id = %s", (edge_id,))
        assert cur.fetchone() == ("prerequisite", "approved")


def test_missing_edge_raises(conn):
    import uuid
    with pytest.raises(EdgeNotFound):
        approve_edge(conn, uuid.uuid4())


def test_add_edge_goes_straight_to_approved(graph):
    conn, a, b, _ = graph
    edge_id = add_edge(conn, a, b, RelationType.CONTRASTS_WITH)
    with conn.cursor() as cur:
        cur.execute("SELECT status, suggested_by FROM ks.edges WHERE id = %s", (edge_id,))
        assert cur.fetchone() == ("approved", "manual")


def test_add_edge_on_a_rejected_pair_revives_it(graph):
    """The user changed their mind — a manual action may override the earlier decision."""
    conn, a, b, _ = graph
    edge_id = add_edge(conn, a, b, RelationType.RELATED)
    reject_edge(conn, edge_id)
    again = add_edge(conn, a, b, RelationType.RELATED)
    assert again == edge_id
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM ks.edges WHERE id = %s", (edge_id,))
        assert cur.fetchone()[0] == "approved"


# ---------------------------------------------------------------- neighbors


def test_neighbors_returns_only_approved_edges(graph):
    conn, a, b, _ = graph
    provider = FakeProvider(json.dumps([{"candidate": 0, "relation_type": "related", "reason": "r"}]))
    suggest_edges(conn, a, provider)
    assert neighbors(conn, a) == ()


def test_related_is_symmetric_queried_both_ways(graph):
    """Stored one way a→b, but neighbors(b) must still see a."""
    conn, a, b, _ = graph
    add_edge(conn, a, b, RelationType.RELATED)
    from_b = neighbors(conn, b)
    assert len(from_b) == 1
    assert from_b[0].node_id == a
    assert from_b[0].direction == "both"


def test_contrasts_with_is_symmetric(graph):
    conn, a, b, _ = graph
    add_edge(conn, a, b, RelationType.CONTRASTS_WITH)
    assert neighbors(conn, b)[0].direction == "both"


def test_prerequisite_is_directed(graph):
    conn, a, b, _ = graph
    add_edge(conn, a, b, RelationType.PREREQUISITE)
    assert neighbors(conn, a)[0].direction == "out"
    assert neighbors(conn, b)[0].direction == "in"


def test_suggest_uses_a_generous_token_budget(graph):
    """A cut-off ruins the whole batch of suggestions, so the budget must be generous."""
    from ks import settings

    class Ghi(FakeProvider):
        def complete(self, messages, *, max_tokens=1000, temperature=0.0):
            self.max_tokens = max_tokens
            return "[]"

    conn, a, _, _ = graph
    provider = Ghi()
    suggest_edges(conn, a, provider)
    assert provider.max_tokens == settings.EDGE_SUGGESTION_MAX_TOKENS >= 4000


def test_truncated_response_is_recorded_as_llm_error_not_parse_error(graph):
    """LLMTruncatedError is a subclass of LLMTransientError → outcome 'llm_error',
    so the transcript/run keeps its retry instead of being treated as a structural error."""
    from ks.llm import LLMTruncatedError
    conn, a, _, _ = graph
    run = suggest_edges(conn, a, FakeProvider(error=LLMTruncatedError("out of max_tokens")))
    assert run.outcome == "llm_error"
