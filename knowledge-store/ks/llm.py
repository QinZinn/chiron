"""LLM client của KS. Viết riêng, KHÔNG import gì từ Horae, nhưng cùng quy ước:
chữ ký hàm, phân loại lỗi, tên key config.

Taxonomy lỗi là superset của Horae: giữ 4 lớp gốc kèm luật fallback, thêm
LLMParseError và LLMRefusalError (Horae deterministic nên không cần).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Message:
    """Tin nhắn chat. role = "user" | "system" | "assistant"."""

    role: str
    content: str


# ---------------------------------------------------------------- errors


class LLMError(Exception):
    """Lỗi chung cho mọi provider."""


class LLMAuthError(LLMError):
    """401/403 — key sai / hết hạn."""


class LLMQuotaError(LLMError):
    """429 — hết quota / rate limit."""


class LLMTransientError(LLMError):
    """5xx, timeout, mạng. Timeout nằm TRONG đây, không tách lớp riêng."""


class LLMBadRequestError(LLMError):
    """4xx khác — LỖI CỦA TA. Không retry."""


class LLMParseError(LLMError):
    """HTTP 200 nhưng thân phản hồi sai cấu trúc."""


class LLMRefusalError(LLMError):
    """Model từ chối trả lời."""


class LLMTruncatedError(LLMTransientError):
    """Phản hồi bị cắt vì cạn max_tokens.

    Là con của LLMTransientError nên retry được: với model reasoning, lượng
    reasoning token thay đổi mỗi lần chạy, cùng một max_tokens lúc đủ lúc không.
    Tách lớp riêng để thông báo lỗi nói thẳng nguyên nhân, thay vì để phản hồi
    cụt lọt xuống json.loads rồi hiện ra dưới dạng LLMParseError khó hiểu.
    """


# ---------------------------------------------------------------- protocol


class LLMProvider(Protocol):
    name: str
    model: str

    def complete(
        self, messages: list[Message], *, max_tokens: int = 1000, temperature: float = 0.0
    ) -> str: ...


class HttpPost(Protocol):
    """POST tới API. Trả (status, body_json). Fake thay thế được."""

    def __call__(self, url: str, headers: dict[str, str], body: str) -> tuple[int, Any]: ...


# ---------------------------------------------------------------- config


@dataclass(frozen=True)
class ProviderConfig:
    """Cấu hình một provider. api_key_env là TÊN biến môi trường, không phải giá trị."""

    name: str  # "anthropic" | tên bất kỳ cho OpenAI-compatible ("deepseek", ...)
    model: str
    api_key_env: str
    base_url: str = ""  # bắt buộc với OpenAI-compatible


# ---------------------------------------------------------------- http


def _default_post(url: str, headers: dict[str, str], body: str) -> tuple[int, Any]:
    req = urllib.request.Request(url, data=body.encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return (resp.status, json.loads(resp.read().decode("utf-8")))
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            return (exc.code, json.loads(text))
        except json.JSONDecodeError:
            return (exc.code, text)
    except urllib.error.URLError as exc:
        raise LLMTransientError(f"lỗi mạng: {exc.reason}") from exc
    except TimeoutError as exc:
        raise LLMTransientError("timeout") from exc


def _raise_error(status: int, data: Any, provider: str) -> None:
    """Map HTTP status → đúng loại lỗi.

    KHÔNG thêm heuristic đoán định dạng lỗi bên thứ ba (ví dụ soi mã Cloudflare):
    định dạng WAF không có tài liệu, không ổn định, và hàm này dùng chung cho
    mọi provider.
    """
    msg = str(data)[:200]
    if status in (401, 403):
        raise LLMAuthError(f"{provider}: {status} — {msg}")
    if status == 429:
        raise LLMQuotaError(f"{provider}: 429 — {msg}")
    if 400 <= status < 500:
        raise LLMBadRequestError(f"{provider}: {status} — {msg}")
    if status >= 500:
        raise LLMTransientError(f"{provider}: {status} — {msg}")
    raise LLMTransientError(f"{provider}: status lạ {status} — {msg}")


# ---------------------------------------------------------------- providers


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str, api_key: str, *, post: HttpPost | None = None):
        self.model = model
        self._api_key = api_key
        self._post = post or _default_post

    def complete(
        self, messages: list[Message], *, max_tokens: int = 1000, temperature: float = 0.0
    ) -> str:
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = json.dumps({
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        })
        status, data = self._post(url, headers, body)
        if status != 200:
            _raise_error(status, data, self.name)

        if not isinstance(data, dict):
            raise LLMParseError(f"{self.name}: thân phản hồi không phải object — {str(data)[:200]}")
        # stop_reason "refusal" là field có tài liệu của Anthropic, không phải đoán.
        if data.get("stop_reason") == "refusal":
            raise LLMRefusalError(f"{self.name}: model từ chối trả lời")
        if data.get("stop_reason") == "max_tokens":
            raise LLMTruncatedError(
                f"{self.name}: phản hồi bị cắt vì cạn max_tokens — tăng ngân sách token"
            )
        try:
            return data["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMParseError(f"{self.name}: thiếu content[0].text — {str(data)[:200]}") from exc


class OpenAICompatibleProvider:
    """Mọi API dạng OpenAI: {base_url}/chat/completions, Bearer token."""

    def __init__(
        self,
        model: str,
        api_key: str,
        *,
        base_url: str,
        name: str = "openai_compatible",
        post: HttpPost | None = None,
    ):
        self.model = model
        self.name = name
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._post = post or _default_post

    def complete(
        self, messages: list[Message], *, max_tokens: int = 1000, temperature: float = 0.0
    ) -> str:
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
        }
        body = json.dumps({
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        })
        status, data = self._post(url, headers, body)
        if status != 200:
            _raise_error(status, data, self.name)

        if not isinstance(data, dict):
            raise LLMParseError(f"{self.name}: thân phản hồi không phải object — {str(data)[:200]}")
        try:
            choice = data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMParseError(f"{self.name}: thiếu choices[0].message — {str(data)[:200]}") from exc

        # `refusal` và finish_reason="content_filter" đều là field có tài liệu của
        # OpenAI API. Không suy đoán ngoài hai field này.
        if message.get("refusal"):
            raise LLMRefusalError(f"{self.name}: {str(message['refusal'])[:200]}")
        if choice.get("finish_reason") == "content_filter":
            raise LLMRefusalError(f"{self.name}: finish_reason=content_filter")
        if choice.get("finish_reason") == "length":
            usage = data.get("usage", {}) or {}
            detail = usage.get("completion_tokens_details", {}) or {}
            raise LLMTruncatedError(
                f"{self.name}: phản hồi bị cắt vì cạn max_tokens — tăng ngân sách token."
                f" completion_tokens={usage.get('completion_tokens')},"
                f" reasoning_tokens={detail.get('reasoning_tokens')}"
            )

        content = message.get("content")
        if not isinstance(content, str):
            raise LLMParseError(f"{self.name}: content không phải chuỗi — {str(data)[:200]}")
        return content


# ---------------------------------------------------------------- chọn provider


PROVIDER_ENV = "KS_LLM_PROVIDER"
BASE_URL_ENV = "KS_LLM_BASE_URL"
MODEL_ENV = "KS_LLM_MODEL"
API_KEY_ENV_ENV = "KS_LLM_API_KEY_ENV"


def config_from_env(env: dict[str, str] | None = None) -> ProviderConfig:
    """Đọc cấu hình provider từ môi trường. Thiếu biến bắt buộc → LLMBadRequestError."""
    env = os.environ if env is None else env
    name = env.get(PROVIDER_ENV, "")
    model = env.get(MODEL_ENV, "")
    api_key_env = env.get(API_KEY_ENV_ENV, "")
    base_url = env.get(BASE_URL_ENV, "")

    missing = [
        var
        for var, val in ((PROVIDER_ENV, name), (MODEL_ENV, model), (API_KEY_ENV_ENV, api_key_env))
        if not val
    ]
    if missing:
        raise LLMBadRequestError(f"Thiếu biến môi trường: {', '.join(missing)}")
    if name != "anthropic" and not base_url:
        raise LLMBadRequestError(f"Provider {name} là OpenAI-compatible, cần {BASE_URL_ENV}")

    return ProviderConfig(name=name, model=model, api_key_env=api_key_env, base_url=base_url)


def build_provider(
    config: ProviderConfig,
    *,
    env: dict[str, str] | None = None,
    post: HttpPost | None = None,
) -> LLMProvider:
    """Dựng provider từ config. Key rỗng → LLMAuthError (không im lặng bỏ qua)."""
    env = os.environ if env is None else env
    api_key = env.get(config.api_key_env, "")
    if not api_key:
        raise LLMAuthError(f"Biến môi trường {config.api_key_env} rỗng hoặc không tồn tại")

    if config.name == "anthropic":
        return AnthropicProvider(config.model, api_key, post=post)
    return OpenAICompatibleProvider(
        config.model, api_key, base_url=config.base_url, name=config.name, post=post
    )


def provider_from_env(
    env: dict[str, str] | None = None, *, post: HttpPost | None = None
) -> LLMProvider:
    return build_provider(config_from_env(env), env=env, post=post)
