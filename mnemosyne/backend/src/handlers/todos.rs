//! The learner's review todo list.
//!
//! GET  /todos[?done=true|false]  — the learner's items, open ones first
//! POST /todos                    — add an item by hand
//! POST /todos/{id}/complete      — tick an item off
//!
//! Items come from two places: [`crate::weak_cards`] opens one `weak_card`
//! item per struggling study set on its own, and the learner adds `manual`
//! ones here. Nothing schedules them; the learner decides when.
//!
//! Every route scopes to the bearer token. There is no `user_id` parameter:
//! whose list it is comes from the token, as for every other learner route.

use actix_web::{get, post, web, HttpResponse};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::{FromRow, PgPool};
use uuid::Uuid;

use super::{classify_db_error, error_response};
use crate::auth::AuthedUser;

/// Longer than any real todo title; stops the list being used as a notebook.
pub const MAX_TITLE_CHARS: usize = 200;

#[derive(Debug, Deserialize)]
pub struct TodoQuery {
    /// Absent: every item. `false`: still to do. `true`: done.
    pub done: Option<bool>,
}

#[derive(Debug, Deserialize)]
pub struct CreateTodo {
    pub title: String,
    /// Optional for a manual item: "review chapter 3" can belong to a set or not.
    pub study_set_id: Option<Uuid>,
}

#[derive(Debug, Serialize, FromRow, PartialEq)]
pub struct TodoOut {
    pub id: Uuid,
    pub title: String,
    /// `weak_card` or `manual`.
    pub source: String,
    pub study_set_id: Option<Uuid>,
    pub study_set_name: Option<String>,
    pub done: bool,
    pub created_at: DateTime<Utc>,
    pub done_at: Option<DateTime<Utc>>,
    pub last_weak_card_at: Option<DateTime<Utc>>,
    /// Cards listed on a weak-card item; 0 for a manual one.
    pub card_count: i64,
}

#[derive(Debug, Serialize)]
pub struct TodoList {
    pub todos: Vec<TodoOut>,
    pub open_count: usize,
}

const BY_ID_QUERY: &str = r#"SELECT t.id, t.title, t.source, t.study_set_id, s.name AS study_set_name,
          t.done, t.created_at, t.done_at, t.last_weak_card_at,
          (SELECT count(*) FROM todo_item_cards tc WHERE tc.todo_id = t.id) AS card_count
   FROM todo_items t
   LEFT JOIN study_sets s ON s.id = t.study_set_id
   WHERE t.id = $1"#;

/// Open items first, the most recent activity first within each group.
const LIST_QUERY: &str = r#"SELECT t.id, t.title, t.source, t.study_set_id, s.name AS study_set_name,
          t.done, t.created_at, t.done_at, t.last_weak_card_at,
          (SELECT count(*) FROM todo_item_cards tc WHERE tc.todo_id = t.id) AS card_count
   FROM todo_items t
   LEFT JOIN study_sets s ON s.id = t.study_set_id
   WHERE t.user_id = $1
     AND ($2::bool IS NULL OR t.done = $2)
   ORDER BY t.done,
            COALESCE(t.done_at, t.last_weak_card_at, t.created_at) DESC,
            t.id"#;

/// The set must be the caller's own: a manual item cannot point into someone
/// else's study set. Yields no row when it is not, so nothing is inserted.
const INSERT_QUERY: &str = r#"WITH new AS (
     INSERT INTO todo_items (user_id, study_set_id, title, source)
     SELECT $1, $2, $3, 'manual'
     WHERE $2::uuid IS NULL
        OR EXISTS (SELECT 1 FROM study_sets WHERE id = $2 AND user_id = $1)
     RETURNING *
   )
   SELECT n.id, n.title, n.source, n.study_set_id, s.name AS study_set_name,
          n.done, n.created_at, n.done_at, n.last_weak_card_at, 0::bigint AS card_count
   FROM new n
   LEFT JOIN study_sets s ON s.id = n.study_set_id"#;

/// Idempotent: completing a done item keeps its first `done_at`.
const COMPLETE_QUERY: &str = r#"UPDATE todo_items
   SET done = true, done_at = COALESCE(done_at, now())
   WHERE id = $1 AND user_id = $2
   RETURNING id"#;

fn not_found(id: Uuid) -> HttpResponse {
    error_response(actix_web::http::StatusCode::NOT_FOUND, format!("todo item {id} not found"))
}

#[get("/todos")]
pub async fn list_todos(
    pool: web::Data<PgPool>,
    user: AuthedUser,
    query: web::Query<TodoQuery>,
) -> HttpResponse {
    match sqlx::query_as::<_, TodoOut>(LIST_QUERY)
        .bind(user.user_id)
        .bind(query.done)
        .fetch_all(pool.get_ref())
        .await
    {
        Ok(todos) => {
            let open_count = todos.iter().filter(|t| !t.done).count();
            HttpResponse::Ok().json(TodoList { todos, open_count })
        }
        Err(e) => error_response(
            actix_web::http::StatusCode::INTERNAL_SERVER_ERROR,
            format!("database error listing todo items: {e}"),
        ),
    }
}

#[post("/todos")]
pub async fn create_todo(
    pool: web::Data<PgPool>,
    user: AuthedUser,
    body: web::Json<CreateTodo>,
) -> HttpResponse {
    let title = body.title.trim();
    if title.is_empty() {
        return error_response(actix_web::http::StatusCode::BAD_REQUEST, "title must not be empty");
    }
    if title.chars().count() > MAX_TITLE_CHARS {
        return error_response(
            actix_web::http::StatusCode::BAD_REQUEST,
            format!("title must be at most {MAX_TITLE_CHARS} characters"),
        );
    }

    match sqlx::query_as::<_, TodoOut>(INSERT_QUERY)
        .bind(user.user_id)
        .bind(body.study_set_id)
        .bind(title)
        .fetch_optional(pool.get_ref())
        .await
    {
        Ok(Some(todo)) => HttpResponse::Created().json(todo),
        // Only reachable with a study_set_id that is not the caller's.
        Ok(None) => error_response(
            actix_web::http::StatusCode::NOT_FOUND,
            format!("study set {} not found", body.study_set_id.unwrap_or_default()),
        ),
        Err(e) => {
            let (status, msg) = classify_db_error(&e);
            error_response(status, msg)
        }
    }
}

#[post("/todos/{id}/complete")]
pub async fn complete_todo(
    pool: web::Data<PgPool>,
    user: AuthedUser,
    path: web::Path<Uuid>,
) -> HttpResponse {
    let id = path.into_inner();
    let updated: Option<Uuid> = match sqlx::query_scalar(COMPLETE_QUERY)
        .bind(id)
        .bind(user.user_id)
        .fetch_optional(pool.get_ref())
        .await
    {
        Ok(r) => r,
        Err(e) => {
            let (status, msg) = classify_db_error(&e);
            return error_response(status, msg);
        }
    };
    if updated.is_none() {
        return not_found(id);
    }
    match sqlx::query_as::<_, TodoOut>(BY_ID_QUERY)
        .bind(id)
        .fetch_one(pool.get_ref())
        .await
    {
        Ok(todo) => HttpResponse::Ok().json(todo),
        Err(e) => error_response(
            actix_web::http::StatusCode::INTERNAL_SERVER_ERROR,
            format!("database error reading todo item: {e}"),
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::handlers::test_db;
    use actix_web::{test, App};

    macro_rules! app {
        ($pool:expr) => {
            test::init_service(
                App::new()
                    .app_data(web::Data::new($pool.clone()))
                    .service(list_todos)
                    .service(create_todo)
                    .service(complete_todo),
            )
            .await
        };
    }

    fn post(uri: &str, token: &str, body: serde_json::Value) -> test::TestRequest {
        test::TestRequest::post()
            .uri(uri)
            .insert_header(("Authorization", format!("Bearer {token}")))
            .set_json(body)
    }

    fn get(uri: &str, token: &str) -> test::TestRequest {
        test::TestRequest::get()
            .uri(uri)
            .insert_header(("Authorization", format!("Bearer {token}")))
    }

    /// Through actix with real tokens: add by hand, list, tick off, filter.
    /// Commits (the handlers take their own connections) and deletes its
    /// learners at the end.
    #[actix_web::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn todos_add_list_complete_and_filter() {
        let pool = test_db::pool().await;
        let mut conn = pool.acquire().await.unwrap();
        let (user, set) = test_db::seed_learner(&mut conn).await;
        drop(conn);
        let token = crate::auth::mint_token(&pool, user, Some("test")).await.unwrap();
        let app = app!(pool);

        let resp = test::call_service(
            &app,
            post("/todos", &token, serde_json::json!({ "title": "  Ôn chương 3  ", "study_set_id": set })).to_request(),
        )
        .await;
        assert_eq!(resp.status(), actix_web::http::StatusCode::CREATED);
        let created: serde_json::Value = test::read_body_json(resp).await;
        assert_eq!(created["title"], "Ôn chương 3", "title is trimmed");
        assert_eq!(created["source"], "manual");
        assert_eq!(created["study_set_name"], "test set");
        let resp = test::call_service(&app, post("/todos", &token, serde_json::json!({ "title": "Đọc lại vở" })).to_request()).await;
        assert_eq!(resp.status(), actix_web::http::StatusCode::CREATED);

        let list: serde_json::Value = test::read_body_json(test::call_service(&app, get("/todos", &token).to_request()).await).await;
        assert_eq!(list["open_count"], 2);

        let id = created["id"].as_str().unwrap();
        let resp = test::call_service(&app, post(&format!("/todos/{id}/complete"), &token, serde_json::json!({})).to_request()).await;
        assert_eq!(resp.status(), actix_web::http::StatusCode::OK);
        let done: serde_json::Value = test::read_body_json(resp).await;
        assert_eq!(done["done"], true);
        let first_done_at = done["done_at"].clone();
        assert!(first_done_at.is_string());

        // Completing again changes nothing, including when it was done.
        let again: serde_json::Value = test::read_body_json(
            test::call_service(&app, post(&format!("/todos/{id}/complete"), &token, serde_json::json!({})).to_request()).await,
        )
        .await;
        assert_eq!(again["done_at"], first_done_at);

        let open: serde_json::Value = test::read_body_json(test::call_service(&app, get("/todos?done=false", &token).to_request()).await).await;
        let closed: serde_json::Value = test::read_body_json(test::call_service(&app, get("/todos?done=true", &token).to_request()).await).await;
        let all: serde_json::Value = test::read_body_json(test::call_service(&app, get("/todos", &token).to_request()).await).await;

        sqlx::query("DELETE FROM users WHERE id = $1").bind(user).execute(&pool).await.unwrap();

        let titles = |v: &serde_json::Value| -> Vec<String> {
            v["todos"].as_array().unwrap().iter().map(|t| t["title"].as_str().unwrap().to_string()).collect()
        };
        assert_eq!(titles(&open), vec!["Đọc lại vở"]);
        assert_eq!(titles(&closed), vec!["Ôn chương 3"]);
        assert_eq!(titles(&all), vec!["Đọc lại vở", "Ôn chương 3"], "open items come first");
        assert_eq!(all["open_count"], 1);
    }

    /// One learner's token reaches nothing of another's: not their list, not
    /// their items, and not their study sets as the home of a new item.
    #[actix_web::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn todos_are_scoped_to_the_token() {
        let pool = test_db::pool().await;
        let mut conn = pool.acquire().await.unwrap();
        let (mine, _my_set) = test_db::seed_learner(&mut conn).await;
        let (theirs, their_set) = test_db::seed_learner(&mut conn).await;
        let their_item: Uuid = sqlx::query_scalar(
            "INSERT INTO todo_items (user_id, title, source) VALUES ($1, 'theirs', 'manual') RETURNING id",
        )
        .bind(theirs)
        .fetch_one(&mut *conn)
        .await
        .unwrap();
        drop(conn);
        let token = crate::auth::mint_token(&pool, mine, Some("test")).await.unwrap();
        let app = app!(pool);

        let list: serde_json::Value = test::read_body_json(test::call_service(&app, get("/todos", &token).to_request()).await).await;
        let complete = test::call_service(
            &app,
            post(&format!("/todos/{their_item}/complete"), &token, serde_json::json!({})).to_request(),
        )
        .await
        .status();
        let into_their_set = test::call_service(
            &app,
            post("/todos", &token, serde_json::json!({ "title": "x", "study_set_id": their_set })).to_request(),
        )
        .await
        .status();
        let blank = test::call_service(&app, post("/todos", &token, serde_json::json!({ "title": "   " })).to_request())
            .await
            .status();
        let too_long = test::call_service(
            &app,
            post("/todos", &token, serde_json::json!({ "title": "a".repeat(MAX_TITLE_CHARS + 1) })).to_request(),
        )
        .await
        .status();
        let unauthenticated = test::call_service(&app, test::TestRequest::get().uri("/todos").to_request())
            .await
            .status();

        let their_done: bool = sqlx::query_scalar("SELECT done FROM todo_items WHERE id = $1")
            .bind(their_item)
            .fetch_one(&pool)
            .await
            .unwrap();
        let mine_count: i64 = sqlx::query_scalar("SELECT count(*) FROM todo_items WHERE user_id = $1")
            .bind(mine)
            .fetch_one(&pool)
            .await
            .unwrap();
        for u in [mine, theirs] {
            sqlx::query("DELETE FROM users WHERE id = $1").bind(u).execute(&pool).await.unwrap();
        }

        assert_eq!(list["todos"].as_array().unwrap().len(), 0);
        assert_eq!(complete, actix_web::http::StatusCode::NOT_FOUND);
        assert!(!their_done, "another learner's item must stay open");
        assert_eq!(into_their_set, actix_web::http::StatusCode::NOT_FOUND);
        assert_eq!(blank, actix_web::http::StatusCode::BAD_REQUEST);
        assert_eq!(too_long, actix_web::http::StatusCode::BAD_REQUEST);
        assert_eq!(unauthenticated, actix_web::http::StatusCode::UNAUTHORIZED);
        assert_eq!(mine_count, 0, "a refused create must not insert anything");
    }

    /// The database, not just the handler, refuses a second open weak-card
    /// item for a set — the rule a manual insert or a future code path would
    /// otherwise be free to break.
    #[tokio::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn todos_the_index_refuses_a_second_open_weak_item_per_set() {
        let pool = test_db::pool().await;
        let mut tx = pool.begin().await.unwrap();
        let (user, set) = test_db::seed_learner(&mut tx).await;
        let insert = "INSERT INTO todo_items (user_id, study_set_id, title, source, last_weak_card_at) \
                      VALUES ($1, $2, 'weak', 'weak_card', now())";
        sqlx::query(insert).bind(user).bind(set).execute(&mut *tx).await.unwrap();
        let second = sqlx::query(insert).bind(user).bind(set).execute(&mut *tx).await;
        let err = second.expect_err("a second open weak-card item must be refused");
        assert_eq!(classify_db_error(&err).0, actix_web::http::StatusCode::CONFLICT, "{err}");
    }
}
