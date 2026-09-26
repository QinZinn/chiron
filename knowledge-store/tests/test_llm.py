"""LLM client: mapping HTTP errors, parsing responses, choosing the provider from env.

No real network calls — every test injects a fake `post`.
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

MSGS = [Message("user", "hello")]


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
def test_status_maps_to_the_right_error_class(status, expected):
    provider = AnthropicProvider("m", "k", post=fake_post(status, {"error": "x"}))
    with pytest.raises(expected):
        provider.complete(MSGS)


def test_every_error_is_an_LLMError():
    """Callers can catch everything with a single except."""
    for cls in (LLMAuthError, LLMQuotaError, LLMTransientError,
                LLMBadRequestError, LLMParseError, LLMRefusalError):
        assert issubclass(cls, llm.LLMError)


def test_does_not_guess_third_party_error_formats():
    """A 403 with a Cloudflare body is still just LLMAuthError — no sniffing for 'error code: 1010'."""
    body = "error code: 1010"
    provider = OpenAICompatibleProvider("m", "k", base_url="https://x/v1", post=fake_post(403, body))
    with pytest.raises(LLMAuthError):
        provider.complete(MSGS)


# ---------------------------------------------------------------- anthropic


def test_anthropic_reads_content_0_text():
    data = {"content": [{"text": "result"}], "stop_reason": "end_turn"}
    assert AnthropicProvider("m", "k", post=fake_post(200, data)).complete(MSGS) == "result"


def test_anthropic_stop_reason_refusal():
    data = {"content": [], "stop_reason": "refusal"}
    with pytest.raises(LLMRefusalError):
        AnthropicProvider("m", "k", post=fake_post(200, data)).complete(MSGS)


def test_anthropic_200_with_wrong_structure_is_a_parse_error():
    with pytest.raises(LLMParseError):
        AnthropicProvider("m", "k", post=fake_post(200, {"something": 1})).complete(MSGS)


def test_anthropic_sends_the_right_headers_and_url():
    sink = []
    data = {"content": [{"text": "x"}]}
    AnthropicProvider("m", "secret", post=fake_post(200, data, sink)).complete(MSGS)
    url, headers, _ = sink[0]
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == "secret"
    assert headers["anthropic-version"] == "2023-06-01"


# ---------------------------------------------------------------- openai-compatible


def test_openai_compatible_reads_choices_0_message_content():
    data = {"choices": [{"message": {"content": "result"}}]}
    provider = OpenAICompatibleProvider("m", "k", base_url="https://api.deepseek.com/v1",
                                        post=fake_post(200, data))
    assert provider.complete(MSGS) == "result"


def test_openai_compatible_url_and_bearer():
    sink = []
    data = {"choices": [{"message": {"content": "x"}}]}
    provider = OpenAICompatibleProvider("m", "secret", base_url="https://api.deepseek.com/v1/",
                                        post=fake_post(200, data, sink))
    provider.complete(MSGS)
    url, headers, _ = sink[0]
    assert url == "https://api.deepseek.com/v1/chat/completions"
    assert headers["Authorization"] == "Bearer secret"


def test_openai_compatible_refusal_field():
    data = {"choices": [{"message": {"content": None, "refusal": "cannot help"}}]}
    provider = OpenAICompatibleProvider("m", "k", base_url="https://x/v1", post=fake_post(200, data))
    with pytest.raises(LLMRefusalError):
        provider.complete(MSGS)


def test_openai_compatible_content_filter():
    data = {"choices": [{"message": {"content": ""}, "finish_reason": "content_filter"}]}
    provider = OpenAICompatibleProvider("m", "k", base_url="https://x/v1", post=fake_post(200, data))
    with pytest.raises(LLMRefusalError):
        provider.complete(MSGS)


def test_openai_compatible_missing_choices_is_a_parse_error():
    provider = OpenAICompatibleProvider("m", "k", base_url="https://x/v1",
                                        post=fake_post(200, {"choices": []}))
    with pytest.raises(LLMParseError):
        provider.complete(MSGS)


def test_body_carries_the_model_and_messages():
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


# ---------------------------------------------------------------- config from env


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


def test_api_key_env_is_the_variable_NAME_not_its_value():
    """Pins the convention: config holds the variable name; the value is only read when the provider is built."""
    cfg = ProviderConfig("deepseek", "m", "DEEPSEEK_API_KEY", "https://x/v1")
    assert cfg.api_key_env == "DEEPSEEK_API_KEY"
    provider = llm.build_provider(cfg, env={"DEEPSEEK_API_KEY": "real-value"})
    assert provider.name == "deepseek"


def test_missing_required_variable_is_a_bad_request():
    with pytest.raises(LLMBadRequestError):
        llm.config_from_env({"KS_LLM_PROVIDER": "deepseek"})


def test_openai_compatible_missing_base_url_is_a_bad_request():
    with pytest.raises(LLMBadRequestError):
        llm.config_from_env({
            "KS_LLM_PROVIDER": "deepseek",
            "KS_LLM_MODEL": "m",
            "KS_LLM_API_KEY_ENV": "DEEPSEEK_API_KEY",
        })


def test_anthropic_needs_no_base_url():
    cfg = llm.config_from_env({
        "KS_LLM_PROVIDER": "anthropic",
        "KS_LLM_MODEL": "claude-opus-5",
        "KS_LLM_API_KEY_ENV": "ANTHROPIC_API_KEY",
    })
    assert cfg.base_url == ""


def test_empty_key_is_an_auth_error_not_silence():
    cfg = ProviderConfig("deepseek", "m", "KHONG_TON_TAI", "https://x/v1")
    with pytest.raises(LLMAuthError):
        llm.build_provider(cfg, env={})


def test_provider_from_env_uses_the_provider_name_as_label():
    provider = llm.provider_from_env({
        "KS_LLM_PROVIDER": "deepseek",
        "KS_LLM_MODEL": "deepseek-v4-flash",
        "KS_LLM_BASE_URL": "https://api.deepseek.com/v1",
        "KS_LLM_API_KEY_ENV": "DEEPSEEK_API_KEY",
        "DEEPSEEK_API_KEY": "k",
    })
    assert provider.name == "deepseek"
    assert provider.model == "deepseek-v4-flash"


# ---------------------------------------------------------------- truncation (reasoning model)


def test_openai_compatible_finish_reason_length_is_truncated():
    """MEASURED with deepseek-v4-flash: reasoning tokens count toward max_tokens,
    so a response can stop mid-way. The cause must be named, instead of letting
    truncated JSON reach the parser and surface as a puzzling LLMParseError."""
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


def test_anthropic_stop_reason_max_tokens_is_truncated():
    from ks.llm import LLMTruncatedError
    data = {"content": [{"text": "["}], "stop_reason": "max_tokens"}
    with pytest.raises(LLMTruncatedError):
        AnthropicProvider("m", "k", post=fake_post(200, data)).complete(MSGS)


def test_truncated_is_a_transient_subclass_so_it_is_retryable():
    """Reasoning tokens vary per run — the same max_tokens is sometimes enough, sometimes not."""
    from ks.llm import LLMTruncatedError
    assert issubclass(LLMTruncatedError, LLMTransientError)


def test_truncated_WITH_content_is_still_truncated_NOT_a_parse_error():
    """Mnemosyne measured 40 real calls: at max_tokens=350, 5/10 truncated replies still
    RETURNED content — half-written JSON, up to 310 characters. Not a rare case.

    Without checking finish_reason those go straight to the parser and report a
    FORMAT error, sending the log reader off to inspect the prompt when the real
    error is the token budget. Exactly KS's original production bug.
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


def test_VALID_JSON_with_finish_reason_length_is_still_rejected():
    """The most dangerous silent case: the model writes the closing `]` and then runs out of
    tokens. The string parses, but the content is INCOMPLETE.

    Accepting it would silently lose data — part of an answer treated as the whole.
    The token budget must win over syntax: truncated means discarded, no salvage.
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


def test_finish_reason_stop_lets_valid_content_through():
    """Control: the truncation check must not block a healthy response."""
    data = {"choices": [{"message": {"content": "[]"}, "finish_reason": "stop"}]}
    provider = OpenAICompatibleProvider("m", "k", base_url="https://x/v1", post=fake_post(200, data))
    assert provider.complete(MSGS) == "[]"
