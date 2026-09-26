//! "Ask" and "Solve" — the two chat modes the UI has always offered
//! and the backend never had.
//!
//! - `POST /chat/start`        — open a conversation with the first question
//! - `POST /chat/{id}/reply`   — keep it going
//! - `GET  /chat/{id}`         — read it back
//! - `GET  /chat`              — list this learner's conversations
//!
//! ## Why these are not Socratic sessions
//!
//! Socratic mode refuses to give answers; that is its entire pedagogical
//! point. These two do the opposite:
//!
//! - **ask** answers the question directly and briefly.
//! - **solve** works the problem in numbered steps and then stays open for
//!   "why step 3?".
//!
//! ## Plain text, not structured steps
//!
//! `solve` returns the steps as ordinary text rather than a JSON array of
//! step objects. A structured shape would look tidier in the API, but every
//! structured LLM response in this codebase has a parse-failure branch that
//! turns a usable answer into a 502 (see `describe_llm_failure`). For a
//! numbered explanation the structure buys the client nothing it cannot get
//! from line breaks, and costs a failure mode the learner would feel. Quiz
//! generation pays that cost because indices and choice arrays genuinely need
//! to be machine-readable; this does not.

use actix_web::{get, post, web, HttpResponse};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::{FromRow, PgPool};
use uuid::Uuid;

use super::{describe_llm_failure, error_response};
use crate::auth::{owns_study_set, AuthedUser};
use crate::llm_provider::{LLMMessage, LLMProvider};

/// Message cap per conversation. Higher than the Socratic cap (40) because
/// nothing here is trying to reach a teaching goal in a bounded number of
/// turns, but still bounded: an unbounded transcript is an unbounded prompt.
const MESSAGE_CAP: i64 = 80;

/// How many past messages are replayed to the model. Same reasoning as the
/// Socratic sliding window: cost per turn must not grow with transcript length.
const CONTEXT_WINDOW: usize = 20;

/// Cap on one message, mirroring the Feynman/generate input caps.
const MAX_MESSAGE_CHARS: usize = 8000;

/// Cap on study-set context injected into the system prompt.
const MAX_CARD_CONTEXT_CHARS: usize = 6000;

/// Sidebar titles are trimmed to this many characters of the opening message.
const TITLE_CHARS: usize = 80;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, sqlx::Type)]
#[sqlx(type_name = "chat_mode", rename_all = "lowercase")]
#[serde(rename_all = "lowercase")]
pub enum ChatMode {
    Ask,
    Solve,
}

impl ChatMode {
    fn interaction_type(self) -> &'static str {
        match self {
            ChatMode::Ask => "ask_answer",
            ChatMode::Solve => "solve_steps",
        }
    }

    fn system_prompt(self, card_context: Option<&str>) -> String {
        let base = match self {
            ChatMode::Ask => "\
You are a tutor for a high-school student. The student asks a question and \
wants a direct, correct and concise answer.

Rules:
- Answer the question directly first, then add a short explanation only if needed.
- Write in English, in a natural voice, without filler.
- Write maths/physics notation in plain text as it is usually written (Φ = B·S·cosα), never LaTeX.
- If the question is missing information, ask exactly one clarifying question.
- If you are not sure, say plainly that you are not sure. Never invent figures, \
formulas or citations.",
            ChatMode::Solve => "\
You are a tutor for a high-school student. The student gives you a problem and \
wants to see how it is solved, not just the final answer.

Rules:
- Solve it in numbered steps. One idea per step; say what you are doing and why.
- State each formula at the step that needs it.
- Finish with a clear \"Final answer:\" line.
- Write in English. Write maths/physics notation in plain text as it is usually written, never LaTeX.
- If the problem is missing information or contradicts itself, say so instead of guessing an answer.
- In later turns the student may ask about one specific step: answer that step, \
do not solve the whole problem again.",
        };
        match card_context {
            Some(ctx) if !ctx.trim().is_empty() => format!(
                "{base}\n\nThe student is studying the card set below. Prefer its wording \
                 and notation where they are relevant:\n\n{ctx}"
            ),
            _ => base.to_string(),
        }
    }
}

// ---------------------------------------------------------------------------
// Request / response types
// ---------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
pub struct StartRequest {
    pub mode: ChatMode,
    /// Optional study set whose cards become context. Must be the learner's.
    pub study_set_id: Option<Uuid>,
    pub message: String,
}

#[derive(Debug, Deserialize)]
pub struct ReplyRequest {
    pub message: String,
}

#[derive(Debug, Serialize)]
pub struct StartResponse {
    pub session_id: Uuid,
    pub mode: ChatMode,
    pub title: String,
    pub reply: String,
}

#[derive(Debug, Serialize)]
pub struct ReplyResponse {
    pub reply: String,
}

#[derive(Debug, Serialize, FromRow)]
pub struct MessageOut {
    pub role: String,
    pub content: String,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Serialize, FromRow)]
pub struct SessionSummary {
    pub id: Uuid,
    pub mode: ChatMode,
    pub set_id: Option<Uuid>,
    pub title: String,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub message_count: i64,
}

#[derive(Debug, Serialize)]
pub struct SessionResponse {
    pub session_id: Uuid,
    pub mode: ChatMode,
    pub set_id: Option<Uuid>,
    pub title: String,
    pub messages: Vec<MessageOut>,
}

#[derive(Debug, Serialize)]
pub struct ListResponse {
    pub sessions: Vec<SessionSummary>,
    pub count: usize,
}

#[derive(Debug, Deserialize)]
pub struct ListQuery {
    pub mode: Option<ChatMode>,
    pub limit: Option<i64>,
}

const DEFAULT_LIST_LIMIT: i64 = 50;
const MAX_LIST_LIMIT: i64 = 200;

#[derive(Debug, FromRow)]
struct SessionRow {
    id: Uuid,
    mode: ChatMode,
    set_id: Option<Uuid>,
    title: String,
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn title_from(message: &str) -> String {
    let trimmed = message.trim();
    let mut out: String = trimmed.chars().take(TITLE_CHARS).collect();
    if trimmed.chars().count() > TITLE_CHARS {
        out.push('…');
    }
    out
}

fn validate_message(message: &str) -> Result<&str, HttpResponse> {
    let trimmed = message.trim();
    if trimmed.is_empty() {
        return Err(error_response(
            actix_web::http::StatusCode::BAD_REQUEST,
            "message must not be empty",
        ));
    }
    if trimmed.chars().count() > MAX_MESSAGE_CHARS {
        return Err(error_response(
            actix_web::http::StatusCode::BAD_REQUEST,
            format!("message must be at most {MAX_MESSAGE_CHARS} characters"),
        ));
    }
    Ok(trimmed)
}

/// Cards of a study set as prompt context, truncated at the character cap.
async fn card_context(pool: &PgPool, set_id: Uuid) -> Option<String> {
    let rows: Vec<(String, String)> =
        sqlx::query_as("SELECT question, answer FROM cards WHERE set_id = $1 ORDER BY created_at")
            .bind(set_id)
            .fetch_all(pool)
            .await
            .ok()?;
    if rows.is_empty() {
        return None;
    }
    let mut ctx = String::new();
    for (q, a) in rows {
        let line = format!("- {q} → {a}\n");
        if ctx.len() + line.len() > MAX_CARD_CONTEXT_CHARS {
            break;
        }
        ctx.push_str(&line);
    }
    Some(ctx)
}

async fn log_ai_interaction(
    pool: &PgPool,
    user_id: Uuid,
    mode: ChatMode,
    input: &str,
    output: &str,
    tokens: u32,
) {
    let _ = sqlx::query(
        r#"INSERT INTO ai_interactions
             (user_id, interaction_type, input_text, output_text, tokens_used)
           VALUES ($1, $2::ai_interaction_type, $3, $4, $5)"#,
    )
    .bind(user_id)
    .bind(mode.interaction_type())
    .bind(input)
    .bind(output)
    .bind(tokens as i32)
    .execute(pool)
    .await;
}

/// Replay the tail of a conversation for the model.
async fn history_messages(pool: &PgPool, session_id: Uuid) -> Result<Vec<LLMMessage>, String> {
    let rows: Vec<MessageOut> = sqlx::query_as::<_, MessageOut>(
        r#"SELECT role, content, created_at FROM chat_messages
           WHERE session_id = $1 ORDER BY created_at"#,
    )
    .bind(session_id)
    .fetch_all(pool)
    .await
    .map_err(|e| format!("database error reading the conversation: {e}"))?;

    // Named `window_start`, not `start`: the #[post] macro defines a unit
    // struct called `start` in this module, which would shadow a binding.
    let window_start = rows.len().saturating_sub(CONTEXT_WINDOW);
    Ok(rows[window_start..]
        .iter()
        .map(|m| match m.role.as_str() {
            "user" => LLMMessage::user(&m.content),
            _ => LLMMessage::assistant(&m.content),
        })
        .collect())
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

#[post("/chat/start")]
pub async fn start(
    pool: web::Data<PgPool>,
    llm: web::Data<Box<dyn LLMProvider>>,
    user: AuthedUser,
    body: web::Json<StartRequest>,
) -> HttpResponse {
    let message = match validate_message(&body.message) {
        Ok(m) => m.to_string(),
        Err(resp) => return resp,
    };

    // A study set may be attached for context, but only the learner's own.
    if let Some(set_id) = body.study_set_id {
        match owns_study_set(pool.get_ref(), user.user_id, set_id).await {
            Ok(true) => {}
            Ok(false) => {
                return error_response(
                    actix_web::http::StatusCode::NOT_FOUND,
                    format!("study set {set_id} not found"),
                );
            }
            Err(e) => {
                return error_response(
                    actix_web::http::StatusCode::INTERNAL_SERVER_ERROR,
                    format!("database error checking study set ownership: {e}"),
                );
            }
        }
    }

    let context = match body.study_set_id {
        Some(set_id) => card_context(pool.get_ref(), set_id).await,
        None => None,
    };
    let system_prompt = body.mode.system_prompt(context.as_deref());
    let title = title_from(&message);

    let session: SessionRow = match sqlx::query_as::<_, SessionRow>(
        r#"INSERT INTO chat_sessions (user_id, mode, set_id, title)
           VALUES ($1, $2, $3, $4)
           RETURNING id, mode, set_id, title"#,
    )
    .bind(user.user_id)
    .bind(body.mode)
    .bind(body.study_set_id)
    .bind(&title)
    .fetch_one(pool.get_ref())
    .await
    {
        Ok(r) => r,
        Err(e) => {
            let (status, msg) = super::classify_db_error(&e);
            return error_response(status, msg);
        }
    };

    // The learner's message is stored before the model is called, so a
    // provider failure loses the answer but never the question.
    let _ = sqlx::query("INSERT INTO chat_messages (session_id, role, content) VALUES ($1, 'user', $2)")
        .bind(session.id)
        .bind(&message)
        .execute(pool.get_ref())
        .await;

    let llm_messages = vec![LLMMessage::system(system_prompt.clone()), LLMMessage::user(&message)];
    let prompt_log = format!("[chat:{:?}:start] {message}", body.mode);

    match llm.chat_completion(&llm_messages, None).await {
        Ok(resp) => {
            let answer = resp.content;
            let _ = sqlx::query(
                "INSERT INTO chat_messages (session_id, role, content) VALUES ($1, 'assistant', $2)",
            )
            .bind(session.id)
            .bind(&answer)
            .execute(pool.get_ref())
            .await;
            let _ = sqlx::query("UPDATE chat_sessions SET updated_at = now() WHERE id = $1")
                .bind(session.id)
                .execute(pool.get_ref())
                .await;
            log_ai_interaction(pool.get_ref(), user.user_id, body.mode, &prompt_log, &answer, resp.total_tokens).await;

            HttpResponse::Created().json(StartResponse {
                session_id: session.id,
                mode: session.mode,
                title: session.title,
                reply: answer,
            })
        }
        Err(e) => {
            let failure = describe_llm_failure(&e);
            log_ai_interaction(
                pool.get_ref(),
                user.user_id,
                body.mode,
                &prompt_log,
                &failure.placeholder,
                failure.tokens_used,
            )
            .await;
            error_response(actix_web::http::StatusCode::BAD_GATEWAY, failure.message)
        }
    }
}

#[post("/chat/{session_id}/reply")]
pub async fn reply(
    pool: web::Data<PgPool>,
    llm: web::Data<Box<dyn LLMProvider>>,
    user: AuthedUser,
    path: web::Path<Uuid>,
    body: web::Json<ReplyRequest>,
) -> HttpResponse {
    let session_id = path.into_inner();
    let message = match validate_message(&body.message) {
        Ok(m) => m.to_string(),
        Err(resp) => return resp,
    };

    // Looked up by (id, owner): another learner's conversation is "not found".
    let session: Option<SessionRow> = match sqlx::query_as::<_, SessionRow>(
        "SELECT id, mode, set_id, title FROM chat_sessions WHERE id = $1 AND user_id = $2",
    )
    .bind(session_id)
    .bind(user.user_id)
    .fetch_optional(pool.get_ref())
    .await
    {
        Ok(r) => r,
        Err(e) => {
            return error_response(
                actix_web::http::StatusCode::INTERNAL_SERVER_ERROR,
                format!("database error: {e}"),
            );
        }
    };
    let Some(session) = session else {
        return error_response(
            actix_web::http::StatusCode::NOT_FOUND,
            format!("conversation {session_id} not found"),
        );
    };

    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM chat_messages WHERE session_id = $1")
        .bind(session_id)
        .fetch_one(pool.get_ref())
        .await
        .unwrap_or(0);
    if count >= MESSAGE_CAP {
        return error_response(
            actix_web::http::StatusCode::BAD_REQUEST,
            "conversation has reached its length limit — start a new one",
        );
    }

    let _ = sqlx::query("INSERT INTO chat_messages (session_id, role, content) VALUES ($1, 'user', $2)")
        .bind(session_id)
        .bind(&message)
        .execute(pool.get_ref())
        .await;

    let context = match session.set_id {
        Some(set_id) => card_context(pool.get_ref(), set_id).await,
        None => None,
    };
    let mut llm_messages = vec![LLMMessage::system(session.mode.system_prompt(context.as_deref()))];
    match history_messages(pool.get_ref(), session_id).await {
        Ok(mut h) => llm_messages.append(&mut h),
        Err(e) => return error_response(actix_web::http::StatusCode::INTERNAL_SERVER_ERROR, e),
    }

    let prompt_log = format!("[chat:{:?}:reply] {message}", session.mode);
    match llm.chat_completion(&llm_messages, None).await {
        Ok(resp) => {
            let answer = resp.content;
            let _ = sqlx::query(
                "INSERT INTO chat_messages (session_id, role, content) VALUES ($1, 'assistant', $2)",
            )
            .bind(session_id)
            .bind(&answer)
            .execute(pool.get_ref())
            .await;
            let _ = sqlx::query("UPDATE chat_sessions SET updated_at = now() WHERE id = $1")
                .bind(session_id)
                .execute(pool.get_ref())
                .await;
            log_ai_interaction(pool.get_ref(), user.user_id, session.mode, &prompt_log, &answer, resp.total_tokens).await;
            HttpResponse::Created().json(ReplyResponse { reply: answer })
        }
        Err(e) => {
            let failure = describe_llm_failure(&e);
            log_ai_interaction(
                pool.get_ref(),
                user.user_id,
                session.mode,
                &prompt_log,
                &failure.placeholder,
                failure.tokens_used,
            )
            .await;
            error_response(actix_web::http::StatusCode::BAD_GATEWAY, failure.message)
        }
    }
}

#[get("/chat/{session_id}")]
pub async fn get_session(
    pool: web::Data<PgPool>,
    user: AuthedUser,
    path: web::Path<Uuid>,
) -> HttpResponse {
    let session_id = path.into_inner();

    let session: Option<SessionRow> = match sqlx::query_as::<_, SessionRow>(
        "SELECT id, mode, set_id, title FROM chat_sessions WHERE id = $1 AND user_id = $2",
    )
    .bind(session_id)
    .bind(user.user_id)
    .fetch_optional(pool.get_ref())
    .await
    {
        Ok(r) => r,
        Err(e) => {
            return error_response(
                actix_web::http::StatusCode::INTERNAL_SERVER_ERROR,
                format!("database error: {e}"),
            );
        }
    };
    let Some(session) = session else {
        return error_response(
            actix_web::http::StatusCode::NOT_FOUND,
            format!("conversation {session_id} not found"),
        );
    };

    match sqlx::query_as::<_, MessageOut>(
        r#"SELECT role, content, created_at FROM chat_messages
           WHERE session_id = $1 ORDER BY created_at"#,
    )
    .bind(session_id)
    .fetch_all(pool.get_ref())
    .await
    {
        Ok(messages) => HttpResponse::Ok().json(SessionResponse {
            session_id: session.id,
            mode: session.mode,
            set_id: session.set_id,
            title: session.title,
            messages,
        }),
        Err(e) => error_response(
            actix_web::http::StatusCode::INTERNAL_SERVER_ERROR,
            format!("database error reading the conversation: {e}"),
        ),
    }
}

const LIST_QUERY: &str = r#"SELECT s.id, s.mode, s.set_id, s.title, s.created_at, s.updated_at,
          (SELECT count(*) FROM chat_messages m WHERE m.session_id = s.id) AS message_count
   FROM chat_sessions s
   WHERE s.user_id = $1
     AND ($2::chat_mode IS NULL OR s.mode = $2::chat_mode)
   ORDER BY s.updated_at DESC
   LIMIT $3"#;

#[get("/chat")]
pub async fn list_sessions(
    pool: web::Data<PgPool>,
    user: AuthedUser,
    query: web::Query<ListQuery>,
) -> HttpResponse {
    let limit = query.limit.unwrap_or(DEFAULT_LIST_LIMIT).clamp(1, MAX_LIST_LIMIT);
    match sqlx::query_as::<_, SessionSummary>(LIST_QUERY)
        .bind(user.user_id)
        .bind(query.mode)
        .bind(limit)
        .fetch_all(pool.get_ref())
        .await
    {
        Ok(sessions) => {
            let count = sessions.len();
            HttpResponse::Ok().json(ListResponse { sessions, count })
        }
        Err(e) => error_response(
            actix_web::http::StatusCode::INTERNAL_SERVER_ERROR,
            format!("database error listing conversations: {e}"),
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::handlers::test_db;

    #[test]
    fn a_title_is_the_opening_message_trimmed() {
        assert_eq!(title_from("  Giải phương trình x² - 5x + 6 = 0  "), "Giải phương trình x² - 5x + 6 = 0");
        let long = "a".repeat(TITLE_CHARS + 20);
        let title = title_from(&long);
        assert_eq!(title.chars().count(), TITLE_CHARS + 1, "truncated plus the ellipsis");
        assert!(title.ends_with('…'));
    }

    #[test]
    fn the_title_counts_characters_not_bytes() {
        // Vietnamese is multi-byte: slicing by bytes would cut a character in
        // half and produce invalid UTF-8 (a panic, in Rust).
        let long = "ề".repeat(TITLE_CHARS + 5);
        let title = title_from(&long);
        assert_eq!(title.chars().count(), TITLE_CHARS + 1);
    }

    #[test]
    fn empty_and_oversized_messages_are_rejected() {
        assert!(validate_message("   ").is_err());
        assert!(validate_message(&"x".repeat(MAX_MESSAGE_CHARS + 1)).is_err());
        assert!(validate_message(" hỏi bài ").is_ok());
    }

    #[test]
    fn the_two_modes_ask_for_opposite_things() {
        let ask = ChatMode::Ask.system_prompt(None);
        let solve = ChatMode::Solve.system_prompt(None);
        // The distinction is the whole feature: one answers, one shows work.
        assert!(ask.contains("direct"), "{ask}");
        assert!(solve.contains("steps"), "{solve}");
        assert!(solve.contains("Final answer"), "{solve}");
        assert_ne!(ask, solve);
        // Neither may behave like the Socratic tutor, which withholds answers.
        assert!(!ask.contains("guiding question"));
    }

    #[test]
    fn study_set_context_is_appended_when_present() {
        let with = ChatMode::Ask.system_prompt(Some("- Từ thông → Φ = B·S·cosα\n"));
        assert!(with.contains("Φ = B·S·cosα"));
        // An empty context must not leave a dangling "here is your material".
        assert_eq!(ChatMode::Ask.system_prompt(Some("   ")), ChatMode::Ask.system_prompt(None));
    }

    #[tokio::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn chat_sessions_are_listed_newest_first_and_only_for_their_owner() {
        let pool = test_db::pool().await;
        let mut tx = pool.begin().await.unwrap();
        let (mine, _) = test_db::seed_learner(&mut tx).await;
        let (theirs, _) = test_db::seed_learner(&mut tx).await;

        for (user, mode, title, ago) in [
            (mine, "ask", "câu hỏi cũ", 2),
            (mine, "solve", "bài mới", 0),
            (theirs, "ask", "của người khác", 1),
        ] {
            sqlx::query(
                "INSERT INTO chat_sessions (user_id, mode, title, updated_at) \
                 VALUES ($1, $2::chat_mode, $3, now() - make_interval(hours => $4::int))",
            )
            .bind(user)
            .bind(mode)
            .bind(title)
            .bind(ago as i32)
            .execute(&mut *tx)
            .await
            .unwrap();
        }

        let rows: Vec<SessionSummary> = sqlx::query_as(LIST_QUERY)
            .bind(mine)
            .bind(None::<ChatMode>)
            .bind(50_i64)
            .fetch_all(&mut *tx)
            .await
            .unwrap();

        assert_eq!(rows.len(), 2, "another learner's conversations must not be listed");
        assert_eq!(rows[0].title, "bài mới", "most recently used first");
        assert_eq!(rows[1].title, "câu hỏi cũ");
    }

    #[tokio::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn listing_can_be_filtered_by_mode() {
        let pool = test_db::pool().await;
        let mut tx = pool.begin().await.unwrap();
        let (user_id, _) = test_db::seed_learner(&mut tx).await;

        for mode in ["ask", "solve"] {
            sqlx::query("INSERT INTO chat_sessions (user_id, mode, title) VALUES ($1, $2::chat_mode, $3)")
                .bind(user_id)
                .bind(mode)
                .bind(format!("{mode} session"))
                .execute(&mut *tx)
                .await
                .unwrap();
        }

        let solves: Vec<SessionSummary> = sqlx::query_as(LIST_QUERY)
            .bind(user_id)
            .bind(Some(ChatMode::Solve))
            .bind(50_i64)
            .fetch_all(&mut *tx)
            .await
            .unwrap();

        assert_eq!(solves.len(), 1);
        assert_eq!(solves[0].mode, ChatMode::Solve);
    }
}
