"""card_sync: quét node, gọi Mnemosyne, xử lý lỗi THEO `reason` — không retry mù."""

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

# Mnemosyne nhận study_set_id là UUID, không phải tên set.
SET_ID = UUID("ae2c0db6-4a7e-4956-9bb9-ffe25eeaf151")

# NGUYÊN VĂN body 502 Mnemosyne trả trên dây thật (họ ép được truncation qua API
# thật bằng cách vá tạm max_tokens=200). Lưu ý KHÔNG có field "message" — bản
# test cũ ở đây bịa ra field đó. Anchor test vào wire thật, đừng vào tưởng tượng.
TRUNCATED_BODY = {
    "error": (
        "DeepSeek stopped mid-answer at its token limit (length); nothing was "
        "parsed. Retrying, or requesting fewer items, may succeed."
    ),
    "reason": "truncated",
}


class FakeCardClient:
    """Trả sẵn (status, body) theo kịch bản. Ghi lại mọi lệnh gọi."""

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


# ---------------------------------------------------------------- quét


def test_node_chua_tung_gui_thi_duoc_chon(node):
    conn, nid = node
    assert pending_nodes(conn) == (nid,)


def test_node_da_sent_khong_duoc_chon_lai(node):
    conn, nid = node
    sync_cards(conn, study_set_id=SET_ID, client=FakeCardClient((201, {"card_id": "c1"})))
    assert pending_nodes(conn) == ()


def test_node_da_failed_KHONG_duoc_chon_lai(node):
    """failed là quyết định cuối — retry sẽ đốt LLM bên Mnemosyne vô ích."""
    conn, nid = node
    sync_cards(conn, study_set_id=SET_ID, client=FakeCardClient((502, TRUNCATED_BODY)))
    assert _log(conn, nid)[0] == "failed"
    assert pending_nodes(conn) == ()


def test_node_da_skipped_KHONG_duoc_chon_lai(node):
    conn, nid = node
    sync_cards(conn, study_set_id=SET_ID, client=FakeCardClient((404, {"error": "set not found"})))
    assert pending_nodes(conn) == ()


def test_node_pending_ĐUOC_chon_lai(node):
    conn, nid = node
    sync_cards(conn, study_set_id=SET_ID, client=FakeCardClient((503, {"error": "KS chưa cấu hình"})))
    assert pending_nodes(conn) == (nid,)


def test_node_da_merge_bi_bo_qua(conn):
    """Card thuộc về node đích, không phải node đã chết."""
    a = _mk(conn, "Quang hợp", "Sinh học")
    b = _mk(conn, "Chiến tranh Lạnh", "Lịch sử")
    with conn.cursor() as cur:
        cur.execute("UPDATE ks.nodes SET merged_into_id = %s WHERE id = %s", (b, a))
    assert pending_nodes(conn) == (b,)


def test_ton_trong_limit(conn):
    for t in ("Quang hợp", "Chiến tranh Lạnh", "Phương trình bậc hai"):
        _mk(conn, t, t)
    assert len(pending_nodes(conn, limit=2)) == 2


# ---------------------------------------------------------------- bảng quyết định


@pytest.mark.parametrize(
    "http_status, body, mong_doi",
    [
        (200, {"card_id": "c1"}, "sent"),
        (201, {"card_id": "c1"}, "sent"),
        # 409 KHÔNG phải lỗi: đã có card, Mnemosyne xác nhận không tốn LLM.
        (409, {"error": "card đã tồn tại"}, "sent"),
        # 404: set hoặc node sai — dữ liệu hỏng, retry không giúp gì.
        (404, {"error": "study set not found"}, "skipped"),
        # 503: Mnemosyne chưa nối được KS — lỗi phía họ, thử lại sau.
        (503, {"error": "knowledge store not configured"}, "pending"),
        # 502 + truncated: KHÔNG retry cùng input.
        (502, TRUNCATED_BODY, "failed"),
        # 502 + knowledge_store_error: an toàn để retry.
        (502, {"reason": "knowledge_store_error", "message": "GET /nodes lỗi"}, "pending"),
        # 502 + provider_error: retry có giới hạn, lần đầu vẫn pending.
        (502, {"reason": "provider_error", "message": "network"}, "pending"),
    ],
)
def test_bang_quyet_dinh_theo_reason(node, http_status, body, mong_doi):
    conn, nid = node
    result = sync_cards(conn, study_set_id=SET_ID, client=FakeCardClient((http_status, body)))
    assert result.outcomes[0].status == mong_doi
    assert _log(conn, nid)[0] == mong_doi


def test_provider_error_can_luot_thi_thanh_failed(node):
    """Retry CÓ GIỚI HẠN — không phải retry lũy tiến mù."""
    conn, nid = node
    body = (502, {"reason": "provider_error", "message": "network"})
    for _ in range(settings.MAX_CARD_SYNC_ATTEMPTS):
        sync_cards(conn, study_set_id=SET_ID, client=FakeCardClient(body))
    status, attempts, _, _ = _log(conn, nid)
    assert (status, attempts) == ("failed", settings.MAX_CARD_SYNC_ATTEMPTS)
    assert pending_nodes(conn) == ()


def test_truncated_failed_NGAY_o_lan_dau_khong_dung_het_luot(node):
    """Khác provider_error: truncated không được hưởng lượt retry nào."""
    conn, nid = node
    sync_cards(conn, study_set_id=SET_ID, client=FakeCardClient((502, TRUNCATED_BODY)))
    status, attempts, _, _ = _log(conn, nid)
    assert status == "failed"
    assert attempts == 1, "truncated phải chết ở lần thử ĐẦU TIÊN"


def test_knowledge_store_error_retry_khong_bi_gioi_han_boi_provider_budget(node):
    """Lỗi hạ tầng tạm thời — timer sẽ thử lại, không cạn lượt như provider_error."""
    conn, nid = node
    body = (502, {"reason": "knowledge_store_error", "message": "KS timeout"})
    for _ in range(settings.MAX_CARD_SYNC_ATTEMPTS + 2):
        sync_cards(conn, study_set_id=SET_ID, client=FakeCardClient(body))
    assert _log(conn, nid)[0] == "pending"
    assert pending_nodes(conn) == (nid,)


def test_reason_la_thi_xu_ly_nhu_provider_error(node):
    conn, nid = node
    sync_cards(conn, study_set_id=SET_ID, client=FakeCardClient((502, {"reason": "chưa từng thấy"})))
    assert _log(conn, nid)[0] == "pending"


def test_502_khong_co_reason_van_khong_crash(node):
    conn, nid = node
    result = sync_cards(conn, study_set_id=SET_ID, client=FakeCardClient((502, "lỗi dạng text thuần")))
    assert result.outcomes[0].status == "pending"


# ---------------------------------------------------------------- log


def test_last_error_luu_NGUYEN_VAN_reason_va_message(node):
    """YÊU CẦU BẮT BUỘC: nhánh truncated chưa từng verify qua API thật, nên dòng
    log này là bằng chứng duy nhất để kiểm hành vi lần đầu gặp ngoài đời."""
    conn, nid = node
    sync_cards(conn, study_set_id=SET_ID, client=FakeCardClient((502, TRUNCATED_BODY)))
    _, _, last_error, _ = _log(conn, nid)
    assert "truncated" in last_error
    # Nguyên văn message của Mnemosyne phải còn nguyên trong log, không rút gọn.
    assert "stopped mid-answer at its token limit" in last_error
    assert "502" in last_error


def test_ghi_attempted_at(node):
    conn, nid = node
    sync_cards(conn, study_set_id=SET_ID, client=FakeCardClient((201, {})))
    assert _log(conn, nid)[3] is True


def test_sent_thi_last_error_la_None(node):
    conn, nid = node
    sync_cards(conn, study_set_id=SET_ID, client=FakeCardClient((201, {})))
    assert _log(conn, nid)[2] is None


# ---------------------------------------------------------------- study_set_id


def test_dung_MOT_study_set_co_dinh_KHONG_map_theo_subject(conn):
    """subject là TEXT tự do, chưa có bằng chứng phân bố để thiết kế mapping."""
    _mk(conn, "Quang hợp", "Sinh học")
    _mk(conn, "Chiến tranh Lạnh", "Lịch sử")
    client = FakeCardClient((201, {}), (201, {}))
    sync_cards(conn, client, study_set_id=SET_ID)
    assert {s for _, s in client.calls} == {SET_ID}, "mọi node vào cùng một set"


def test_study_set_id_thieu_thi_bao_loi_kem_cach_sua():
    with pytest.raises(CardClientError, match="POST /study_sets"):
        study_set_id_from_env({})


def test_study_set_id_la_ten_set_thi_bao_loi_ro(conn):
    """Giả định ban đầu của KS là gửi tên "KS review" — Mnemosyne trả 400 vì
    serde không parse được thành Uuid. Chặn ngay ở KS với thông báo rõ."""
    with pytest.raises(CardClientError, match="không phải UUID"):
        study_set_id_from_env({"KS_CARD_SYNC_STUDY_SET_ID": "KS review"})


def test_study_set_id_hop_le_parse_duoc():
    got = study_set_id_from_env(
        {"KS_CARD_SYNC_STUDY_SET_ID": "ae2c0db6-4a7e-4956-9bb9-ffe25eeaf151"})
    assert got == SET_ID


def test_body_gui_di_dung_hai_field_uuid(conn):
    """Cả hai field bắt buộc, đều là UUID, không có path/query param."""
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


# ---------------------------------------------------------------- lỗi hạ tầng


def test_khong_goi_noi_mnemosyne_thi_DUNG_ca_lo(conn):
    """Gọi tiếp 99 node nữa để nhận cùng lỗi mạng là vô nghĩa, và sẽ đốt hết
    lượt retry của chúng vì lý do không liên quan gì tới node."""
    for t in ("Quang hợp", "Chiến tranh Lạnh", "Phương trình bậc hai"):
        _mk(conn, t, t)
    client = FakeCardClient(error=CardClientError("connection refused"))
    result = sync_cards(conn, client, study_set_id=SET_ID)
    assert len(client.calls) == 1, "phải dừng sau node đầu tiên"
    assert result.outcomes[0].status == "pending"


def test_loi_ha_tang_KHONG_dot_luot_retry(node):
    conn, nid = node
    sync_cards(conn, study_set_id=SET_ID, client=FakeCardClient(error=CardClientError("connection refused")))
    status, attempts, last_error, _ = _log(conn, nid)
    assert (status, attempts) == ("pending", 0)
    assert "connection refused" in last_error


def test_mot_node_loi_khong_chan_node_sau(conn):
    a = _mk(conn, "Quang hợp", "Sinh học")
    b = _mk(conn, "Chiến tranh Lạnh", "Lịch sử")
    client = FakeCardClient((404, {"error": "x"}), (201, {"card_id": "c"}))
    result = sync_cards(conn, client, study_set_id=SET_ID)
    assert [o.status for o in result.outcomes] == ["skipped", "sent"]


def test_khong_tu_commit(node):
    conn, nid = node
    sync_cards(conn, study_set_id=SET_ID, client=FakeCardClient((201, {})))
    conn.rollback()
    assert _log(conn, nid) is None


# ---------------------------------------------------------------- cấu hình


def test_thieu_url_thi_bao_loi_ro():
    with pytest.raises(CardClientError, match="KS_MNEMOSYNE_URL"):
        client_from_env({})


def test_token_KHONG_bat_buoc(monkeypatch):
    """Mnemosyne chưa có auth layer — /cards/from_node không có extractor auth.
    Bắt buộc token ở phía KS sẽ tự chặn mình mà không được gì."""
    client = client_from_env({"KS_MNEMOSYNE_URL": "http://127.0.0.1:8081"})
    assert client is not None


# ---------------------------------------------------------------- timeout


def test_timeout_mac_dinh_du_rong_cho_model_reasoning():
    """ĐO ĐƯỢC THẬT: node summary ~1900 ký tự mất 11s; node ~2900 ký tự vượt 30s.

    Timeout ngắn quá TỆ HƠN là chậm — KS bỏ cuộc trước khi Mnemosyne trả lời thì
    mất luôn phân loại thật (truncated / provider_error / knowledge_store_error),
    tất cả bị ghi đè thành CardClientError. Đã che mất đúng một ca cần quan sát.
    """
    assert settings.DEFAULT_MNEMOSYNE_TIMEOUT >= 120


def test_timeout_doc_duoc_tu_env():
    client = client_from_env({
        "KS_MNEMOSYNE_URL": "http://127.0.0.1:8081",
        "KS_MNEMOSYNE_TIMEOUT": "240",
    })
    assert client._timeout == 240


def test_timeout_khong_phai_so_thi_bao_loi_ro():
    with pytest.raises(CardClientError, match="KS_MNEMOSYNE_TIMEOUT"):
        client_from_env({
            "KS_MNEMOSYNE_URL": "http://127.0.0.1:8081",
            "KS_MNEMOSYNE_TIMEOUT": "lâu",
        })


# ---------------------------------------------------------------- kết quả mồ côi


def test_timeout_roi_409_tu_hoa_giai_ket_qua_mo_coi(node):
    """BẤT BIẾN QUAN TRỌNG: timeout phía KS KHÔNG huỷ việc phía Mnemosyne.

    Mnemosyne xác nhận bằng thí nghiệm: giết client sau 2 giây, handler của họ
    vẫn chạy tới cùng và TẠO CARD BÌNH THƯỜNG 3 giây sau đó. Nghĩa là timeout
    sinh ra **kết quả mồ côi** — họ có card, KS tưởng hỏng, hai bên tin hai
    chuyện khác nhau về cùng một node.

    Thứ hoà giải nó là chuỗi hai bước dưới đây, và cả hai bước đều bắt buộc:
      1. timeout ghi `pending` (KHÔNG phải `failed`) → node còn được chọn lại
      2. lần sau nhận 409 → ghi `sent` (409 KHÔNG phải lỗi)

    Đổi bất kỳ bước nào cũng làm ca mồ côi mắc kẹt vĩnh viễn.
    """
    conn, nid = node

    # Lần 1: KS timeout. Mnemosyne (không thấy được từ đây) vẫn tạo card xong.
    sync_cards(conn, FakeCardClient(error=CardClientError("timeout khi gọi Mnemosyne")),
               study_set_id=SET_ID)
    status, attempts, last_error, _ = _log(conn, nid)
    assert status == "pending", "timeout phải để node lại cho lần sau"
    assert attempts == 0, "lỗi hạ tầng không được đốt lượt retry"
    assert pending_nodes(conn) == (nid,)

    # Lần 2: card đã tồn tại bên kia → 409 kèm existing_card_id.
    result = sync_cards(
        conn,
        FakeCardClient((409, {"error": "card đã tồn tại",
                              "existing_card_id": "c0ffee00-0000-4000-8000-000000000000"})),
        study_set_id=SET_ID,
    )
    assert result.outcomes[0].status == "sent"
    assert _log(conn, nid)[0] == "sent"
    assert pending_nodes(conn) == (), "đã hoà giải, không lặp lại nữa"
