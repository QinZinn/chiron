//! Editing and deleting — the half of CRUD this API never had.
//!
//! - `PATCH  /me`                  learning style
//! - `PATCH  /study_sets/{id}`     name, topic
//! - `DELETE /study_sets/{id}`     and everything in it
//! - `PATCH  /cards/{id}`          question, answer
//! - `DELETE /cards/{id}`
//! - `DELETE /quiz/{question_id}`
//! - `DELETE /socratic/{id}`, `DELETE /chat/{id}`
//!
//! Until now everything here was append-only, which is a strange shape for a
//! system whose cards are written by an LLM: a wrong question could be
//! generated in one call and then only removed with psql.
//!
//! ## Deletes are real deletes
//!
//! No soft-delete flag. The schema cascades (a study set takes its cards,
//! their reviews and its quiz questions with it), and a learner who asks to
//! remove something should not have it linger invisibly in their own
//! statistics. The cost is that review history goes too — which is why the UI
//! says so before asking.
//!
//! ## PATCH means "change these fields"
//!
//! Absent fields are left alone; `null` is not accepted as "clear it" for
//! anything that is NOT NULL in the schema. `topic` and `learning_style` are
//! nullable, and an explicit `null` there does clear them.

use actix_web::{delete, patch, web, HttpResponse};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::{FromRow, PgPool};
use uuid::Uuid;

use super::error_response;
use crate::auth::AuthedUser;

/// `Option<Option<T>>`: absent (leave alone) vs. present-and-null (clear) vs.
/// present-with-a-value. Serde gives the first two the same shape unless the
/// field is declared like this.
#[derive(Debug, Deserialize, Default)]
pub struct PatchMe {
    #[serde(default, deserialize_with = "double_option")]
    pub learning_style: Option<Option<String>>,
}

#[derive(Debug, Deserialize, Default)]
pub struct PatchStudySet {
    pub name: Option<String>,
    #[serde(default, deserialize_with = "double_option")]
    pub topic: Option<Option<String>>,
}

#[derive(Debug, Deserialize, Default)]
pub struct PatchCard {
    pub question: Option<String>,
    pub answer: Option<String>,
}

fn double_option<'de, D, T>(de: D) -> Result<Option<Option<T>>, D::Error>
where
    D: serde::Deserializer<'de>,
    T: serde::Deserialize<'de>,
{
    Option::<T>::deserialize(de).map(Some)
}

#[derive(Debug, Serialize, FromRow)]
pub struct UserRow {
    pub id: Uuid,
    pub email: String,
    pub learning_style: Option<String>,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Serialize, FromRow)]
pub struct StudySetRow {
    pub id: Uuid,
    pub user_id: Uuid,
    pub name: String,
    pub topic: Option<String>,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Serialize, FromRow)]
pub struct CardRow {
    pub id: Uuid,
    pub set_id: Uuid,
    pub question: String,
    pub answer: String,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Serialize)]
pub struct DeletedResponse {
    pub deleted: bool,
    pub id: Uuid,
}

fn not_found(kind: &str, id: Uuid) -> HttpResponse {
    // Same answer for "does not exist" and "belongs to someone else".
    error_response(actix_web::http::StatusCode::NOT_FOUND, format!("{kind} {id} not found"))
}

fn db_error(e: sqlx::Error) -> HttpResponse {
    error_response(
        actix_web::http::StatusCode::INTERNAL_SERVER_ERROR,
        format!("database error: {e}"),
    )
}

// ---------------------------------------------------------------------------
// Profile
// ---------------------------------------------------------------------------

#[patch("/me")]
pub async fn patch_me(
    pool: web::Data<PgPool>,
    user: AuthedUser,
    body: web::Json<PatchMe>,
) -> HttpResponse {
    let Some(style) = &body.learning_style else {
        // Nothing to change: return the current row rather than pretending an
        // update happened.
        return match sqlx::query_as::<_, UserRow>(
            "SELECT id, email, learning_style, created_at FROM users WHERE id = $1",
        )
        .bind(user.user_id)
        .fetch_optional(pool.get_ref())
        .await
        {
            Ok(Some(row)) => HttpResponse::Ok().json(row),
            Ok(None) => not_found("user", user.user_id),
            Err(e) => db_error(e),
        };
    };

    match sqlx::query_as::<_, UserRow>(
        r#"UPDATE users SET learning_style = $2, updated_at = now()
           WHERE id = $1
           RETURNING id, email, learning_style, created_at"#,
    )
    .bind(user.user_id)
    .bind(style.as_deref())
    .fetch_optional(pool.get_ref())
    .await
    {
        Ok(Some(row)) => HttpResponse::Ok().json(row),
        Ok(None) => not_found("user", user.user_id),
        Err(e) => db_error(e),
    }
}

// ---------------------------------------------------------------------------
// Study sets
// ---------------------------------------------------------------------------

#[patch("/study_sets/{set_id}")]
pub async fn patch_study_set(
    pool: web::Data<PgPool>,
    user: AuthedUser,
    path: web::Path<Uuid>,
    body: web::Json<PatchStudySet>,
) -> HttpResponse {
    let set_id = path.into_inner();
    if let Some(name) = &body.name {
        if name.trim().is_empty() {
            return error_response(
                actix_web::http::StatusCode::BAD_REQUEST,
                "study set name must not be empty",
            );
        }
    }

    // COALESCE leaves a column alone when its parameter is NULL, except for
    // `topic`, where clearing is a real intent — hence the extra flag.
    match sqlx::query_as::<_, StudySetRow>(
        r#"UPDATE study_sets
           SET name  = COALESCE($3, name),
               topic = CASE WHEN $4 THEN $5 ELSE topic END
           WHERE id = $1 AND user_id = $2
           RETURNING id, user_id, name, topic, created_at"#,
    )
    .bind(set_id)
    .bind(user.user_id)
    .bind(body.name.as_deref().map(str::trim))
    .bind(body.topic.is_some())
    .bind(body.topic.clone().flatten())
    .fetch_optional(pool.get_ref())
    .await
    {
        Ok(Some(row)) => HttpResponse::Ok().json(row),
        Ok(None) => not_found("study set", set_id),
        Err(e) => db_error(e),
    }
}

#[delete("/study_sets/{set_id}")]
pub async fn delete_study_set(
    pool: web::Data<PgPool>,
    user: AuthedUser,
    path: web::Path<Uuid>,
) -> HttpResponse {
    let set_id = path.into_inner();
    match sqlx::query("DELETE FROM study_sets WHERE id = $1 AND user_id = $2")
        .bind(set_id)
        .bind(user.user_id)
        .execute(pool.get_ref())
        .await
    {
        Ok(r) if r.rows_affected() > 0 => {
            HttpResponse::Ok().json(DeletedResponse { deleted: true, id: set_id })
        }
        Ok(_) => not_found("study set", set_id),
        Err(e) => db_error(e),
    }
}

// ---------------------------------------------------------------------------
// Cards
// ---------------------------------------------------------------------------

#[patch("/cards/{card_id}")]
pub async fn patch_card(
    pool: web::Data<PgPool>,
    user: AuthedUser,
    path: web::Path<Uuid>,
    body: web::Json<PatchCard>,
) -> HttpResponse {
    let card_id = path.into_inner();
    for (field, value) in [("question", &body.question), ("answer", &body.answer)] {
        if let Some(v) = value {
            if v.trim().is_empty() {
                return error_response(
                    actix_web::http::StatusCode::BAD_REQUEST,
                    format!("{field} must not be empty"),
                );
            }
        }
    }

    // The ownership check is the join, so a card in someone else's set is
    // simply not updated and reads back as "not found".
    match sqlx::query_as::<_, CardRow>(
        r#"UPDATE cards c
           SET question = COALESCE($3, c.question),
               answer   = COALESCE($4, c.answer)
           FROM study_sets s
           WHERE c.id = $1 AND s.id = c.set_id AND s.user_id = $2
           RETURNING c.id, c.set_id, c.question, c.answer, c.created_at"#,
    )
    .bind(card_id)
    .bind(user.user_id)
    .bind(body.question.as_deref().map(str::trim))
    .bind(body.answer.as_deref().map(str::trim))
    .fetch_optional(pool.get_ref())
    .await
    {
        Ok(Some(row)) => HttpResponse::Ok().json(row),
        Ok(None) => not_found("card", card_id),
        Err(e) => db_error(e),
    }
}

#[delete("/cards/{card_id}")]
pub async fn delete_card(
    pool: web::Data<PgPool>,
    user: AuthedUser,
    path: web::Path<Uuid>,
) -> HttpResponse {
    let card_id = path.into_inner();
    match sqlx::query(
        r#"DELETE FROM cards c
           USING study_sets s
           WHERE c.id = $1 AND s.id = c.set_id AND s.user_id = $2"#,
    )
    .bind(card_id)
    .bind(user.user_id)
    .execute(pool.get_ref())
    .await
    {
        Ok(r) if r.rows_affected() > 0 => {
            HttpResponse::Ok().json(DeletedResponse { deleted: true, id: card_id })
        }
        Ok(_) => not_found("card", card_id),
        Err(e) => db_error(e),
    }
}

// ---------------------------------------------------------------------------
// Quiz questions and conversations
// ---------------------------------------------------------------------------

#[delete("/quiz/{question_id}")]
pub async fn delete_quiz_question(
    pool: web::Data<PgPool>,
    user: AuthedUser,
    path: web::Path<Uuid>,
) -> HttpResponse {
    let question_id = path.into_inner();
    match sqlx::query(
        r#"DELETE FROM quiz_questions q
           USING study_sets s
           WHERE q.id = $1 AND s.id = q.set_id AND s.user_id = $2"#,
    )
    .bind(question_id)
    .bind(user.user_id)
    .execute(pool.get_ref())
    .await
    {
        Ok(r) if r.rows_affected() > 0 => {
            HttpResponse::Ok().json(DeletedResponse { deleted: true, id: question_id })
        }
        Ok(_) => not_found("quiz question", question_id),
        Err(e) => db_error(e),
    }
}

#[delete("/socratic/{session_id}")]
pub async fn delete_socratic(
    pool: web::Data<PgPool>,
    user: AuthedUser,
    path: web::Path<Uuid>,
) -> HttpResponse {
    let session_id = path.into_inner();
    // The transcript already shipped to the Knowledge Store stays there: KS
    // owns its own records, and this endpoint does not reach into it.
    match sqlx::query("DELETE FROM socratic_sessions WHERE id = $1 AND user_id = $2")
        .bind(session_id)
        .bind(user.user_id)
        .execute(pool.get_ref())
        .await
    {
        Ok(r) if r.rows_affected() > 0 => {
            HttpResponse::Ok().json(DeletedResponse { deleted: true, id: session_id })
        }
        Ok(_) => not_found("socratic session", session_id),
        Err(e) => db_error(e),
    }
}

#[delete("/chat/{session_id}")]
pub async fn delete_chat(
    pool: web::Data<PgPool>,
    user: AuthedUser,
    path: web::Path<Uuid>,
) -> HttpResponse {
    let session_id = path.into_inner();
    match sqlx::query("DELETE FROM chat_sessions WHERE id = $1 AND user_id = $2")
        .bind(session_id)
        .bind(user.user_id)
        .execute(pool.get_ref())
        .await
    {
        Ok(r) if r.rows_affected() > 0 => {
            HttpResponse::Ok().json(DeletedResponse { deleted: true, id: session_id })
        }
        Ok(_) => not_found("conversation", session_id),
        Err(e) => db_error(e),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::handlers::test_db;

    #[test]
    fn absent_and_null_are_different_things() {
        // {} leaves the field alone; {"topic": null} clears it. Serde collapses
        // the two unless the field is Option<Option<_>>, and collapsing them
        // would make every PATCH silently wipe the topic.
        let absent: PatchStudySet = serde_json::from_str("{}").unwrap();
        assert!(absent.topic.is_none());
        let cleared: PatchStudySet = serde_json::from_str(r#"{"topic": null}"#).unwrap();
        assert_eq!(cleared.topic, Some(None));
        let set: PatchStudySet = serde_json::from_str(r#"{"topic": "Vật lý 11"}"#).unwrap();
        assert_eq!(set.topic, Some(Some("Vật lý 11".to_string())));
    }

    #[tokio::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn a_card_cannot_be_edited_or_deleted_by_another_learner() {
        let pool = test_db::pool().await;
        let mut tx = pool.begin().await.unwrap();
        let (mine, _) = test_db::seed_learner(&mut tx).await;
        let (_theirs, their_set) = test_db::seed_learner(&mut tx).await;
        let their_card = test_db::seed_card(&mut tx, their_set, "not yours").await;

        let updated: Option<CardRow> = sqlx::query_as(
            r#"UPDATE cards c SET question = COALESCE($3, c.question)
               FROM study_sets s
               WHERE c.id = $1 AND s.id = c.set_id AND s.user_id = $2
               RETURNING c.id, c.set_id, c.question, c.answer, c.created_at"#,
        )
        .bind(their_card)
        .bind(mine)
        .bind(Some("hijacked"))
        .fetch_optional(&mut *tx)
        .await
        .unwrap();
        assert!(updated.is_none(), "one learner must not edit another's card");

        let deleted = sqlx::query(
            r#"DELETE FROM cards c USING study_sets s
               WHERE c.id = $1 AND s.id = c.set_id AND s.user_id = $2"#,
        )
        .bind(their_card)
        .bind(mine)
        .execute(&mut *tx)
        .await
        .unwrap();
        assert_eq!(deleted.rows_affected(), 0, "nor delete it");

        let still_there: i64 = sqlx::query_scalar("SELECT count(*) FROM cards WHERE id = $1")
            .bind(their_card)
            .fetch_one(&mut *tx)
            .await
            .unwrap();
        assert_eq!(still_there, 1);
    }

    #[tokio::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn deleting_a_study_set_takes_its_cards_and_their_history() {
        let pool = test_db::pool().await;
        let mut tx = pool.begin().await.unwrap();
        let (user_id, set_id) = test_db::seed_learner(&mut tx).await;
        let card = test_db::seed_card(&mut tx, set_id, "doomed").await;
        test_db::record_review(&mut tx, card, user_id, 1.0, 5.0, Utc::now(), Utc::now()).await;

        sqlx::query("DELETE FROM study_sets WHERE id = $1 AND user_id = $2")
            .bind(set_id)
            .bind(user_id)
            .execute(&mut *tx)
            .await
            .unwrap();

        // The cascade is the feature: a learner who removes a set should not
        // keep its reviews feeding their accuracy figures. Written as two
        // literal queries — sqlx refuses SQL built by format!, and rightly so.
        let cards_left: i64 = sqlx::query_scalar("SELECT count(*) FROM cards WHERE set_id = $1")
            .bind(set_id)
            .fetch_one(&mut *tx)
            .await
            .unwrap();
        let events_left: i64 =
            sqlx::query_scalar("SELECT count(*) FROM learning_events WHERE card_id = $1")
                .bind(card)
                .fetch_one(&mut *tx)
                .await
                .unwrap();
        assert_eq!(cards_left, 0, "cards should have gone with the study set");
        assert_eq!(events_left, 0, "so should their review history");
    }
}
