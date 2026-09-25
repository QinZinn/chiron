//! An LLM provider for any OpenAI-compatible `/chat/completions` endpoint.
//!
//! `LLM_PROVIDER` used to accept exactly one value and panic on anything else,
//! which made the `LLMProvider` trait decorative: there was one implementation
//! and no way to reach a second. This is the generic one — DeepSeek, OpenAI,
//! Together, a local vLLM or Ollama server all speak this shape — configured
//! entirely from the environment.
//!
//! The dedicated `deepseek` provider stays as the default. It is not redundant:
//! it carries DeepSeek-specific handling (reasoning-token accounting, the
//! truncation classification in `describe_llm_failure`) that a generic client
//! has no business guessing at.

use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use crate::llm_provider::{LLMError, LLMMessage, LLMProvider, LLMResponse};

pub const PROVIDER_NAME: &str = "openai_compatible";

/// Timeout per call. Generous because reasoning models legitimately take
/// tens of seconds; short timeouts turn a slow answer into a lost one.
const TIMEOUT_SECS: u64 = 180;

pub struct OpenAiCompatibleClient {
    base_url: String,
    api_key: String,
    model: String,
    http: reqwest::Client,
}

/// Written by hand rather than derived: a derived Debug prints `api_key`, and
/// this type ends up in panic messages at startup.
impl std::fmt::Debug for OpenAiCompatibleClient {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("OpenAiCompatibleClient")
            .field("base_url", &self.base_url)
            .field("model", &self.model)
            .field("api_key", &"<redacted>")
            .finish()
    }
}

#[derive(Debug, Serialize)]
struct ChatRequest<'a> {
    model: &'a str,
    messages: Vec<RequestMessage<'a>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    max_tokens: Option<u32>,
}

#[derive(Debug, Serialize)]
struct RequestMessage<'a> {
    role: &'a str,
    content: &'a str,
}

#[derive(Debug, Deserialize)]
struct ChatResponse {
    choices: Vec<Choice>,
    usage: Option<Usage>,
}

#[derive(Debug, Deserialize)]
struct Choice {
    message: ResponseMessage,
    finish_reason: Option<String>,
}

#[derive(Debug, Deserialize)]
struct ResponseMessage {
    content: Option<String>,
}

#[derive(Debug, Deserialize)]
struct Usage {
    total_tokens: Option<u32>,
}

impl OpenAiCompatibleClient {
    /// Build from `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`.
    ///
    /// Returns the missing variable's name rather than a bare None, so startup
    /// can say which one to set instead of "provider unavailable".
    pub fn from_env() -> Result<Self, String> {
        let base_url = std::env::var("LLM_BASE_URL").map_err(|_| "LLM_BASE_URL".to_string())?;
        let api_key = std::env::var("LLM_API_KEY").map_err(|_| "LLM_API_KEY".to_string())?;
        let model = std::env::var("LLM_MODEL").map_err(|_| "LLM_MODEL".to_string())?;
        if base_url.trim().is_empty() {
            return Err("LLM_BASE_URL".into());
        }
        if api_key.trim().is_empty() {
            return Err("LLM_API_KEY".into());
        }
        if model.trim().is_empty() {
            return Err("LLM_MODEL".into());
        }
        Ok(Self {
            base_url: base_url.trim_end_matches('/').to_string(),
            api_key,
            model,
            http: reqwest::Client::builder()
                .timeout(std::time::Duration::from_secs(TIMEOUT_SECS))
                .build()
                .expect("building a reqwest client with only a timeout cannot fail"),
        })
    }

    pub fn model(&self) -> &str {
        &self.model
    }
}

#[async_trait]
impl LLMProvider for OpenAiCompatibleClient {
    /// `model` overrides the configured one for a single call, matching the
    /// trait (and the DeepSeek client): handlers occasionally want a cheaper
    /// model for a cheap job.
    async fn chat_completion(
        &self,
        messages: &[LLMMessage],
        model: Option<&str>,
    ) -> Result<LLMResponse, LLMError> {
        let body = ChatRequest {
            model: model.unwrap_or(&self.model),
            messages: messages
                .iter()
                .map(|m| RequestMessage { role: m.role, content: &m.content })
                .collect(),
            max_tokens: None,
        };

        let resp = self
            .http
            .post(format!("{}/chat/completions", self.base_url))
            .bearer_auth(&self.api_key)
            .json(&body)
            .send()
            .await
            .map_err(|e| LLMError::Network(e.to_string()))?;

        let status = resp.status();
        let text = resp.text().await.map_err(|e| LLMError::Network(e.to_string()))?;
        if !status.is_success() {
            return Err(LLMError::Http { status: status.as_u16(), body: text });
        }

        parse_completion(&text)
    }
}

/// Turn a 2xx response body into an [`LLMResponse`], **rejecting a truncated
/// completion**.
///
/// Kept separate from the network call so the rule can be tested without a
/// server. `finish_reason == "length"` is checked *before* `content` is read:
/// a call that spent its token budget can come back with an empty or
/// mid-sentence message, and handing that on makes a downstream JSON parser
/// report a formatting problem — sending whoever reads the error to the wrong
/// place. The DeepSeek client makes the same check in `to_llm_response`.
fn parse_completion(text: &str) -> Result<LLMResponse, LLMError> {
    let parsed: ChatResponse =
        serde_json::from_str(text).map_err(|e| LLMError::Parse(e.to_string()))?;
    let choice = parsed
        .choices
        .into_iter()
        .next()
        .ok_or_else(|| LLMError::Parse("response contained no choices".into()))?;
    let total_tokens = parsed.usage.and_then(|u| u.total_tokens).unwrap_or(0);

    let finish_reason = choice.finish_reason.clone().unwrap_or_default();
    if finish_reason == "length" {
        return Err(LLMError::Truncated { finish_reason: "length".into(), total_tokens });
    }

    let content = choice
        .message
        .content
        .filter(|c| !c.trim().is_empty())
        .ok_or_else(|| LLMError::Parse("response message had no content".into()))?;

    Ok(LLMResponse { content, total_tokens, finish_reason })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// All the env-var cases in ONE test on purpose: tests in a binary share
    /// the process environment and run in parallel, so separate tests that
    /// each set `LLM_MODEL` race and fail each other at random.
    #[test]
    fn env_configuration_is_reported_precisely() {
        // Which variable is missing must come back by name, so startup can say
        // what to fix instead of "provider unavailable".
        temp_env(&[("LLM_BASE_URL", None), ("LLM_API_KEY", None), ("LLM_MODEL", None)], || {
            assert_eq!(OpenAiCompatibleClient::from_env().unwrap_err(), "LLM_BASE_URL");
        });
        temp_env(
            &[("LLM_BASE_URL", Some("https://api.example.com/v1")), ("LLM_API_KEY", None), ("LLM_MODEL", None)],
            || assert_eq!(OpenAiCompatibleClient::from_env().unwrap_err(), "LLM_API_KEY"),
        );
        temp_env(
            &[("LLM_BASE_URL", Some("https://api.example.com/v1")), ("LLM_API_KEY", Some("k")), ("LLM_MODEL", None)],
            || assert_eq!(OpenAiCompatibleClient::from_env().unwrap_err(), "LLM_MODEL"),
        );

        // An empty value in .env is the commonest way to "configure" a provider
        // by accident; accepting it would surface later as a 401.
        temp_env(
            &[
                ("LLM_BASE_URL", Some("https://api.example.com/v1")),
                ("LLM_API_KEY", Some("   ")),
                ("LLM_MODEL", Some("m")),
            ],
            || assert_eq!(OpenAiCompatibleClient::from_env().unwrap_err(), "LLM_API_KEY"),
        );

        // A trailing slash must not produce //chat/completions — and Debug must
        // not print the key, because this type shows up in startup panics.
        // Checked here rather than in a test of its own: two tests that both
        // set LLM_* race in one process and fail each other at random.
        temp_env(
            &[
                ("LLM_BASE_URL", Some("https://api.example.com/v1/")),
                ("LLM_API_KEY", Some("sk-should-not-appear")),
                ("LLM_MODEL", Some("m")),
            ],
            || {
                let c = OpenAiCompatibleClient::from_env().unwrap();
                assert_eq!(c.base_url, "https://api.example.com/v1");
                let printed = format!("{c:?}");
                assert!(!printed.contains("sk-should-not-appear"), "{printed}");
                assert!(printed.contains("redacted"), "{printed}");
            },
        );
    }

    fn body(content: &str, finish_reason: &str) -> String {
        serde_json::json!({
            "choices": [{
                "message": { "role": "assistant", "content": content },
                "finish_reason": finish_reason,
            }],
            "usage": { "total_tokens": 4096 },
        })
        .to_string()
    }

    #[test]
    fn a_normal_completion_passes_through() {
        let r = parse_completion(&body("Xin chào", "stop")).unwrap();
        assert_eq!(r.content, "Xin chào");
        assert_eq!(r.finish_reason, "stop");
        assert_eq!(r.total_tokens, 4096);
    }

    #[test]
    fn length_with_empty_content_is_truncated_not_a_parse_error() {
        // The reported failure: reasoning tokens eat the budget, the message
        // comes back empty, and the caller blames the JSON parser.
        match parse_completion(&body("", "length")) {
            Err(LLMError::Truncated { finish_reason, total_tokens }) => {
                assert_eq!(finish_reason, "length");
                assert_eq!(total_tokens, 4096);
            }
            other => panic!("expected Truncated, got {other:?}"),
        }
    }

    #[test]
    fn length_with_partial_content_is_still_truncated() {
        // Cut mid-sentence: non-empty, so only finish_reason can tell.
        assert!(matches!(
            parse_completion(&body("{\"title\": \"Quang h", "length")),
            Err(LLMError::Truncated { .. })
        ));
    }

    #[test]
    fn empty_content_without_length_is_a_parse_error() {
        assert!(matches!(parse_completion(&body("  ", "stop")), Err(LLMError::Parse(_))));
    }

    #[test]
    fn missing_finish_reason_is_accepted_when_content_is_present() {
        // Some compatible servers omit the field; that must not be an error.
        let text = serde_json::json!({
            "choices": [{ "message": { "content": "ok" } }],
        })
        .to_string();
        let r = parse_completion(&text).unwrap();
        assert_eq!((r.content.as_str(), r.finish_reason.as_str(), r.total_tokens), ("ok", "", 0));
    }

    #[test]
    fn no_choices_and_malformed_bodies_are_parse_errors() {
        assert!(matches!(parse_completion(r#"{"choices": []}"#), Err(LLMError::Parse(_))));
        assert!(matches!(parse_completion("not json"), Err(LLMError::Parse(_))));
    }

    /// Set/restore env vars around a closure. Tests in one binary share the
    /// process environment, so this restores what it found.
    fn temp_env(vars: &[(&str, Option<&str>)], f: impl FnOnce()) {
        let previous: Vec<(String, Option<String>)> = vars
            .iter()
            .map(|(k, _)| ((*k).to_string(), std::env::var(k).ok()))
            .collect();
        for (k, v) in vars {
            match v {
                Some(v) => unsafe { std::env::set_var(k, v) },
                None => unsafe { std::env::remove_var(k) },
            }
        }
        f();
        for (k, v) in previous {
            match v {
                Some(v) => unsafe { std::env::set_var(&k, v) },
                None => unsafe { std::env::remove_var(&k) },
            }
        }
    }
}
