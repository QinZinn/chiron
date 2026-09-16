"""LLM client: map lỗi HTTP, parse phản hồi, chọn provider từ env.

Không gọi mạng thật — mọi test bơm fake `post`.
"""

from __future__ import annotations

import pytest

from ks import llm
from ks.llm import (
    AnthropicProvider,
    LLMAuthError,
    LLMBadRequestError,
    LLMParseError,
    LLMQuotaError,
    LLMRefusalError,
    LLMTransientError,
    Message,
    OpenAICompatibleProvider,
    ProviderConfig,
)

MSGS = [Message("user", "xin chào")]


def fake_post(status, data, sink=None):
    def _post(url, headers, body):
        if sink is not None:
            sink.append((url, headers, body))
        return (status, data)

    return _post


# ---------------------------------------------------------------- taxonomy


@pytest.mark.parametrize(
    "status, expected",
    [
        (401, LLMAuthError),
        (403, LLMAuthError),
        (429, LLMQuotaError),
        (400, LLMBadRequestError),
        (404, LLMBadRequestError),
        (422, LLMBadRequestError),
        (500, LLMTransientError),
        (503, LLMTransientError),
    ],
)
def test_map_status_ve_dung_loai_loi(status, expected):
    provider = AnthropicProvider("m", "k", post=fake_post(status, {"error": "x"}))
    with pytest.raises(expected):
        provider.complete(MSGS)


def test_moi_loi_deu_la_LLMError():
    """Caller bắt được tất cả bằng một except duy nhất."""
    for cls in (LLMAuthError, LLMQuotaError, LLMTransientError,
                LLMBadRequestError, LLMParseError, LLMRefusalError):
        assert issubclass(cls, llm.LLMError)


def test_khong_doan_dinh_dang_loi_ben_thu_ba():
    """403 kèm body Cloudflare vẫn chỉ là LLMAuthError — không soi 'error code: 1010'."""
    body = "error code: 1010"
    provider = OpenAICompatibleProvider("m", "k", base_url="https://x/v1", post=fake_post(403, body))
    with pytest.raises(LLMAuthError):
        provider.complete(MSGS)


# ---------------------------------------------------------------- anthropic


def test_anthropic_doc_content_0_text():
    data = {"content": [{"text": "kết quả"}], "stop_reason": "end_turn"}
    assert AnthropicProvider("m", "k", post=fake_post(200, data)).complete(MSGS) == "kết quả"


def test_anthropic_stop_reason_refusal():
    data = {"content": [], "stop_reason": "refusal"}
    with pytest.raises(LLMRefusalError):
        AnthropicProvider("m", "k", post=fake_post(200, data)).complete(MSGS)


def test_anthropic_200_nhung_sai_cau_truc_la_parse_error():
    with pytest.raises(LLMParseError):
        AnthropicProvider("m", "k", post=fake_post(200, {"gì đó": 1})).complete(MSGS)


def test_anthropic_gui_dung_header_va_url():
    sink = []
    data = {"content": [{"text": "x"}]}
    AnthropicProvider("m", "secret", post=fake_post(200, data, sink)).complete(MSGS)
    url, headers, _ = sink[0]
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == "secret"
    assert headers["anthropic-version"] == "2023-06-01"


# ---------------------------------------------------------------- openai-compatible


def test_openai_compatible_doc_choices_0_message_content():
    data = {"choices": [{"message": {"content": "kết quả"}}]}
    provider = OpenAICompatibleProvider("m", "k", base_url="https://api.deepseek.com/v1",
                                        post=fake_post(200, data))
    assert provider.complete(MSGS) == "kết quả"


def test_openai_compatible_url_va_bearer():
    sink = []
    data = {"choices": [{"message": {"content": "x"}}]}
    provider = OpenAICompatibleProvider("m", "secret", base_url="https://api.deepseek.com/v1/",
                                        post=fake_post(200, data, sink))
    provider.complete(MSGS)
    url, headers, _ = sink[0]
    assert url == "https://api.deepseek.com/v1/chat/completions"
    assert headers["Authorization"] == "Bearer secret"


def test_openai_compatible_refusal_field():
    data = {"choices": [{"message": {"content": None, "refusal": "không thể giúp"}}]}
    provider = OpenAICompatibleProvider("m", "k", base_url="https://x/v1", post=fake_post(200, data))
    with pytest.raises(LLMRefusalError):
        provider.complete(MSGS)


def test_openai_compatible_content_filter():
    data = {"choices": [{"message": {"content": ""}, "finish_reason": "content_filter"}]}
    provider = OpenAICompatibleProvider("m", "k", base_url="https://x/v1", post=fake_post(200, data))
    with pytest.raises(LLMRefusalError):
        provider.complete(MSGS)


def test_openai_compatible_thieu_choices_la_parse_error():
    provider = OpenAICompatibleProvider("m", "k", base_url="https://x/v1",
                                        post=fake_post(200, {"choices": []}))
    with pytest.raises(LLMParseError):
        provider.complete(MSGS)


def test_body_mang_dung_model_va_messages():
    import json
    sink = []
    data = {"choices": [{"message": {"content": "x"}}]}
    provider = OpenAICompatibleProvider("deepseek-v4-flash", "k", base_url="https://x/v1",
                                        post=fake_post(200, data, sink))
    provider.complete([Message("system", "s"), Message("user", "u")], max_tokens=42, temperature=0.5)
    payload = json.loads(sink[0][2])
    assert payload["model"] == "deepseek-v4-flash"
    assert payload["max_tokens"] == 42
    assert payload["temperature"] == 0.5
    assert payload["messages"] == [{"role": "system", "content": "s"},
                                   {"role": "user", "content": "u"}]


# ---------------------------------------------------------------- config từ env


def test_config_from_env_deepseek():
    env = {
        "KS_LLM_PROVIDER": "deepseek",
        "KS_LLM_MODEL": "deepseek-v4-flash",
        "KS_LLM_BASE_URL": "https://api.deepseek.com/v1",
        "KS_LLM_API_KEY_ENV": "DEEPSEEK_API_KEY",
    }
    cfg = llm.config_from_env(env)
    assert cfg == ProviderConfig("deepseek", "deepseek-v4-flash",
                                 "DEEPSEEK_API_KEY", "https://api.deepseek.com/v1")


def test_api_key_env_la_TEN_bien_khong_phai_gia_tri():
    """Chốt quy ước: config chứa tên biến, giá trị chỉ đọc lúc dựng provider."""
    cfg = ProviderConfig("deepseek", "m", "DEEPSEEK_API_KEY", "https://x/v1")
    assert cfg.api_key_env == "DEEPSEEK_API_KEY"
    provider = llm.build_provider(cfg, env={"DEEPSEEK_API_KEY": "giá-trị-thật"})
    assert provider.name == "deepseek"


def test_thieu_bien_bat_buoc_la_bad_request():
    with pytest.raises(LLMBadRequestError):
        llm.config_from_env({"KS_LLM_PROVIDER": "deepseek"})


def test_openai_compatible_thieu_base_url_la_bad_request():
    with pytest.raises(LLMBadRequestError):
        llm.config_from_env({
            "KS_LLM_PROVIDER": "deepseek",
            "KS_LLM_MODEL": "m",
            "KS_LLM_API_KEY_ENV": "DEEPSEEK_API_KEY",
        })


def test_anthropic_khong_can_base_url():
    cfg = llm.config_from_env({
        "KS_LLM_PROVIDER": "anthropic",
        "KS_LLM_MODEL": "claude-opus-5",
        "KS_LLM_API_KEY_ENV": "ANTHROPIC_API_KEY",
    })
    assert cfg.base_url == ""


def test_key_rong_la_auth_error_chu_khong_im_lang():
    cfg = ProviderConfig("deepseek", "m", "KHONG_TON_TAI", "https://x/v1")
    with pytest.raises(LLMAuthError):
        llm.build_provider(cfg, env={})


def test_provider_from_env_dung_ten_provider_lam_nhan():
    provider = llm.provider_from_env({
        "KS_LLM_PROVIDER": "deepseek",
        "KS_LLM_MODEL": "deepseek-v4-flash",
        "KS_LLM_BASE_URL": "https://api.deepseek.com/v1",
        "KS_LLM_API_KEY_ENV": "DEEPSEEK_API_KEY",
        "DEEPSEEK_API_KEY": "k",
    })
    assert provider.name == "deepseek"
    assert provider.model == "deepseek-v4-flash"


# ---------------------------------------------------------------- cắt ngang (reasoning model)


def test_openai_compatible_finish_reason_length_la_truncated():
    """ĐO ĐƯỢC THẬT với deepseek-v4-flash: reasoning token tính vào max_tokens,
    nên phản hồi có thể cụt giữa chừng. Phải nói thẳng nguyên nhân thay vì để
    JSON cụt lọt xuống parser rồi hiện ra dưới dạng LLMParseError khó hiểu."""
    from ks.llm import LLMTruncatedError
    data = {
        "choices": [{"message": {"content": '[\n  {\n    "relation_type": "pr'},
                    "finish_reason": "length"}],
        "usage": {"completion_tokens": 1000,
                  "completion_tokens_details": {"reasoning_tokens": 985}},
    }
    provider = OpenAICompatibleProvider("m", "k", base_url="https://x/v1", post=fake_post(200, data))
    with pytest.raises(LLMTruncatedError) as exc:
        provider.complete(MSGS)
    assert "reasoning_tokens=985" in str(exc.value)


def test_anthropic_stop_reason_max_tokens_la_truncated():
    from ks.llm import LLMTruncatedError
    data = {"content": [{"text": "["}], "stop_reason": "max_tokens"}
    with pytest.raises(LLMTruncatedError):
        AnthropicProvider("m", "k", post=fake_post(200, data)).complete(MSGS)


def test_truncated_la_con_cua_transient_nen_retry_duoc():
    """Lượng reasoning token thay đổi mỗi lần chạy — cùng max_tokens lúc đủ lúc không."""
    from ks.llm import LLMTruncatedError
    assert issubclass(LLMTruncatedError, LLMTransientError)


def test_truncated_CO_content_van_phai_la_truncated_KHONG_phai_parse_error():
    """Mnemosyne đo 40 call thật: ở max_tokens=350, 5/10 reply bị cắt vẫn TRẢ
    VỀ content — JSON viết dở, tới 310 ký tự. Không phải ca hiếm.

    Không check finish_reason thì đám đó đi thẳng vào parser và báo lỗi ĐỊNH
    DẠNG, khiến người đọc log đi soi prompt trong khi lỗi thật là ngân sách
    token. Đúng bug production ban đầu của KS.
    """
    from ks.llm import LLMTruncatedError
    data = {
        "choices": [{
            "message": {"content": '[\n  {\n    "candidate": 0,\n    "relation_type": "pr'},
            "finish_reason": "length",
        }],
        "usage": {"completion_tokens": 350,
                  "completion_tokens_details": {"reasoning_tokens": 232}},
    }
    provider = OpenAICompatibleProvider("m", "k", base_url="https://x/v1", post=fake_post(200, data))
    with pytest.raises(LLMTruncatedError):
        provider.complete(MSGS)


def test_JSON_HOP_LE_nhung_finish_reason_length_van_bi_tu_choi():
    """Ca âm thầm nguy hiểm nhất: model viết xong `]` rồi mới cạn token. Chuỗi
    parse được, nhưng nội dung THIẾU so với đáng lẽ phải có.

    Chấp nhận nó là im lặng mất dữ liệu — một phần câu trả lời bị coi như toàn
    bộ. Ngân sách token phải thắng cú pháp: cắt là bỏ, không cứu vãn.
    """
    from ks.llm import LLMTruncatedError
    data = {
        "choices": [{
            "message": {"content": '[{"candidate": 0, "relation_type": "related", "reason": "x"}]'},
            "finish_reason": "length",
        }],
    }
    provider = OpenAICompatibleProvider("m", "k", base_url="https://x/v1", post=fake_post(200, data))
    with pytest.raises(LLMTruncatedError):
        provider.complete(MSGS)


def test_finish_reason_stop_thi_content_hop_le_van_di_qua_binh_thuong():
    """Đối chứng: check truncation không được chặn nhầm phản hồi lành lặn."""
    data = {"choices": [{"message": {"content": "[]"}, "finish_reason": "stop"}]}
    provider = OpenAICompatibleProvider("m", "k", base_url="https://x/v1", post=fake_post(200, data))
    assert provider.complete(MSGS) == "[]"
