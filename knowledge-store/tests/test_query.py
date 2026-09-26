"""ks/query.py — list_nodes: filters, one-step merge resolution, dedup."""

from __future__ import annotations

from ks.ingest import ingest_concepts
from ks.models import ConceptDraft, SourceModule
from ks.query import list_nodes


def _mk(conn, title, subject="Vật lý", summary="x", source=SourceModule.MNEMOSYNE):
    return ingest_concepts(conn, [ConceptDraft(title, subject, summary, source)]).ingested[0].node_id


def _merge(conn, src, dst):
    with conn.cursor() as cur:
        cur.execute("UPDATE ks.nodes SET merged_into_id = %s WHERE id = %s", (dst, src))


def test_returns_every_node(conn):
    _mk(conn, "Quang hợp", "Sinh học")
    _mk(conn, "Chiến tranh Lạnh", "Lịch sử")
    assert [n.title for n in list_nodes(conn)] == ["Chiến tranh Lạnh", "Quang hợp"]


def test_returns_no_edges(conn):
    from ks.models import NodeSummary
    _mk(conn, "Quang hợp", "Sinh học")
    node = list_nodes(conn)[0]
    assert isinstance(node, NodeSummary)
    assert not hasattr(node, "edges")


def test_filter_by_subject(conn):
    _mk(conn, "Quang hợp", "Sinh học")
    _mk(conn, "Chiến tranh Lạnh", "Lịch sử")
    assert [n.title for n in list_nodes(conn, subject="Sinh học")] == ["Quang hợp"]


def test_subject_is_free_text_matched_exactly(conn):
    _mk(conn, "Quang hợp", "Sinh học nâng cao")
    assert list_nodes(conn, subject="Sinh học") == ()
    assert len(list_nodes(conn, subject="Sinh học nâng cao")) == 1


def test_filter_by_source_module(conn):
    _mk(conn, "Quang hợp", "Sinh học")
    _mk(conn, "Từ vựng IELTS", "Tiếng Anh", source=SourceModule.LEXIFLASH)
    assert [n.title for n in list_nodes(conn, source_module="lexiflash")] == ["Từ vựng IELTS"]


def test_two_filters_combine(conn):
    _mk(conn, "Quang hợp", "Sinh học")
    _mk(conn, "Chiến tranh Lạnh", "Lịch sử", source=SourceModule.LEXIFLASH)
    assert list_nodes(conn, subject="Sinh học", source_module="lexiflash") == ()


def test_limit_is_respected(conn):
    _mk(conn, "Quang hợp", "Sinh học")
    _mk(conn, "Chiến tranh Lạnh", "Lịch sử")
    _mk(conn, "Phương trình bậc hai", "Toán")
    assert len(list_nodes(conn, limit=2)) == 2


def test_merged_node_returns_the_target(conn):
    a = _mk(conn, "Quang hợp", "Sinh học")
    b = _mk(conn, "Chiến tranh Lạnh", "Lịch sử")
    _merge(conn, a, b)
    assert [n.title for n in list_nodes(conn)] == ["Chiến tranh Lạnh"]


def test_several_nodes_with_one_target_collapse(conn):
    """Three nodes pointing at the same target give ONE row."""
    target = _mk(conn, "Chiến tranh Lạnh", "Lịch sử")
    for title in ("Quang hợp", "Phương trình bậc hai", "Thì hiện tại hoàn thành"):
        _merge(conn, _mk(conn, title, "Môn khác"), target)
    result = list_nodes(conn)
    assert len(result) == 1
    assert result[0].id == target


def test_resolves_exactly_ONE_STEP_no_chain_walk(conn):
    """A → B → C: listing A must give B, NOT C."""
    c = _mk(conn, "Chiến tranh Lạnh", "Lịch sử")
    b = _mk(conn, "Phương trình bậc hai", "Toán")
    a = _mk(conn, "Quang hợp", "Sinh học")
    _merge(conn, b, c)
    _merge(conn, a, b)
    titles = sorted(n.title for n in list_nodes(conn))
    # A→B gives "Phương trình bậc hai"; B→C and C give "Chiến tranh Lạnh"
    assert titles == ["Chiến tranh Lạnh", "Phương trình bậc hai"]


def test_filters_apply_to_the_resolved_node(conn):
    """Filtering subject='Vật lý' (Physics) must not return a Biology node just because the original
    node (merged away) used to be Physics."""
    target = _mk(conn, "Quang hợp", "Sinh học")
    src = _mk(conn, "Định luật Ohm", "Vật lý")
    _merge(conn, src, target)
    assert list_nodes(conn, subject="Vật lý") == ()
    assert [n.title for n in list_nodes(conn, subject="Sinh học")] == ["Quang hợp"]


def test_empty_db_returns_empty(conn):
    assert list_nodes(conn) == ()


# ---------------------------------------------------------------- get_node


def test_get_node_returns_the_node(conn):
    from ks.query import get_node
    node_id = _mk(conn, "Quang hợp", "Sinh học", "Cây dùng ánh sáng.")
    node = get_node(conn, node_id)
    assert (node.id, node.title, node.subject, node.summary) == (
        node_id, "Quang hợp", "Sinh học", "Cây dùng ánh sáng."
    )


def test_get_node_missing_returns_None(conn):
    import uuid

    from ks.query import get_node
    assert get_node(conn, uuid.uuid4()) is None


def test_get_node_merged_returns_the_target(conn):
    """Exactly like GET /nodes: resolve the merge, NO 404."""
    from ks.query import get_node
    a = _mk(conn, "Quang hợp", "Sinh học")
    b = _mk(conn, "Chiến tranh Lạnh", "Lịch sử")
    _merge(conn, a, b)
    node = get_node(conn, a)
    assert node.id == b
    assert node.title == "Chiến tranh Lạnh"


def test_get_node_resolves_exactly_ONE_STEP(conn):
    """A → B → C: asking for A gives B, NOT C."""
    from ks.query import get_node
    c = _mk(conn, "Chiến tranh Lạnh", "Lịch sử")
    b = _mk(conn, "Phương trình bậc hai", "Toán")
    a = _mk(conn, "Quang hợp", "Sinh học")
    _merge(conn, b, c)
    _merge(conn, a, b)
    assert get_node(conn, a).id == b


def test_get_node_id_of_an_unaccepted_extracted_concept_is_not_a_node(conn):
    """There is NO such thing as an 'unreviewed node': ks.nodes has no status column.
    A concept not yet accepted exists only in ks.extracted_concepts, with no node at all."""
    import json
    import uuid

    from ks.query import get_node
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO ks.transcripts (session_ref, content) VALUES (%s, '[]') RETURNING id",
            (f"s-{uuid.uuid4()}",),
        )
        tid = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO ks.extracted_concepts"
            " (transcript_id, title, subject, summary, source_module)"
            " VALUES (%s, 'Chưa duyệt', 'Vật lý', 'x', 'mnemosyne') RETURNING id, status",
            (tid,),
        )
        concept_id, status = cur.fetchone()
    assert status == "pending_review"
    assert get_node(conn, concept_id) is None
