//! Blurting (brain dump) endpoints.
//!
//! - `POST /study_sets/{set_id}/blurting`          — submit what you remember, get a card-by-card verdict
//! - `GET  /study_sets/{set_id}/blurting/history`  — the learner's past attempts on that set
//!
//! The learner writes down everything they remember of a study set without
//! looking. The AI compares that with the set's cards and says which cards
//! were remembered and which were got wrong; every other card it was shown is
//! missing. Built on the same pattern as `/feynman_evaluate`: the same card
//! context and cap, the same fence-tolerant JSON parse, the same length limit
//! and failure reporting (a truncated completion is `LLMError::Truncated`,
//! never parsed).
//!
//! ## Card identity
//!
//! The model never sees or returns a `card_id`. Each card in the prompt is
//! labelled `c1`, `c2`, … and the model answers with those labels, which the
//! server maps back to the real ids. A label that is not one of this prompt's
//! cards — a typo, an invented `c99`, a UUID copied from somewhere — is dropped
//! and logged, and nothing is written for it. So every `card_id` stored or
//! returned belongs to this study set, by construction, and the foreign key in
//! `blurting_attempt_cards` backs that up.

use actix_web::{get, post, web, HttpResponse};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::{FromRow, PgPool};
use uuid::Uuid;

use super::{describe_llm_failure, error_response};
use crate::auth::AuthedUser;
use crate::llm_provider::{LLMMessage, LLMProvider};

/// Cap on card content in the prompt — the same budget `/feynman_evaluate` uses.
const MAX_CARD_CONTEXT_CHARS: usize = 6000;

/// Cap on what the learner writes, as for a Feynman explanation.
const MAX_RECALL_CHARS: usize = 4000;

// ---------------------------------------------------------------------------
// Request / response types
// ---------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
pub struct BlurtingRequest {
    pub recall_text: String,
}

#[derive(Debug, Serialize, PartialEq)]
pub struct CardVerdict {
    pub card_id: Uuid,
    pub question: String,
    pub answer: String,
    /// What was got wrong; empty unless the verdict is `wrong`.
    pub note: String,
}

#[derive(Debug, Serialize)]
pub struct BlurtingResponse {
    pub attempt_id: Uuid,
    pub feedback: String,
    pub remembered: Vec<CardVerdict>,
    pub missing: Vec<CardVerdict>,
    pub wrong: Vec<CardVerdict>,
    /// Cards the AI was shown. Below `cards_total` when the set is larger than
    /// the prompt's card budget: the rest were not judged at all.
    pub cards_considered: usize,
    pub cards_total: usize,
    /// Labels the model returned that were not this prompt's cards.
    pub dropped_labels: usize,
}

#[derive(Debug, Serialize, FromRow)]
pub struct HistoryEntry {
    pub id: Uuid,
    pub recall_text: String,
    pub feedback: String,
    pub cards_considered: i32,
    pub cards_total: i32,
    pub remembered: i64,
    pub missing: i64,
    pub wrong: i64,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Serialize)]
pub struct HistoryResponse {
    pub attempts: Vec<HistoryEntry>,
    pub count: usize,
}

// ---------------------------------------------------------------------------
// Cards and prompt
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, FromRow, PartialEq)]
pub struct CardRow {
    pub id: Uuid,
    pub question: String,
    pub answer: String,
}

/// The label a card has in the prompt.
fn label(i: usize) -> String {
    format!("c{}", i + 1)
}

/// The cards that fit the budget, in order, and the context text for them.
/// Whole cards only, as in `/feynman_evaluate`: a card cut in half would be
/// judged on half its content.
fn build_context(cards: &[CardRow]) -> (usize, String) {
    let mut context = String::new();
    let mut considered = 0;
    for (i, card) in cards.iter().enumerate() {
        let entry = format!("[{}]\nQ: {}\nA: {}\n\n", label(i), card.question, card.answer);
        if context.len() + entry.len() > MAX_CARD_CONTEXT_CHARS {
            break;
        }
        context.push_str(&entry);
        considered += 1;
    }
    (considered, context)
}

fn build_system_prompt(card_context: &str) -> String {
    format!(
        "You are grading a \"blurting\" exercise: the student has just written down \
         EVERYTHING they remember about a study set WITHOUT looking at the material. \
         Compare what they wrote with each card below. Each card has a label in square \
         brackets, e.g. [c1].\n\
         \n\
         CARDS:\n\
         {card_context}\n\
         For each card, decide:\n\
         - \"remembered\": the text states the key idea of the answer correctly (it need \
         not match word for word; different phrasing still counts).\n\
         - \"wrong\": the text mentions the card's content but gets it WRONG (wrong \
         figures, confused concepts, a reversed relationship). Add a \"note\": one English \
         sentence saying exactly what is wrong.\n\
         - A card that is not mentioned, or mentioned too vaguely to tell it was \
         remembered, is NOT listed — the system counts it as missing.\n\
         Use only the labels of the cards above. Do not invent concept names or add cards.\n\
         \n\
         \"feedback\": 1-3 English sentences addressed directly to the student — what \
         they remembered and where the gaps are. Do not over-praise.\n\
         \n\
         Return only valid JSON, no markdown, no explanation outside the JSON:\n\
         {{\"remembered\": [\"c1\"], \"wrong\": [{{\"card\": \"c2\", \"note\": \"...\"}}], \
         \"feedback\": \"...\"}}"
    )
}

// ---------------------------------------------------------------------------
// Parsing and validating the model's answer
// ---------------------------------------------------------------------------

#[derive(Debug, Deserialize, PartialEq)]
struct WrongItem {
    card: String,
    #[serde(default)]
    note: String,
}

#[derive(Debug, Deserialize, PartialEq)]
struct BlurtingAIResponse {
    #[serde(default)]
    remembered: Vec<String>,
    #[serde(default)]
    wrong: Vec<WrongItem>,
    feedback: String,
}

/// Defensive parse: try raw, then strip a leading ```json or ``` fence — the
/// same tolerance `/feynman_evaluate` has.
fn parse_response(raw: &str) -> Result<BlurtingAIResponse, String> {
    if let Ok(v) = serde_json::from_str::<BlurtingAIResponse>(raw) {
        return Ok(v);
    }
    let trimmed = raw.trim();
    let stripped = if trimmed.starts_with("```") {
        let after = trimmed
            .strip_prefix("```json")
            .or_else(|| trimmed.strip_prefix("```"))
            .unwrap_or(trimmed);
        after.strip_suffix("```").unwrap_or(after).trim()
    } else {
        trimmed
    };
    serde_json::from_str::<BlurtingAIResponse>(stripped).map_err(|e| format!("{e} (after stripping fences)"))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Verdict {
    Remembered,
    Missing,
    Wrong,
}

impl Verdict {
    fn as_str(self) -> &'static str {
        match self {
            Verdict::Remembered => "remembered",
            Verdict::Missing => "missing",
            Verdict::Wrong => "wrong",
        }
    }
}

/// The verdict for each considered card, in card order, plus the labels that
/// were thrown away. Pure, so the whole validation rule is testable.
///
/// - A label must be one of `c1..c{considered}`; anything else is dropped.
///   Labels are matched after trimming and lower-casing, so `C3 ` is `c3`.
/// - A card listed as both remembered and wrong counts as wrong: the learner
///   said something false about it, and that is the part worth showing.
/// - Every considered card the model did not list is missing.
fn resolve(parsed: &BlurtingAIResponse, considered: usize) -> (Vec<(Verdict, String)>, Vec<String>) {
    let index_of = |raw: &str| -> Option<usize> {
        let l = raw.trim().to_lowercase();
        let n: usize = l.strip_prefix('c')?.parse().ok()?;
        (1..=considered).contains(&n).then(|| n - 1)
    };
    let mut verdicts: Vec<(Verdict, String)> = vec![(Verdict::Missing, String::new()); considered];
    let mut dropped = Vec::new();
    for raw in &parsed.remembered {
        match index_of(raw) {
            Some(i) if verdicts[i].0 == Verdict::Missing => verdicts[i].0 = Verdict::Remembered,
            Some(_) => {}
            None => dropped.push(raw.clone()),
        }
    }
    for item in &parsed.wrong {
        match index_of(&item.card) {
            Some(i) => verdicts[i] = (Verdict::Wrong, item.note.trim().to_string()),
            None => dropped.push(item.card.clone()),
        }
    }
    (verdicts, dropped)
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

#[post("/study_sets/{set_id}/blurting")]
pub async fn evaluate(
    pool: web::Data<PgPool>,
    llm: web::Data<Box<dyn LLMProvider>>,
    user: AuthedUser,
    path: web::Path<Uuid>,
    body: web::Json<BlurtingRequest>,
) -> HttpResponse {
    let set_id = path.into_inner();

    // 1. The set must exist and be this learner's; another learner's set is
    //    indistinguishable from a missing one.
    match sqlx::query_scalar::<_, bool>("SELECT EXISTS(SELECT 1 FROM study_sets WHERE id = $1 AND user_id = $2)")
        .bind(set_id)
        .bind(user.user_id)
        .fetch_one(pool.get_ref())
        .await
    {
        Ok(true) => {}
        Ok(false) => {
            return error_response(actix_web::http::StatusCode::NOT_FOUND, format!("study set {set_id} not found"))
        }
        Err(e) => {
            return error_response(actix_web::http::StatusCode::INTERNAL_SERVER_ERROR, format!("database error: {e}"))
        }
    }

    // 2. Validate the text.
    let recall = body.recall_text.trim();
    if recall.is_empty() {
        return error_response(actix_web::http::StatusCode::BAD_REQUEST, "recall_text must not be empty");
    }
    if body.recall_text.chars().count() > MAX_RECALL_CHARS {
        return error_response(
            actix_web::http::StatusCode::BAD_REQUEST,
            format!("recall_text exceeds {MAX_RECALL_CHARS} character limit"),
        );
    }

    // 3. The set's cards, in a fixed order so labels are stable.
    let cards: Vec<CardRow> = match sqlx::query_as(
        "SELECT id, question, answer FROM cards WHERE set_id = $1 ORDER BY created_at, id",
    )
    .bind(set_id)
    .fetch_all(pool.get_ref())
    .await
    {
        Ok(c) => c,
        Err(e) => {
            return error_response(
                actix_web::http::StatusCode::INTERNAL_SERVER_ERROR,
                format!("database error fetching cards: {e}"),
            )
        }
    };
    if cards.is_empty() {
        return error_response(
            actix_web::http::StatusCode::BAD_REQUEST,
            "study set has no cards — nothing to compare against, generate some cards first",
        );
    }
    let (considered, context) = build_context(&cards);

    // 4. Ask the model.
    let system_prompt = build_system_prompt(&context);
    let user_prompt = format!("The student's text:\n\n{recall}");
    let messages = vec![LLMMessage::system(system_prompt.clone()), LLMMessage::user(user_prompt.clone())];
    let prompt_log = format!("[blurting:evaluate]\n[system] {system_prompt}\n[user] {user_prompt}");

    let resp = match llm.chat_completion(&messages, None).await {
        Ok(r) => r,
        Err(err) => {
            let failure = describe_llm_failure(&err);
            let _ = log_ai_interaction(pool.get_ref(), user.user_id, &prompt_log, &failure.placeholder, failure.tokens_used)
                .await;
            return error_response(actix_web::http::StatusCode::BAD_GATEWAY, failure.message);
        }
    };
    let _ = log_ai_interaction(pool.get_ref(), user.user_id, &prompt_log, &resp.content, resp.total_tokens).await;

    // 5. Parse and validate.
    let parsed = match parse_response(&resp.content) {
        Ok(p) => p,
        Err(e) => {
            return error_response(
                actix_web::http::StatusCode::BAD_GATEWAY,
                format!("DeepSeek returned non-JSON output, parse failed: {e}"),
            )
        }
    };
    let (verdicts, dropped) = resolve(&parsed, considered);
    if !dropped.is_empty() {
        eprintln!(
            "[blurting] set {set_id}: dropped {} label(s) that are not cards of this prompt (c1..c{considered}): {:?}",
            dropped.len(),
            dropped
        );
    }

    // 6. Store the attempt and every considered card's verdict together.
    let stored: Result<Uuid, sqlx::Error> = async {
        let mut tx = pool.begin().await?;
        let attempt_id: Uuid = sqlx::query_scalar(
            r#"INSERT INTO blurting_attempts (user_id, set_id, recall_text, feedback, cards_considered, cards_total)
               VALUES ($1, $2, $3, $4, $5, $6) RETURNING id"#,
        )
        .bind(user.user_id)
        .bind(set_id)
        .bind(&body.recall_text)
        .bind(parsed.feedback.trim())
        .bind(considered as i32)
        .bind(cards.len() as i32)
        .fetch_one(&mut *tx)
        .await?;
        for (card, (verdict, note)) in cards.iter().zip(&verdicts) {
            sqlx::query(
                "INSERT INTO blurting_attempt_cards (attempt_id, card_id, verdict, note) VALUES ($1, $2, $3, $4)",
            )
            .bind(attempt_id)
            .bind(card.id)
            .bind(verdict.as_str())
            .bind(note)
            .execute(&mut *tx)
            .await?;
        }
        tx.commit().await?;
        Ok(attempt_id)
    }
    .await;
    let attempt_id = match stored {
        Ok(id) => id,
        Err(e) => {
            return error_response(
                actix_web::http::StatusCode::INTERNAL_SERVER_ERROR,
                format!("failed to persist blurting attempt: {e}"),
            )
        }
    };

    // 7. Respond, grouped by verdict.
    let mut out = BlurtingResponse {
        attempt_id,
        feedback: parsed.feedback.trim().to_string(),
        remembered: vec![],
        missing: vec![],
        wrong: vec![],
        cards_considered: considered,
        cards_total: cards.len(),
        dropped_labels: dropped.len(),
    };
    for (card, (verdict, note)) in cards.iter().zip(verdicts) {
        let v = CardVerdict { card_id: card.id, question: card.question.clone(), answer: card.answer.clone(), note };
        match verdict {
            Verdict::Remembered => out.remembered.push(v),
            Verdict::Missing => out.missing.push(v),
            Verdict::Wrong => out.wrong.push(v),
        }
    }
    HttpResponse::Created().json(out)
}

#[get("/study_sets/{set_id}/blurting/history")]
pub async fn history(pool: web::Data<PgPool>, user: AuthedUser, path: web::Path<Uuid>) -> HttpResponse {
    let set_id = path.into_inner();
    match sqlx::query_as::<_, HistoryEntry>(
        r#"SELECT a.id, a.recall_text, a.feedback, a.cards_considered, a.cards_total,
                  count(*) FILTER (WHERE c.verdict = 'remembered') AS remembered,
                  count(*) FILTER (WHERE c.verdict = 'missing') AS missing,
                  count(*) FILTER (WHERE c.verdict = 'wrong') AS wrong,
                  a.created_at
           FROM blurting_attempts a
           LEFT JOIN blurting_attempt_cards c ON c.attempt_id = a.id
           WHERE a.set_id = $1 AND a.user_id = $2
           GROUP BY a.id
           ORDER BY a.created_at"#,
    )
    .bind(set_id)
    .bind(user.user_id)
    .fetch_all(pool.get_ref())
    .await
    {
        Ok(attempts) => {
            let count = attempts.len();
            HttpResponse::Ok().json(HistoryResponse { attempts, count })
        }
        Err(e) => error_response(actix_web::http::StatusCode::INTERNAL_SERVER_ERROR, format!("database error: {e}")),
    }
}

async fn log_ai_interaction(
    pool: &PgPool,
    user_id: Uuid,
    input_text: &str,
    output_text: &str,
    tokens_used: u32,
) -> Result<(), sqlx::Error> {
    sqlx::query(
        r#"INSERT INTO ai_interactions (user_id, interaction_type, input_text, output_text, tokens_used)
           VALUES ($1, 'blurting_evaluation', $2, $3, $4)"#,
    )
    .bind(user_id)
    .bind(input_text)
    .bind(output_text)
    .bind(tokens_used as i32)
    .execute(pool)
    .await
    .map(|_| ())
}


#[cfg(test)]
mod tests {
    use super::*;
    use crate::handlers::test_db;
    use crate::llm_provider::{LLMError, LLMResponse};

    fn card(n: usize, answer_len: usize) -> CardRow {
        CardRow { id: Uuid::from_u128(n as u128), question: format!("q{n}"), answer: "a".repeat(answer_len) }
    }

    fn ai(remembered: &[&str], wrong: &[(&str, &str)]) -> BlurtingAIResponse {
        BlurtingAIResponse {
            remembered: remembered.iter().map(|s| s.to_string()).collect(),
            wrong: wrong.iter().map(|(c, n)| WrongItem { card: c.to_string(), note: n.to_string() }).collect(),
            feedback: "ok".into(),
        }
    }

    // -- pure -----------------------------------------------------------------

    #[test]
    fn context_labels_cards_in_order_and_stops_at_whole_cards() {
        let (n, ctx) = build_context(&[card(1, 3), card(2, 3)]);
        assert_eq!(n, 2);
        assert!(ctx.starts_with("[c1]\nQ: q1\nA: aaa\n\n[c2]\nQ: q2\n"), "{ctx}");

        // A card that would overflow the budget is left out whole, as are
        // the ones after it — the prompt never holds half a card.
        let big = card(2, MAX_CARD_CONTEXT_CHARS);
        let (n, ctx) = build_context(&[card(1, 10), big, card(3, 10)]);
        assert_eq!(n, 1);
        assert!(!ctx.contains("[c2]") && !ctx.contains("[c3]"));
    }

    #[test]
    fn parse_accepts_fenced_json_and_missing_lists() {
        let fenced = "```json\n{\"remembered\": [\"c1\"], \"feedback\": \"tốt\"}\n```";
        let p = parse_response(fenced).unwrap();
        assert_eq!(p.remembered, vec!["c1"]);
        assert!(p.wrong.is_empty());
        assert!(parse_response("not json").is_err());
        assert!(parse_response("{\"remembered\": []}").is_err(), "feedback is required");
    }

    #[test]
    fn unlisted_cards_are_missing_and_listed_ones_keep_their_verdict() {
        let (v, dropped) = resolve(&ai(&["c1"], &[("c3", "nhầm N với P")]), 4);
        assert!(dropped.is_empty());
        let kinds: Vec<Verdict> = v.iter().map(|x| x.0).collect();
        assert_eq!(kinds, vec![Verdict::Remembered, Verdict::Missing, Verdict::Wrong, Verdict::Missing]);
        assert_eq!(v[2].1, "nhầm N với P");
    }

    #[test]
    fn labels_that_are_not_this_prompts_cards_are_dropped() {
        let foreign = Uuid::from_u128(999).to_string();
        let (v, dropped) =
            resolve(&ai(&["c1", "c9", "c0", "x2", &foreign, ""], &[("c5", "?"), ("cc", "?")]), 2);
        assert_eq!(dropped, vec!["c9", "c0", "x2", foreign.as_str(), "", "c5", "cc"]);
        assert_eq!(v.iter().map(|x| x.0).collect::<Vec<_>>(), vec![Verdict::Remembered, Verdict::Missing]);
    }

    #[test]
    fn labels_are_matched_loosely_and_wrong_beats_remembered() {
        let (v, dropped) = resolve(&ai(&[" C1 ", "c2", "c2"], &[("c2", "sai đơn vị")]), 2);
        assert!(dropped.is_empty());
        assert_eq!(v[0].0, Verdict::Remembered);
        assert_eq!(v[1], (Verdict::Wrong, "sai đơn vị".to_string()));
    }

    // -- through the handler, against the real database ------------------------

    struct FakeProvider {
        outcome: fn() -> Result<LLMResponse, LLMError>,
    }

    #[async_trait::async_trait]
    impl LLMProvider for FakeProvider {
        async fn chat_completion(&self, _: &[LLMMessage], _: Option<&str>) -> Result<LLMResponse, LLMError> {
            (self.outcome)()
        }
    }

    fn reply(content: &'static str) -> Result<LLMResponse, LLMError> {
        Ok(LLMResponse { content: content.into(), total_tokens: 321, finish_reason: "stop".into() })
    }

    /// Commits (the handler takes its own connections) and deletes its learners.
    async fn run(
        outcome: fn() -> Result<LLMResponse, LLMError>,
    ) -> (u16, serde_json::Value, Vec<(Uuid, String, String)>, Vec<Uuid>, Uuid) {
        use actix_web::{test, App};
        let pool = test_db::pool().await;
        let mut conn = pool.acquire().await.unwrap();
        let (user, set) = test_db::seed_learner(&mut conn).await;
        let (other, other_set) = test_db::seed_learner(&mut conn).await;
        let mut ids = Vec::new();
        for q in ["Phân đa lượng gồm?", "Đơn vị hàm lượng?", "Phân vi lượng gồm?"] {
            ids.push(test_db::seed_card(&mut conn, set, q).await);
        }
        let foreign = test_db::seed_card(&mut conn, other_set, "thẻ của người khác").await;
        drop(conn);
        let token = crate::auth::mint_token(&pool, user, Some("test")).await.unwrap();
        let provider: Box<dyn LLMProvider> = Box::new(FakeProvider { outcome });
        let app = test::init_service(
            App::new()
                .app_data(web::Data::new(pool.clone()))
                .app_data(web::Data::new(provider))
                .service(evaluate)
                .service(history),
        )
        .await;
        let req = test::TestRequest::post()
            .uri(&format!("/study_sets/{set}/blurting"))
            .insert_header(("Authorization", format!("Bearer {token}")))
            .set_json(serde_json::json!({ "recall_text": "Đa lượng là N, P, K. Đơn vị là mg/l." }))
            .to_request();
        let resp = test::call_service(&app, req).await;
        let status = resp.status().as_u16();
        let body: serde_json::Value = test::read_body_json(resp).await;
        let stored: Vec<(Uuid, String, String)> = sqlx::query_as(
            "SELECT c.card_id, c.verdict, c.note FROM blurting_attempt_cards c \
             JOIN blurting_attempts a ON a.id = c.attempt_id WHERE a.set_id = $1 ORDER BY c.card_id",
        )
        .bind(set)
        .fetch_all(&pool)
        .await
        .unwrap();
        for u in [user, other] {
            sqlx::query("DELETE FROM users WHERE id = $1").bind(u).execute(&pool).await.unwrap();
        }
        (status, body, stored, ids, foreign)
    }

    #[actix_web::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn blurting_stores_one_verdict_per_card_and_drops_foreign_labels() {
        // c1 remembered, c2 wrong, c3 unlisted → missing; "c7" and a raw UUID
        // are not cards of this prompt and must vanish without a trace.
        let (status, body, stored, ids, foreign) = run(|| {
            reply(
                r#"{"remembered": ["c1", "c7", "00000000-0000-0000-0000-000000000abc"],
                    "wrong": [{"card": "c2", "note": "Vở ghi mg/kg, không phải mg/l."}],
                    "feedback": "Nhớ đúng nhóm đa lượng, sai đơn vị, quên vi lượng."}"#,
            )
        })
        .await;
        assert_eq!(status, 201, "{body}");
        assert_eq!(body["remembered"][0]["card_id"], serde_json::json!(ids[0]));
        assert_eq!(body["wrong"][0]["card_id"], serde_json::json!(ids[1]));
        assert_eq!(body["wrong"][0]["note"], "Vở ghi mg/kg, không phải mg/l.");
        assert_eq!(body["missing"][0]["card_id"], serde_json::json!(ids[2]));
        assert_eq!((body["cards_considered"].as_u64(), body["cards_total"].as_u64()), (Some(3), Some(3)));
        assert_eq!(body["dropped_labels"], 2);

        let mut expected = vec![
            (ids[0], "remembered".to_string(), String::new()),
            (ids[1], "wrong".to_string(), "Vở ghi mg/kg, không phải mg/l.".to_string()),
            (ids[2], "missing".to_string(), String::new()),
        ];
        expected.sort();
        assert_eq!(stored, expected);
        assert!(stored.iter().all(|(id, ..)| *id != foreign));
    }

    #[actix_web::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn blurting_a_truncated_or_unparsable_reply_stores_nothing() {
        let (status, body, stored, ..) =
            run(|| Err(LLMError::Truncated { finish_reason: "length".into(), total_tokens: 4096 })).await;
        assert_eq!(status, 502);
        assert!(body["error"].as_str().unwrap().contains("token limit"), "{body}");
        assert!(stored.is_empty(), "a truncated reply must not produce verdicts");

        let (status, _, stored, ..) = run(|| reply("Học sinh nhớ khá tốt.")).await;
        assert_eq!(status, 502);
        assert!(stored.is_empty());
    }
}
