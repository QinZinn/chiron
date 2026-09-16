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

        let parsed: ChatResponse =
            serde_json::from_str(&text).map_err(|e| LLMError::Parse(e.to_string()))?;
        let choice = parsed
            .choices
            .into_iter()
            .next()
            .ok_or_else(|| LLMError::Parse("response contained no choices".into()))?;
        let total_tokens = parsed.usage.and_then(|u| u.total_tokens).unwrap_or(0);

        // Truncation is reported the same way the DeepSeek client reports it,
        // because the handlers' shared failure classification depends on the
        // distinction: a truncated call was billed and may be worth retrying
        // smaller, a dead one was not.
        let finish_reason = choice.finish_reason.clone().unwrap_or_default();
        if finish_reason == "length" {
            return Err(LLMError::Truncated {
                finish_reason: "length".into(),
                total_tokens,
            });
        }

        let content = choice
            .message
            .content
            .filter(|c| !c.trim().is_empty())
            .ok_or_else(|| LLMError::Parse("response message had no content".into()))?;

        Ok(LLMResponse { content, total_tokens, finish_reason })
    }
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
