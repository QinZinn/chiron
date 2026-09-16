"""ks/query.py — list_nodes: lọc, resolve merge một bước, dedup."""

from __future__ import annotations

from ks.ingest import ingest_concepts
from ks.models import ConceptDraft, SourceModule
from ks.query import list_nodes


def _mk(conn, title, subject="Vật lý", summary="x", source=SourceModule.MNEMOSYNE):
    return ingest_concepts(conn, [ConceptDraft(title, subject, summary, source)]).ingested[0].node_id


def _merge(conn, src, dst):
    with conn.cursor() as cur:
        cur.execute("UPDATE ks.nodes SET merged_into_id = %s WHERE id = %s", (dst, src))


def test_tra_ve_tat_ca_node(conn):
    _mk(conn, "Quang hợp", "Sinh học")
    _mk(conn, "Chiến tranh Lạnh", "Lịch sử")
    assert [n.title for n in list_nodes(conn)] == ["Chiến tranh Lạnh", "Quang hợp"]


def test_khong_tra_edges(conn):
    from ks.models import NodeSummary
    _mk(conn, "Quang hợp", "Sinh học")
    node = list_nodes(conn)[0]
    assert isinstance(node, NodeSummary)
    assert not hasattr(node, "edges")


def test_loc_theo_subject(conn):
    _mk(conn, "Quang hợp", "Sinh học")
    _mk(conn, "Chiến tranh Lạnh", "Lịch sử")
    assert [n.title for n in list_nodes(conn, subject="Sinh học")] == ["Quang hợp"]


def test_subject_la_text_tu_do_khop_nguyen_van(conn):
    _mk(conn, "Quang hợp", "Sinh học nâng cao")
    assert list_nodes(conn, subject="Sinh học") == ()
    assert len(list_nodes(conn, subject="Sinh học nâng cao")) == 1


def test_loc_theo_source_module(conn):
    _mk(conn, "Quang hợp", "Sinh học")
    _mk(conn, "Từ vựng IELTS", "Tiếng Anh", source=SourceModule.LEXIFLASH)
    assert [n.title for n in list_nodes(conn, source_module="lexiflash")] == ["Từ vựng IELTS"]


def test_hai_bo_loc_cong_don(conn):
    _mk(conn, "Quang hợp", "Sinh học")
    _mk(conn, "Chiến tranh Lạnh", "Lịch sử", source=SourceModule.LEXIFLASH)
    assert list_nodes(conn, subject="Sinh học", source_module="lexiflash") == ()


def test_limit_duoc_ton_trong(conn):
    _mk(conn, "Quang hợp", "Sinh học")
    _mk(conn, "Chiến tranh Lạnh", "Lịch sử")
    _mk(conn, "Phương trình bậc hai", "Toán")
    assert len(list_nodes(conn, limit=2)) == 2


def test_merged_node_tra_ve_node_dich(conn):
    a = _mk(conn, "Quang hợp", "Sinh học")
    b = _mk(conn, "Chiến tranh Lạnh", "Lịch sử")
    _merge(conn, a, b)
    assert [n.title for n in list_nodes(conn)] == ["Chiến tranh Lạnh"]


def test_dedup_nhieu_node_cung_dich(conn):
    """Ba node trỏ về cùng một đích chỉ ra MỘT dòng."""
    target = _mk(conn, "Chiến tranh Lạnh", "Lịch sử")
    for title in ("Quang hợp", "Phương trình bậc hai", "Thì hiện tại hoàn thành"):
        _merge(conn, _mk(conn, title, "Môn khác"), target)
    result = list_nodes(conn)
    assert len(result) == 1
    assert result[0].id == target


def test_resolve_dung_MOT_BUOC_khong_walk_chain(conn):
    """A → B → C: liệt kê A phải ra B, KHÔNG phải C."""
    c = _mk(conn, "Chiến tranh Lạnh", "Lịch sử")
    b = _mk(conn, "Phương trình bậc hai", "Toán")
    a = _mk(conn, "Quang hợp", "Sinh học")
    _merge(conn, b, c)
    _merge(conn, a, b)
    titles = sorted(n.title for n in list_nodes(conn))
    # A→B cho ra "Phương trình bậc hai"; B→C và C cho ra "Chiến tranh Lạnh"
    assert titles == ["Chiến tranh Lạnh", "Phương trình bậc hai"]


def test_loc_ap_len_node_da_resolve(conn):
    """Lọc subject='Vật lý' không được trả về node môn Sinh học chỉ vì node gốc
    (đã merge đi) từng thuộc môn Vật lý."""
    target = _mk(conn, "Quang hợp", "Sinh học")
    src = _mk(conn, "Định luật Ohm", "Vật lý")
    _merge(conn, src, target)
    assert list_nodes(conn, subject="Vật lý") == ()
    assert [n.title for n in list_nodes(conn, subject="Sinh học")] == ["Quang hợp"]


def test_db_rong_tra_ve_rong(conn):
    assert list_nodes(conn) == ()


# ---------------------------------------------------------------- get_node


def test_get_node_tra_ve_node(conn):
    from ks.query import get_node
    node_id = _mk(conn, "Quang hợp", "Sinh học", "Cây dùng ánh sáng.")
    node = get_node(conn, node_id)
    assert (node.id, node.title, node.subject, node.summary) == (
        node_id, "Quang hợp", "Sinh học", "Cây dùng ánh sáng."
    )


def test_get_node_khong_ton_tai_tra_None(conn):
    import uuid

    from ks.query import get_node
    assert get_node(conn, uuid.uuid4()) is None


def test_get_node_da_merge_tra_node_dich(conn):
    """Giống hệt GET /nodes: resolve merge, KHÔNG 404."""
    from ks.query import get_node
    a = _mk(conn, "Quang hợp", "Sinh học")
    b = _mk(conn, "Chiến tranh Lạnh", "Lịch sử")
    _merge(conn, a, b)
    node = get_node(conn, a)
    assert node.id == b
    assert node.title == "Chiến tranh Lạnh"


def test_get_node_resolve_dung_MOT_BUOC(conn):
    """A → B → C: hỏi A ra B, KHÔNG phải C."""
    from ks.query import get_node
    c = _mk(conn, "Chiến tranh Lạnh", "Lịch sử")
    b = _mk(conn, "Phương trình bậc hai", "Toán")
    a = _mk(conn, "Quang hợp", "Sinh học")
    _merge(conn, b, c)
    _merge(conn, a, b)
    assert get_node(conn, a).id == b


def test_get_node_id_cua_extracted_concept_chua_accept_khong_phai_node(conn):
    """KHÔNG có khái niệm 'node chưa duyệt': ks.nodes không mang cột status.
    Khái niệm chưa accept chỉ tồn tại ở ks.extracted_concepts, chưa có node nào."""
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
