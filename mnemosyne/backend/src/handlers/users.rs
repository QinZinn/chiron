//! Handlers for the `users` table.
//!
//! GET  /me    — the learner the bearer token belongs to
//! POST /users — create a user            (operator token, see [`AdminToken`])
//! GET  /users — list all users           (operator token)
//!
//! `/users` is administration, not learning: one learner must not be able to
//! enumerate the others or create accounts, so those two sit behind
//! `MNEMOSYNE_ADMIN_TOKEN` rather than behind a learner token. With that env
//! var unset they refuse everyone and the `create-user` CLI command is the way
//! in — which is the intended setup path anyway, since it also mints the first
//! token.

use actix_web::{HttpResponse, post, get, web};
use serde::{Deserialize, Serialize};
use sqlx::{FromRow, PgPool};
use uuid::Uuid;
use chrono::{DateTime, Utc};

use super::{classify_db_error, error_response};
use crate::auth::{AdminToken, AuthedUser};

#[derive(Debug, Deserialize)]
pub struct CreateUserRequest {
    pub email: String,
    pub learning_style: Option<String>,
}

#[derive(Debug, Serialize, FromRow)]
pub struct UserRow {
    pub id: Uuid,
    pub email: String,
    pub learning_style: Option<String>,
    pub created_at: DateTime<Utc>,
    // intentionally omitting updated_at from the response — we never update
    // learning_style in this prompt, so created_at is the meaningful timestamp.
}

/// Who am I? The frontend calls this on startup: with per-learner tokens there
/// is no user picker any more, so the token itself answers the question.
#[get("/me")]
pub async fn me(pool: web::Data<PgPool>, user: AuthedUser) -> HttpResponse {
    match sqlx::query_as::<_, UserRow>(
        "SELECT id, email, learning_style, created_at FROM users WHERE id = $1",
    )
    .bind(user.user_id)
    .fetch_optional(pool.get_ref())
    .await
    {
        // A valid token whose user is gone means the row was deleted between
        // minting and now; the token cascades away with it, so this is close to
        // unreachable — reported honestly rather than unwrapped.
        Ok(Some(row)) => HttpResponse::Ok().json(row),
        Ok(None) => error_response(
            actix_web::http::StatusCode::NOT_FOUND,
            "the token's user no longer exists",
        ),
        Err(e) => error_response(
            actix_web::http::StatusCode::INTERNAL_SERVER_ERROR,
            format!("database error: {e}"),
        ),
    }
}

#[post("/users")]
pub async fn create_user(
    pool: web::Data<PgPool>,
    _admin: AdminToken,
    body: web::Json<CreateUserRequest>,
) -> HttpResponse {
    let email = body.email.trim();
    if email.is_empty() {
        return error_response(
            actix_web::http::StatusCode::BAD_REQUEST,
            "email must not be empty",
        );
    }

    match sqlx::query_as::<_, UserRow>(
        r#"INSERT INTO users (email, learning_style)
           VALUES ($1, $2)
           RETURNING id, email, learning_style, created_at"#,
    )
    .bind(email)
    .bind(&body.learning_style)
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

#[get("/users")]
pub async fn list_users(pool: web::Data<PgPool>, _admin: AdminToken) -> HttpResponse {
    match sqlx::query_as::<_, UserRow>(
        "SELECT id, email, learning_style, created_at FROM users ORDER BY created_at",
    )
    .fetch_all(pool.get_ref())
    .await
    {
        Ok(rows) => HttpResponse::Ok().json(rows),
        Err(e) => error_response(
            actix_web::http::StatusCode::INTERNAL_SERVER_ERROR,
            format!("database error: {e}"),
        ),
    }
}
