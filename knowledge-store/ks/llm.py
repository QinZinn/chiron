"""The KS LLM client. Written separately, imports NOTHING from Horae, but follows its
conventions: function signatures, error classes, config key names.

The error taxonomy is a superset of Horae's: the 4 original classes and their
fallback rules, plus LLMParseError and LLMRefusalError (Horae is deterministic and needs neither).
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
    """A chat message. role = "user" | "system" | "assistant"."""

    role: str
    content: str


# ---------------------------------------------------------------- errors


class LLMError(Exception):
    """Common base for every provider."""


class LLMAuthError(LLMError):
    """401/403 — wrong or expired key."""


class LLMQuotaError(LLMError):
    """429 — out of quota / rate limited."""


class LLMTransientError(LLMError):
    """5xx, timeout, network. Timeouts belong HERE, not in a class of their own."""


class LLMBadRequestError(LLMError):
    """Any other 4xx — OUR mistake. Never retried."""


class LLMParseError(LLMError):
    """HTTP 200 but the response body has the wrong structure."""


class LLMRefusalError(LLMError):
    """The model refused to answer."""


class LLMTruncatedError(LLMTransientError):
    """The response was cut off because max_tokens ran out.

    A subclass of LLMTransientError, so it is retryable: with a reasoning model the
    number of reasoning tokens varies per run, and the same max_tokens is sometimes
    enough and sometimes not. A class of its own so the error message names the cause,
    instead of a truncated response reaching json.loads and surfacing as a puzzling LLMParseError.
    """


# ---------------------------------------------------------------- protocol


class LLMProvider(Protocol):
    name: str
    model: str

    def complete(
        self, messages: list[Message], *, max_tokens: int = 1000, temperature: float = 0.0
    ) -> str: ...


class HttpPost(Protocol):
    """POST to the API. Returns (status, body_json). Replaceable with a fake."""

    def __call__(self, url: str, headers: dict[str, str], body: str) -> tuple[int, Any]: ...


# ---------------------------------------------------------------- config


@dataclass(frozen=True)
class ProviderConfig:
    """One provider's configuration. api_key_env is the NAME of an environment variable, not its value."""

    name: str  # "anthropic" | any name for an OpenAI-compatible one ("deepseek", ...)
    model: str
    api_key_env: str
    base_url: str = ""  # required for OpenAI-compatible providers


# ---------------------------------------------------------------- http


# One 8500-token concept extraction takes ~30 s (measured 2026-09-25, ~280 tokens/s),
# so the EXTRACTION_MAX_TOKENS=16000 budget needs nearly 60 s — exactly the old
# timeout. 150 s leaves room while staying under the frontend proxy's 180 s timeout for /extract.
HTTP_TIMEOUT_SECONDS = 150


def _default_post(url: str, headers: dict[str, str], body: str) -> tuple[int, Any]:
    req = urllib.request.Request(url, data=body.encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            return (resp.status, json.loads(resp.read().decode("utf-8")))
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            return (exc.code, json.loads(text))
        except json.JSONDecodeError:
            return (exc.code, text)
    except urllib.error.URLError as exc:
        raise LLMTransientError(f"network error: {exc.reason}") from exc
    except TimeoutError as exc:
        raise LLMTransientError("timeout") from exc


def _raise_error(status: int, data: Any, provider: str) -> None:
    """Map an HTTP status → the right error class.

    Do NOT add heuristics guessing third-party error formats (e.g. sniffing
    Cloudflare codes): WAF formats are undocumented and unstable, and this function
    is shared by every provider.
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
    raise LLMTransientError(f"{provider}: unexpected status {status} — {msg}")


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
            raise LLMParseError(f"{self.name}: response body is not an object — {str(data)[:200]}")
        # stop_reason "refusal" is a documented Anthropic field, not a guess.
        if data.get("stop_reason") == "refusal":
            raise LLMRefusalError(f"{self.name}: the model refused to answer")
        if data.get("stop_reason") == "max_tokens":
            raise LLMTruncatedError(
                f"{self.name}: response cut off because max_tokens ran out — raise the token budget"
            )
        try:
            return data["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMParseError(f"{self.name}: missing content[0].text — {str(data)[:200]}") from exc


class OpenAICompatibleProvider:
    """Any OpenAI-style API: {base_url}/chat/completions, Bearer token."""

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
            raise LLMParseError(f"{self.name}: response body is not an object — {str(data)[:200]}")
        try:
            choice = data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMParseError(f"{self.name}: missing choices[0].message — {str(data)[:200]}") from exc

        # `refusal` and finish_reason="content_filter" are both documented OpenAI API
        # fields. No guessing beyond these two.
        if message.get("refusal"):
            raise LLMRefusalError(f"{self.name}: {str(message['refusal'])[:200]}")
        if choice.get("finish_reason") == "content_filter":
            raise LLMRefusalError(f"{self.name}: finish_reason=content_filter")
        if choice.get("finish_reason") == "length":
            usage = data.get("usage", {}) or {}
            detail = usage.get("completion_tokens_details", {}) or {}
            raise LLMTruncatedError(
                f"{self.name}: response cut off because max_tokens ran out — raise the token budget."
                f" completion_tokens={usage.get('completion_tokens')},"
                f" reasoning_tokens={detail.get('reasoning_tokens')}"
            )

        content = message.get("content")
        if not isinstance(content, str):
            raise LLMParseError(f"{self.name}: content is not a string — {str(data)[:200]}")
        return content


# ---------------------------------------------------------------- choosing a provider


PROVIDER_ENV = "KS_LLM_PROVIDER"
BASE_URL_ENV = "KS_LLM_BASE_URL"
MODEL_ENV = "KS_LLM_MODEL"
API_KEY_ENV_ENV = "KS_LLM_API_KEY_ENV"


def config_from_env(env: dict[str, str] | None = None) -> ProviderConfig:
    """Read the provider config from the environment. Missing required variable → LLMBadRequestError."""
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
        raise LLMBadRequestError(f"Missing environment variables: {', '.join(missing)}")
    if name != "anthropic" and not base_url:
        raise LLMBadRequestError(f"Provider {name} is OpenAI-compatible and needs {BASE_URL_ENV}")

    return ProviderConfig(name=name, model=model, api_key_env=api_key_env, base_url=base_url)


def build_provider(
    config: ProviderConfig,
    *,
    env: dict[str, str] | None = None,
    post: HttpPost | None = None,
) -> LLMProvider:
    """Build the provider from config. Empty key → LLMAuthError (never silently ignored)."""
    env = os.environ if env is None else env
    api_key = env.get(config.api_key_env, "")
    if not api_key:
        raise LLMAuthError(f"Environment variable {config.api_key_env} is empty or missing")

    if config.name == "anthropic":
        return AnthropicProvider(config.model, api_key, post=post)
    return OpenAICompatibleProvider(
        config.model, api_key, base_url=config.base_url, name=config.name, post=post
    )


def provider_from_env(
    env: dict[str, str] | None = None, *, post: HttpPost | None = None
) -> LLMProvider:
    return build_provider(config_from_env(env), env=env, post=post)
