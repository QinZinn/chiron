//! Handlers for the `cards` table.
//!
//! POST /cards  — create a flashcard in one of the learner's study sets
//! GET  /cards  — list the learner's cards, optionally filtered by ?set_id=<uuid>
//!
//! Both are scoped to the bearer token: a card belongs to a study set, and a
//! study set belongs to a learner.

use actix_web::{HttpResponse, post, get, web};
use serde::{Deserialize, Serialize};
use sqlx::{FromRow, PgPool};
use uuid::Uuid;
use chrono::{DateTime, Utc};

use super::{classify_db_error, error_response};
use crate::auth::{owns_study_set, AuthedUser};

#[derive(Debug, Deserialize)]
pub struct CreateCardRequest {
    pub set_id: Uuid,
    pub question: String,
    pub answer: String,
}

#[derive(Debug, Serialize, FromRow)]
pub struct CardRow {
    pub id: Uuid,
    pub set_id: Uuid,
    pub question: String,
    pub answer: String,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Deserialize)]
pub struct ListCardsQuery {
    pub set_id: Option<Uuid>,
}

#[post("/cards")]
pub async fn create_card(
    pool: web::Data<PgPool>,
    user: AuthedUser,
    body: web::Json<CreateCardRequest>,
) -> HttpResponse {
    if body.question.trim().is_empty() || body.answer.trim().is_empty() {
        return error_response(
            actix_web::http::StatusCode::BAD_REQUEST,
            "question and answer must not be empty",
        );
    }

    // Cards land in someone's study set, so the set has to be this learner's.
    // A set owned by another learner reads as "not found", the same as one
    // that does not exist.
    match owns_study_set(pool.get_ref(), user.user_id, body.set_id).await {
        Ok(true) => {}
        Ok(false) => {
            return error_response(
                actix_web::http::StatusCode::NOT_FOUND,
                format!("study set {} not found", body.set_id),
            );
        }
        Err(e) => {
            return error_response(
                actix_web::http::StatusCode::INTERNAL_SERVER_ERROR,
                format!("database error checking study set ownership: {e}"),
            );
        }
    }

    match sqlx::query_as::<_, CardRow>(
        r#"INSERT INTO cards (set_id, question, answer)
           VALUES ($1, $2, $3)
           RETURNING id, set_id, question, answer, created_at"#,
    )
    .bind(body.set_id)
    .bind(&body.question)
    .bind(&body.answer)
    .fetch_one(pool.get_ref())
    .await
    {
        Ok(row) => HttpResponse::Created().json(row),
        Err(e) => {
            let (status, msg) = classify_db_error(&e);
            error_response(status, msg)
        }
    }
}

#[get("/cards")]
pub async fn list_cards(
    pool: web::Data<PgPool>,
    user: AuthedUser,
    query: web::Query<ListCardsQuery>,
) -> HttpResponse {
    // Joined to study_sets rather than filtered on cards alone: the owner of a
    // card is a property of its set, and this is the only place that decides
    // which cards a learner may see. Asking for another learner's set_id
    // returns an empty list, not their cards.
    let result = match query.set_id {
        Some(sid) => {
            sqlx::query_as::<_, CardRow>(
                r#"SELECT c.id, c.set_id, c.question, c.answer, c.created_at
                   FROM cards c
                   JOIN study_sets s ON s.id = c.set_id
                   WHERE c.set_id = $1 AND s.user_id = $2
                   ORDER BY c.created_at"#,
            )
            .bind(sid)
            .bind(user.user_id)
            .fetch_all(pool.get_ref())
            .await
        }
        None => {
            sqlx::query_as::<_, CardRow>(
                r#"SELECT c.id, c.set_id, c.question, c.answer, c.created_at
                   FROM cards c
                   JOIN study_sets s ON s.id = c.set_id
                   WHERE s.user_id = $1
                   ORDER BY c.created_at"#,
            )
            .bind(user.user_id)
            .fetch_all(pool.get_ref())
            .await
        }
    };

    match result {
        Ok(rows) => HttpResponse::Ok().json(rows),
        Err(e) => error_response(
            actix_web::http::StatusCode::INTERNAL_SERVER_ERROR,
            format!("database error: {e}"),
        ),
    }
}
