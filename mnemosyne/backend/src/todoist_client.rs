//! HTTP client for Todoist — the queue between Mnemosyne and Horae.
//!
//! Mnemosyne never calls Horae. When a card turns weak it files an `@ontap`
//! task in Todoist (see [`crate::weak_cards`]); Horae reads Todoist like any
//! other task source and schedules it. This client is the only piece that
//! talks to Todoist.
//!
//! ## Which API
//!
//! Todoist's **unified API v1** (`https://api.todoist.com/api/v1`). REST v2
//! and Sync v9 are retired — `GET /rest/v2/tasks` and `POST /sync/v9/sync`
//! both answer `410 This endpoint is deprecated` (checked with curl on
//! 2026-09-13), so code or examples written against either fail outright.
//!
//! v1 has REST-style task endpoints alongside `/sync`, and those are what this
//! uses: plain JSON bodies, and the real task id comes straight back in the
//! response, with no `temp_id_mapping` to unpack. The wire format below is
//! taken from Todoist's published OpenAPI document
//! (`https://developer.todoist.com/openapi.json`), not from a summary of it:
//!
//! - `POST /tasks` — body `{content, description, labels}`; `content` is the
//!   only required field. `200` with the task (`ItemSyncView`), whose `id`
//!   is a string.
//! - `POST /tasks/{id}` — same fields, all optional; `200` with the task.
//! - `POST /tasks/{id}/close` — no body; `2xx`.
//!
//! ## Labels are names, and the name carries the `@`
//!
//! `labels` is a list of label *names*. In the account this runs against the
//! existing label is named `@ontap` — the `@` is part of the name, not
//! Todoist's display prefix (read back through the Todoist connector on
//! 2026-09-13; the live "Ôn IELTS [90m/ngày]" task carries `["@ontap"]`).
//! Sending `"ontap"` would quietly create a second label. Horae accepts both
//! spellings — `parsing._normalize_labels` strips a leading `@` — so matching
//! the existing name is purely about not littering the account.
//!
//! Error taxonomy mirrors [`crate::ks_client::KsError`] (unreachable / http /
//! parse) — see [`TodoistError::is_permanent`] for why the HTTP case is split
//! further when logging.

use async_trait::async_trait;
use serde::{Deserialize, Serialize};

const BASE_URL: &str = "https://api.todoist.com/api/v1";

/// The label Horae reads as "ongoing review" (see the module docs for why the
/// name includes the `@`).
pub const ONTAP_LABEL: &str = "@ontap";

/// Short on purpose: these calls sit inside `POST /review`, and nothing on
/// Todoist's side is slow by nature. A learner waiting on a flashcard should
/// not wait long on a Todoist outage.
const REQUEST_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(5);

// ---------------------------------------------------------------------------
// Wire types
// ---------------------------------------------------------------------------

#[derive(Debug, Serialize)]
struct CreateTaskRequest<'a> {
    content: &'a str,
    description: &'a str,
    labels: &'a [&'a str],
}

#[derive(Debug, Serialize)]
struct UpdateTaskRequest<'a> {
    content: &'a str,
    description: &'a str,
}

/// The fields of Todoist's `ItemSyncView` this client reads. Unknown fields
/// are ignored so Todoist can grow the object without breaking us.
#[derive(Debug, Deserialize)]
struct TaskBody {
    id: String,
    /// Required in the published schema; defaulted anyway, because reading a
    /// missing flag as "still open" only costs one more update later.
    #[serde(default)]
    checked: bool,
    #[serde(default)]
    is_deleted: bool,
}

// ---------------------------------------------------------------------------
// Outcome / error types
// ---------------------------------------------------------------------------

/// What an update found out about the task on Todoist's side.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RemoteTaskState {
    Open,
    /// Somebody completed or deleted it in Todoist. It no longer schedules
    /// anything, so the caller should stop treating it as the open task.
    Gone,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TodoistError {
    /// Timeout, connection refused, DNS/TLS — Todoist did not answer.
    Unreachable(String),
    /// A non-success status.
    Http { status: u16, body: String },
    /// The body did not match the documented schema.
    Parse(String),
}

impl TodoistError {
    /// `true` when retrying can never help on its own: the token is missing,
    /// wrong or revoked (401/403). Everything else — timeouts, 429, 5xx, even
    /// a parse error after an API change — is logged as something the next
    /// trigger will retry.
    ///
    /// The split exists for the log line. The weak-card flow retries by
    /// design (every later review tries again), so a transient failure needs
    /// no action — but a bad token would fail on every single review forever,
    /// and a line that looks the same as a blip is one nobody acts on.
    pub fn is_permanent(&self) -> bool {
        matches!(self, TodoistError::Http { status: 401 | 403, .. })
    }

    /// `true` for the 404 Todoist gives for a task that no longer exists.
    pub fn is_not_found(&self) -> bool {
        matches!(self, TodoistError::Http { status: 404, .. })
    }
}

impl std::fmt::Display for TodoistError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            TodoistError::Unreachable(m) => write!(f, "Todoist unreachable: {m}"),
            TodoistError::Http { status, body } => {
                let snippet: String = body.chars().take(500).collect();
                write!(f, "Todoist HTTP {status}: {snippet}")
            }
            TodoistError::Parse(m) => write!(f, "Todoist parse error: {m}"),
        }
    }
}

impl std::error::Error for TodoistError {}

// ---------------------------------------------------------------------------
// Pure helpers (unit-testable without touching the network)
// ---------------------------------------------------------------------------

fn check_status(status: u16, body: &str) -> Result<(), TodoistError> {
    if (200..300).contains(&status) {
        Ok(())
    } else {
        Err(TodoistError::Http { status, body: body.to_string() })
    }
}

fn parse_task(body: &str) -> Result<TaskBody, TodoistError> {
    let task: TaskBody = serde_json::from_str(body).map_err(|e| {
        TodoistError::Parse(format!(
            "{e}; body snippet: {}",
            body.chars().take(500).collect::<String>()
        ))
    })?;
    if task.id.is_empty() {
        return Err(TodoistError::Parse("task in response has an empty id".to_string()));
    }
    Ok(task)
}

fn remote_state(task: &TaskBody) -> RemoteTaskState {
    if task.checked || task.is_deleted {
        RemoteTaskState::Gone
    } else {
        RemoteTaskState::Open
    }
}

// ---------------------------------------------------------------------------
// Trait + client
// ---------------------------------------------------------------------------

/// The three Todoist operations the weak-card flow needs. A trait so tests can
/// swap in a fake — the flow's whole promise is about what happens when
/// Todoist misbehaves, and that cannot be arranged against the real service.
#[async_trait]
pub trait TodoistApi: Send + Sync {
    /// Create a task; returns its Todoist id.
    async fn create_task(
        &self,
        content: &str,
        description: &str,
        labels: &[&str],
    ) -> Result<String, TodoistError>;

    /// Rewrite a task's title and description. Reports whether the task is
    /// still open, since the response is the one place that says so for free.
    async fn update_task(
        &self,
        task_id: &str,
        content: &str,
        description: &str,
    ) -> Result<RemoteTaskState, TodoistError>;

    async fn close_task(&self, task_id: &str) -> Result<(), TodoistError>;
}

pub struct TodoistClient {
    http: reqwest::Client,
    token: String,
}

impl TodoistClient {
    /// Build a client from `TODOIST_TOKEN` — the same variable Horae reads,
    /// so one real token has one name across both modules.
    ///
    /// Returns `None` when the token is absent or empty. **Callers must not
    /// panic on `None`.** Like the Knowledge Store client, this feeds an
    /// auxiliary feature: a review must be recorded whether or not a Todoist
    /// task can be filed about it.
    pub fn from_env() -> Option<Self> {
        let token = std::env::var("TODOIST_TOKEN").ok()?;
        if token.is_empty() {
            return None;
        }
        let http = reqwest::Client::builder()
            .timeout(REQUEST_TIMEOUT)
            .build()
            .expect("reqwest client construction should not fail with sane defaults");
        Some(Self { http, token })
    }

    async fn post(
        &self,
        path: &str,
        json: Option<&impl Serialize>,
    ) -> Result<String, TodoistError> {
        let mut req = self.http.post(format!("{BASE_URL}{path}")).bearer_auth(&self.token);
        if let Some(json) = json {
            req = req.json(json);
        }
        let resp = req.send().await.map_err(|e| TodoistError::Unreachable(e.to_string()))?;
        let status = resp.status().as_u16();
        let body = resp.text().await.unwrap_or_default();
        check_status(status, &body)?;
        Ok(body)
    }
}

#[async_trait]
impl TodoistApi for TodoistClient {
    async fn create_task(
        &self,
        content: &str,
        description: &str,
        labels: &[&str],
    ) -> Result<String, TodoistError> {
        let body = self
            .post("/tasks", Some(&CreateTaskRequest { content, description, labels }))
            .await?;
        parse_task(&body).map(|t| t.id)
    }

    async fn update_task(
        &self,
        task_id: &str,
        content: &str,
        description: &str,
    ) -> Result<RemoteTaskState, TodoistError> {
        let body = self
            .post(&format!("/tasks/{task_id}"), Some(&UpdateTaskRequest { content, description }))
            .await?;
        parse_task(&body).map(|t| remote_state(&t))
    }

    async fn close_task(&self, task_id: &str) -> Result<(), TodoistError> {
        // The body is documented as an empty schema; nothing in it is read.
        self.post(&format!("/tasks/{task_id}/close"), None::<&()>).await.map(|_| ())
    }
}

/// An in-memory Todoist for tests: records every call, and fails or reports
/// tasks as completed on demand.
#[cfg(test)]
pub mod fake {
    use std::sync::Mutex;

    use super::*;

    #[derive(Debug, Clone, PartialEq, Eq)]
    pub enum Call {
        Create { content: String, description: String, labels: Vec<String> },
        Update { task_id: String, content: String, description: String },
        Close { task_id: String },
    }

    pub struct FakeTodoist {
        calls: Mutex<Vec<Call>>,
        fail_with: Mutex<Option<TodoistError>>,
        on_update: Mutex<RemoteTaskState>,
        latency: Mutex<std::time::Duration>,
    }

    impl Default for FakeTodoist {
        fn default() -> Self {
            Self {
                calls: Mutex::new(Vec::new()),
                fail_with: Mutex::new(None),
                on_update: Mutex::new(RemoteTaskState::Open),
                latency: Mutex::new(std::time::Duration::ZERO),
            }
        }
    }

    impl FakeTodoist {
        pub fn calls(&self) -> Vec<Call> {
            self.calls.lock().unwrap().clone()
        }

        /// Make every call fail with this error, or succeed again with `None`.
        /// Failed calls are still recorded.
        pub fn fail_with(&self, error: Option<TodoistError>) {
            *self.fail_with.lock().unwrap() = error;
        }

        /// What updates report about the task from now on.
        pub fn report_on_update(&self, state: RemoteTaskState) {
            *self.on_update.lock().unwrap() = state;
        }

        /// Make every call take this long — widens the window for races.
        pub fn slow_down(&self, latency: std::time::Duration) {
            *self.latency.lock().unwrap() = latency;
        }

        async fn record(&self, call: Call) -> Result<usize, TodoistError> {
            let n = {
                let mut calls = self.calls.lock().unwrap();
                calls.push(call);
                calls.len()
            };
            let latency = *self.latency.lock().unwrap();
            tokio::time::sleep(latency).await;
            match self.fail_with.lock().unwrap().clone() {
                Some(e) => Err(e),
                None => Ok(n),
            }
        }
    }

    #[async_trait]
    impl TodoistApi for FakeTodoist {
        async fn create_task(
            &self,
            content: &str,
            description: &str,
            labels: &[&str],
        ) -> Result<String, TodoistError> {
            let n = self.record(Call::Create {
                content: content.to_string(),
                description: description.to_string(),
                labels: labels.iter().map(|l| l.to_string()).collect(),
            })
            .await?;
            Ok(format!("fake-task-{n}"))
        }

        async fn update_task(
            &self,
            task_id: &str,
            content: &str,
            description: &str,
        ) -> Result<RemoteTaskState, TodoistError> {
            self.record(Call::Update {
                task_id: task_id.to_string(),
                content: content.to_string(),
                description: description.to_string(),
            })
            .await?;
            Ok(*self.on_update.lock().unwrap())
        }

        async fn close_task(&self, task_id: &str) -> Result<(), TodoistError> {
            self.record(Call::Close { task_id: task_id.to_string() }).await.map(|_| ())
        }
    }

    /// Lets a test keep a handle on the fake it hands to the app.
    #[async_trait]
    impl TodoistApi for std::sync::Arc<FakeTodoist> {
        async fn create_task(&self, c: &str, d: &str, l: &[&str]) -> Result<String, TodoistError> {
            self.as_ref().create_task(c, d, l).await
        }
        async fn update_task(&self, id: &str, c: &str, d: &str) -> Result<RemoteTaskState, TodoistError> {
            self.as_ref().update_task(id, c, d).await
        }
        async fn close_task(&self, id: &str) -> Result<(), TodoistError> {
            self.as_ref().close_task(id).await
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn create_request_sends_labels_as_a_field_not_in_the_title() {
        // Horae classifies by the `labels` field. "@ontap" typed into the
        // title would leave the task unlabelled and Horae would read it as an
        // ordinary assignment with no deadline — i.e. drop it.
        let req = CreateTaskRequest {
            content: "Ôn thẻ yếu — Unit 5 [20m/ngày]",
            description: "- Q: ...",
            labels: &[ONTAP_LABEL],
        };
        let json = serde_json::to_value(&req).unwrap();
        assert_eq!(json["labels"], serde_json::json!(["@ontap"]));
        assert!(!json["content"].as_str().unwrap().contains('@'));
        // No due date of any kind: an @ontap task has none by definition.
        let keys: Vec<&str> = json.as_object().unwrap().keys().map(String::as_str).collect();
        assert_eq!(keys.len(), 3, "unexpected fields: {keys:?}");
    }

    #[test]
    fn create_response_yields_the_string_id() {
        // Trimmed from the ItemSyncView example in Todoist's OpenAPI document.
        let body = r#"{"id":"6XGgmFVcrG5RRjVr","content":"Buy milk","labels":["@ontap"],
            "checked":false,"is_deleted":false,"project_id":"6XGgm6PHrGgMpCFX","due":null}"#;
        assert_eq!(parse_task(body).unwrap().id, "6XGgmFVcrG5RRjVr");
    }

    #[test]
    fn a_response_without_an_id_is_a_parse_error() {
        assert!(matches!(parse_task(r#"{"content":"x"}"#), Err(TodoistError::Parse(_))));
        assert!(matches!(parse_task(r#"{"id":""}"#), Err(TodoistError::Parse(_))));
        assert!(matches!(parse_task("<html>502</html>"), Err(TodoistError::Parse(_))));
    }

    #[test]
    fn a_completed_or_deleted_task_reads_as_gone() {
        let open = parse_task(r#"{"id":"1","checked":false,"is_deleted":false}"#).unwrap();
        let done = parse_task(r#"{"id":"1","checked":true,"is_deleted":false}"#).unwrap();
        let deleted = parse_task(r#"{"id":"1","checked":false,"is_deleted":true}"#).unwrap();
        assert_eq!(remote_state(&open), RemoteTaskState::Open);
        assert_eq!(remote_state(&done), RemoteTaskState::Gone);
        assert_eq!(remote_state(&deleted), RemoteTaskState::Gone);
    }

    #[test]
    fn only_auth_failures_are_permanent() {
        for status in [401, 403] {
            assert!(TodoistError::Http { status, body: String::new() }.is_permanent());
        }
        for status in [400, 404, 429, 500, 503] {
            assert!(!TodoistError::Http { status, body: String::new() }.is_permanent());
        }
        assert!(!TodoistError::Unreachable("timeout".into()).is_permanent());
        assert!(!TodoistError::Parse("bad".into()).is_permanent());
    }

    #[test]
    fn non_success_statuses_become_http_errors() {
        assert!(check_status(200, "").is_ok());
        assert!(check_status(204, "").is_ok());
        assert_eq!(
            check_status(410, "This endpoint is deprecated."),
            Err(TodoistError::Http { status: 410, body: "This endpoint is deprecated.".into() })
        );
    }

    // -- live check ----------------------------------------------------------
    //
    // Proves the wire format against the real API: creates a task, updates
    // it, closes it. Leaves one completed task in the account's history.
    //
    //     cargo test -p backend -- --ignored --nocapture live_todoist_round_trip

    #[tokio::test]
    #[ignore = "requires a real TODOIST_TOKEN; writes to that Todoist account"]
    async fn live_todoist_round_trip() {
        dotenvy::dotenv().ok();
        dotenvy::from_filename("../.env").ok();
        let client = TodoistClient::from_env().expect("TODOIST_TOKEN must be set in .env");

        let id = client
            .create_task("Mnemosyne live check [20m/ngày]", "created by live_todoist_round_trip", &[ONTAP_LABEL])
            .await
            .expect("create should succeed");
        eprintln!("[live] created {id}");

        let state = client
            .update_task(&id, "Mnemosyne live check [30m/ngày]", "updated")
            .await
            .expect("update should succeed");
        assert_eq!(state, RemoteTaskState::Open);

        client.close_task(&id).await.expect("close should succeed");
        eprintln!("[live] closed {id}");
    }
}
