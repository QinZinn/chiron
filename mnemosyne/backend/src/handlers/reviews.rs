//! Handler for `POST /review` — the first endpoint wiring `mnemosyne-core`'s
//! FSRS scheduler into the database. Records a review attempt for a card by
//! a user, computes the updated FSRS scheduling state, and persists both the
//! event and the new state to `learning_events`.

use actix_web::{post, web, HttpResponse};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::{FromRow, PgPool};
use uuid::Uuid;

use mnemosyne_core::models::{CardState, Rating};
use mnemosyne_core::scheduling::{FsrsScheduler, NewCardStates};

use super::{classify_db_error, error_response};
use crate::auth::{owns_card, AuthedUser};
use crate::weak_cards;

/// Incoming review request. The learner comes from the bearer token, never
/// from the body. `rating` is parsed case-insensitively to
/// [`mnemosyne_core::models::Rating`]; anything else is rejected with 400.
#[derive(Debug, Deserialize)]
pub struct ReviewRequest {
    pub card_id: Uuid,
    pub rating: String,
}

/// Response returned on a successful review (HTTP 201).
#[derive(Debug, Serialize)]
pub struct ReviewResponse {
    pub learning_event_id: Uuid,
    pub stability: f32,
    pub difficulty: f32,
    pub interval_days: i64,
    pub next_review_at: DateTime<Utc>,
}

/// Row used to reconstruct the prior FSRS scheduling state for a (card, user)
/// pair. `stability`/`difficulty` are nullable in the schema (legacy pre-FSRS
/// rows would have NULL); we use `Option<f64>` because Postgres `FLOAT` (no
/// precision specifier) is `FLOAT8` (double precision), and sqlx maps `FLOAT8`
/// to `f64`.
#[derive(Debug, FromRow)]
struct PriorEventRow {
    stability: Option<f64>,
    difficulty: Option<f64>,
    created_at: DateTime<Utc>,
}

/// Row returned by `INSERT ... RETURNING` for the new learning_event.
#[derive(Debug, FromRow)]
struct InsertedEventRow {
    id: Uuid,
    stability: Option<f64>,
    difficulty: Option<f64>,
    interval: i32,
    next_review_at: DateTime<Utc>,
}

/// The prior-state lookup. Named so that tests run the same text the handler
/// does; a test holding its own copy would keep passing after this one drifted.
///
/// `ORDER BY created_at DESC LIMIT 1` is the whole point: FSRS compounds, so
/// reading the *first* review instead of the *latest* would quietly reschedule
/// every card from its original state forever.
const PRIOR_STATE_QUERY: &str = r#"SELECT stability, difficulty, created_at
   FROM learning_events
   WHERE card_id = $1 AND user_id = $2
   ORDER BY created_at DESC
   LIMIT 1"#;

/// The event insert. `card_id`/`user_id` are FK columns, and that is the only
/// thing standing between a review and a card that does not exist.
const INSERT_EVENT_QUERY: &str = r#"INSERT INTO learning_events
     (card_id, user_id, is_correct, stability, difficulty, interval, next_review_at)
   VALUES ($1, $2, $3, $4, $5, $6, $7)
   RETURNING id, stability, difficulty, interval, next_review_at"#;

/// Parse the incoming rating string (case-insensitive) to the FSRS `Rating`
/// enum. Returns `Err(())` on anything not in {again, hard, good, easy}.
fn parse_rating(s: &str) -> Result<Rating, ()> {
    match s.to_lowercase().as_str() {
        "again" => Ok(Rating::Again),
        "hard" => Ok(Rating::Hard),
        "good" => Ok(Rating::Good),
        "easy" => Ok(Rating::Easy),
        _ => Err(()),
    }
}

/// Deliberate product decision: Again = forgotten, everything else = correct
/// (matches the Anki/FSRS convention). Hard counts as correct — the learner
/// did recall it, with effort.
fn is_correct(rating: Rating) -> bool {
    rating != Rating::Again
}

/// Pick the state matching the learner's rating out of the four the scheduler
/// produces for a brand-new card.
fn state_for_rating(states: NewCardStates, rating: Rating) -> CardState {
    match rating {
        Rating::Again => states.again,
        Rating::Hard => states.hard,
        Rating::Good => states.good,
        Rating::Easy => states.easy,
    }
}

/// Recover the integer day interval the scheduler chose.
///
/// The floor of 1 is not cosmetic: `num_days()` truncates, so any interval the
/// scheduler set to less than a full day would come back as 0 and be written
/// as "due immediately, forever".
fn interval_days_between(due: DateTime<Utc>, now: DateTime<Utc>) -> i64 {
    (due - now).num_days().max(1)
}

#[post("/review")]
pub async fn review(
    pool: web::Data<PgPool>,
    scheduler: web::Data<FsrsScheduler>,
    user: AuthedUser,
    body: web::Json<ReviewRequest>,
) -> HttpResponse {
    let user_id = user.user_id;

    // 0. The card must belong to one of this learner's study sets. Without
    //    this, a valid token could file reviews against somebody else's card
    //    and pollute their FSRS history. A card owned by another learner is
    //    reported as "not found", the same as one that does not exist: telling
    //    the two apart would let any token probe which card ids are real.
    match owns_card(pool.get_ref(), user_id, body.card_id).await {
        Ok(true) => {}
        Ok(false) => {
            return error_response(
                actix_web::http::StatusCode::NOT_FOUND,
                format!("card {} not found", body.card_id),
            );
        }
        Err(e) => {
            return error_response(
                actix_web::http::StatusCode::INTERNAL_SERVER_ERROR,
                format!("database error checking card ownership: {e}"),
            );
        }
    }

    // 1. Parse rating (case-insensitive). Reject bad strings with 400 — do
    //    not silently default.
    let rating = match parse_rating(&body.rating) {
        Ok(r) => r,
        Err(_) => {
            return error_response(
                actix_web::http::StatusCode::BAD_REQUEST,
                format!(
                    "invalid rating '{}': must be one of again, hard, good, easy",
                    body.rating
                ),
            );
        }
    };

    let now = Utc::now();
    let is_correct = is_correct(rating);

    // 2. Look up prior scheduling state for this (card, user) pair. If no
    //    prior event exists (or it predates FSRS adoption and has NULL
    //    stability/difficulty), treat as a brand-new card.
    let prior: Option<PriorEventRow> = match sqlx::query_as::<_, PriorEventRow>(PRIOR_STATE_QUERY)
    .bind(body.card_id)
    .bind(user_id)
    .fetch_optional(pool.get_ref())
    .await
    {
        Ok(row) => row,
        Err(e) => {
            return error_response(
                actix_web::http::StatusCode::INTERNAL_SERVER_ERROR,
                format!("database error looking up prior state: {e}"),
            );
        }
    };

    // 3. Compute the updated CardState via the FSRS scheduler.
    let resulting_state: CardState = match prior {
        Some(p) if p.stability.is_some() && p.difficulty.is_some() => {
            // Follow-up review: reconstruct prior state, call schedule_review.
            // `last_review` is when the prior event happened = its created_at.
            // `due` is informational here — schedule_review ignores it (it
            // computes elapsed from last_review).
            let current = CardState {
                stability: p.stability.unwrap() as f32,
                difficulty: p.difficulty.unwrap() as f32,
                due: now,
                last_review: Some(p.created_at),
            };
            match scheduler.schedule_review(&current, rating, now) {
                Ok((state, _log)) => state,
                Err(e) => {
                    return error_response(
                        actix_web::http::StatusCode::INTERNAL_SERVER_ERROR,
                        format!("scheduler error: {e}"),
                    );
                }
            }
        }
        _ => {
            // First review (or prior was pre-FSRS): use the schedule_new path,
            // pick the state matching the learner's actual rating.
            let states = match scheduler.schedule_new(now) {
                Ok(s) => s,
                Err(e) => {
                    return error_response(
                        actix_web::http::StatusCode::INTERNAL_SERVER_ERROR,
                        format!("scheduler error: {e}"),
                    );
                }
            };
            state_for_rating(states, rating)
        }
    };

    // 4. Derive interval (days) and next_review_at from the resulting state.
    //    The scheduler sets `due = now + round(interval).max(1) days`, so we
    //    can recover the integer interval by subtracting now from due.
    let interval_days = interval_days_between(resulting_state.due, now);

    // 5. Insert a new learning_events row. card_id/user_id existence is
    //    enforced by FK constraints; a violation is mapped to 400 by
    //    classify_db_error(). We leave `ease_factor` at its schema default
    //    (2.5) — it's legacy/unused post-FSRS (see ADR 0001).
    let inserted: InsertedEventRow = match sqlx::query_as::<_, InsertedEventRow>(INSERT_EVENT_QUERY)
    .bind(body.card_id)
    .bind(user_id)
    .bind(is_correct)
    .bind(resulting_state.stability as f64)
    .bind(resulting_state.difficulty as f64)
    .bind(interval_days as i32)
    .bind(resulting_state.due)
    .fetch_one(pool.get_ref())
    .await
    {
        Ok(row) => row,
        Err(e) => {
            let (status, msg) = classify_db_error(&e);
            return error_response(status, msg);
        }
    };

    // 6. Weak-card check: open or extend the set's weak-card todo item when
    //    this card keeps being failed. Runs only once the review is stored,
    //    and cannot fail the request — it returns an outcome it has already
    //    logged, never an error. See crate::weak_cards.
    weak_cards::sync_after_review(pool.get_ref(), body.card_id, user_id).await;

    // 7. Respond with the persisted state (read back from the DB so the
    //    response reflects exactly what was stored, not the in-memory value).
    HttpResponse::Created().json(ReviewResponse {
        learning_event_id: inserted.id,
        stability: inserted.stability.unwrap_or(0.0) as f32,
        difficulty: inserted.difficulty.unwrap_or(0.0) as f32,
        interval_days: inserted.interval as i64,
        next_review_at: inserted.next_review_at,
    })
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::handlers::test_db;
    use chrono::Duration;

    // -- pure -----------------------------------------------------------------

    #[test]
    fn ratings_are_parsed_case_insensitively() {
        assert_eq!(parse_rating("again"), Ok(Rating::Again));
        assert_eq!(parse_rating("Hard"), Ok(Rating::Hard));
        assert_eq!(parse_rating("GOOD"), Ok(Rating::Good));
        assert_eq!(parse_rating("eAsY"), Ok(Rating::Easy));
    }

    #[test]
    fn anything_outside_the_four_ratings_is_rejected() {
        // Rejected rather than defaulted: silently reading an unknown rating as
        // "good" would reschedule a card the learner may have just failed, and
        // nothing downstream would ever show that it happened.
        for bad in ["", "  ", "ok", "3", "forgot", "again ", "trop facile"] {
            assert!(parse_rating(bad).is_err(), "should reject {bad:?}");
        }
    }

    #[test]
    fn only_again_counts_as_forgetting() {
        // Hard is a success: the learner did recall it, with effort. Treating
        // it as a failure would collapse a four-way rating into pass/fail and
        // throw away the signal FSRS uses most.
        assert!(!is_correct(Rating::Again));
        assert!(is_correct(Rating::Hard));
        assert!(is_correct(Rating::Good));
        assert!(is_correct(Rating::Easy));
    }

    #[test]
    fn each_rating_selects_its_own_scheduled_state() {
        // The four states differ; picking the wrong arm would schedule the card
        // as though the learner had answered something they did not.
        let scheduler = FsrsScheduler::default();
        let now = Utc::now();
        let expected = scheduler.schedule_new(now).unwrap();

        for (rating, want) in [
            (Rating::Again, expected.again.stability),
            (Rating::Hard, expected.hard.stability),
            (Rating::Good, expected.good.stability),
            (Rating::Easy, expected.easy.stability),
        ] {
            let states = scheduler.schedule_new(now).unwrap();
            assert_eq!(
                state_for_rating(states, rating).stability,
                want,
                "wrong state for {rating:?}"
            );
        }

        // And they really are distinct, so the assertions above can fail.
        assert_ne!(expected.again.stability, expected.easy.stability);
    }

    #[test]
    fn an_interval_shorter_than_a_day_still_counts_as_one() {
        // num_days() truncates. Without the floor, a sub-day interval is stored
        // as 0 and the card becomes permanently due — the schedule stops
        // spacing anything at all.
        let now = Utc::now();
        assert_eq!(interval_days_between(now, now), 1);
        assert_eq!(interval_days_between(now + Duration::hours(23), now), 1);
        assert_eq!(interval_days_between(now + Duration::days(5), now), 5);
        // Also guards against a due date in the past producing a negative.
        assert_eq!(interval_days_between(now - Duration::days(3), now), 1);
    }

    // -- against the real database (see handlers::test_db) ---------------------

    #[tokio::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn reviews_prior_state_lookup_returns_the_latest_review() {
        let pool = test_db::pool().await;
        let mut tx = pool.begin().await.unwrap();
        let (user_id, set_id) = test_db::seed_learner(&mut tx).await;
        let card = test_db::seed_card(&mut tx, set_id, "reviewed twice").await;

        let now = Utc::now();
        test_db::record_review(&mut tx, card, user_id, 9.0, 2.0, now, now - Duration::days(2)).await;
        test_db::record_review(&mut tx, card, user_id, 0.5, 8.0, now, now - Duration::days(1)).await;

        let prior: Option<PriorEventRow> = sqlx::query_as(PRIOR_STATE_QUERY)
            .bind(card)
            .bind(user_id)
            .fetch_optional(&mut *tx)
            .await
            .unwrap();

        let prior = prior.expect("a reviewed card has prior state");
        // This row is the input to the next FSRS computation. Reading the first
        // review instead of the latest would reschedule the card from its
        // original state every single time, undoing the learner's history.
        assert_eq!(prior.stability, Some(0.5), "took the wrong review");
        assert_eq!(prior.difficulty, Some(8.0));
    }

    #[tokio::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn reviews_a_card_with_no_history_has_no_prior_state() {
        let pool = test_db::pool().await;
        let mut tx = pool.begin().await.unwrap();
        let (user_id, set_id) = test_db::seed_learner(&mut tx).await;
        let card = test_db::seed_card(&mut tx, set_id, "never reviewed").await;

        let prior: Option<PriorEventRow> = sqlx::query_as(PRIOR_STATE_QUERY)
            .bind(card)
            .bind(user_id)
            .fetch_optional(&mut *tx)
            .await
            .unwrap();

        // None is what routes the handler to schedule_new rather than
        // schedule_review; an error here would be a 500 on a first review.
        assert!(prior.is_none());
    }

    /// The handler end to end, through actix: a card reviewed into weakness
    /// files one todo item for its set, a further weak review does not file a
    /// second, and the review response is unaffected.
    ///
    /// Unlike the tests around it this one **commits**: the handler takes its
    /// own connections from the pool, so it cannot see rows held in a test's
    /// open transaction. It deletes its learner at the end, and every row it
    /// made cascades away with them.
    #[actix_web::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn reviews_a_weak_card_files_one_todo_item() {
        use actix_web::{test, App};

        let pool = test_db::pool().await;
        let mut conn = pool.acquire().await.unwrap();
        let (user_id, set_id) = test_db::seed_learner(&mut conn).await;
        let card_id = test_db::seed_card(&mut conn, set_id, "e2e weak card").await;
        drop(conn);

        let token = crate::auth::mint_token(&pool, user_id, Some("test")).await.unwrap();
        let app = test::init_service(
            App::new()
                .app_data(web::Data::new(pool.clone()))
                .app_data(web::Data::new(FsrsScheduler::default()))
                .service(review),
        )
        .await;

        // Two "again" in five: the fifth review makes the card weak; the
        // sixth, another "again", keeps it weak.
        let mut responses = Vec::new();
        for rating in ["again", "good", "again", "good", "good", "again"] {
            let req = test::TestRequest::post()
                .uri("/review")
                .insert_header(("Authorization", format!("Bearer {token}")))
                .set_json(serde_json::json!({ "card_id": card_id, "rating": rating }))
                .to_request();
            let resp = test::call_service(&app, req).await;
            assert_eq!(resp.status(), actix_web::http::StatusCode::CREATED, "rating {rating}");
            let body: serde_json::Value = test::read_body_json(resp).await;
            responses.push(body);
        }

        let stored: (Uuid, Option<f64>, Option<f64>, i32) = sqlx::query_as(
            "SELECT id, stability, difficulty, interval FROM learning_events \
             WHERE card_id = $1 ORDER BY created_at DESC LIMIT 1",
        )
        .bind(card_id)
        .fetch_one(&pool)
        .await
        .unwrap();
        let items: Vec<(Uuid, Uuid, String, bool)> = sqlx::query_as(
            "SELECT id, user_id, source, done FROM todo_items WHERE study_set_id = $1",
        )
        .bind(set_id)
        .fetch_all(&pool)
        .await
        .unwrap();
        let listed: Vec<Uuid> = sqlx::query_scalar(
            "SELECT card_id FROM todo_item_cards WHERE todo_id = ANY($1)",
        )
        .bind(items.iter().map(|i| i.0).collect::<Vec<_>>())
        .fetch_all(&pool)
        .await
        .unwrap();

        sqlx::query("DELETE FROM users WHERE id = $1").bind(user_id).execute(&pool).await.unwrap();

        let last = responses.last().unwrap();
        assert_eq!(last["learning_event_id"], serde_json::json!(stored.0));
        assert_eq!(last["stability"].as_f64().unwrap() as f32, stored.1.unwrap() as f32);
        assert_eq!(last["difficulty"].as_f64().unwrap() as f32, stored.2.unwrap() as f32);
        assert_eq!(last["interval_days"], serde_json::json!(stored.3));
        assert_eq!(items.len(), 1, "exactly one todo item for the set: {items:?}");
        assert_eq!((items[0].1, items[0].2.as_str(), items[0].3), (user_id, "weak_card", false));
        assert_eq!(listed, vec![card_id]);
    }

    /// The whole point of the token layer: without one, nothing is recorded.
    /// A 401 that still wrote the review would be worse than no auth at all,
    /// so this checks the database as well as the status code.
    #[actix_web::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn reviews_without_a_token_are_refused_and_store_nothing() {
        use actix_web::{test, App};

        let pool = test_db::pool().await;
        let mut conn = pool.acquire().await.unwrap();
        let (user_id, set_id) = test_db::seed_learner(&mut conn).await;
        let card_id = test_db::seed_card(&mut conn, set_id, "unauthenticated").await;
        drop(conn);

        let app = test::init_service(
            App::new()
                .app_data(web::Data::new(pool.clone()))
                .app_data(web::Data::new(FsrsScheduler::default()))
                .service(review),
        )
        .await;

        for header in [None, Some("Bearer mnem_not-a-real-token")] {
            let mut req = test::TestRequest::post()
                .uri("/review")
                .set_json(serde_json::json!({ "card_id": card_id, "rating": "good" }));
            if let Some(h) = header {
                req = req.insert_header(("Authorization", h));
            }
            let resp = test::call_service(&app, req.to_request()).await;
            assert_eq!(
                resp.status(),
                actix_web::http::StatusCode::UNAUTHORIZED,
                "header {header:?}"
            );
        }

        let events: i64 = sqlx::query_scalar("SELECT count(*) FROM learning_events WHERE card_id = $1")
            .bind(card_id)
            .fetch_one(&pool)
            .await
            .unwrap();
        sqlx::query("DELETE FROM users WHERE id = $1").bind(user_id).execute(&pool).await.unwrap();
        assert_eq!(events, 0, "a refused request must not leave a review behind");
    }

    /// A token is not a skeleton key: it authenticates one learner, and that
    /// learner's reach stops at their own cards.
    #[actix_web::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn reviews_cannot_touch_another_learners_card() {
        use actix_web::{test, App};

        let pool = test_db::pool().await;
        let mut conn = pool.acquire().await.unwrap();
        let (mine, _my_set) = test_db::seed_learner(&mut conn).await;
        let (theirs, their_set) = test_db::seed_learner(&mut conn).await;
        let their_card = test_db::seed_card(&mut conn, their_set, "not mine").await;
        drop(conn);

        let token = crate::auth::mint_token(&pool, mine, Some("test")).await.unwrap();
        let app = test::init_service(
            App::new()
                .app_data(web::Data::new(pool.clone()))
                .app_data(web::Data::new(FsrsScheduler::default()))
                .service(review),
        )
        .await;

        let req = test::TestRequest::post()
            .uri("/review")
            .insert_header(("Authorization", format!("Bearer {token}")))
            .set_json(serde_json::json!({ "card_id": their_card, "rating": "good" }))
            .to_request();
        let resp = test::call_service(&app, req).await;
        let status = resp.status();

        let events: i64 = sqlx::query_scalar("SELECT count(*) FROM learning_events WHERE card_id = $1")
            .bind(their_card)
            .fetch_one(&pool)
            .await
            .unwrap();
        for u in [mine, theirs] {
            sqlx::query("DELETE FROM users WHERE id = $1").bind(u).execute(&pool).await.unwrap();
        }

        assert_eq!(status, actix_web::http::StatusCode::NOT_FOUND);
        assert_eq!(events, 0, "one learner must not be able to review another's card");
    }

    #[tokio::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn reviews_a_card_that_does_not_exist_is_rejected_as_a_bad_request() {
        let pool = test_db::pool().await;
        let mut tx = pool.begin().await.unwrap();
        let (user_id, _set_id) = test_db::seed_learner(&mut tx).await;

        let missing_card: Uuid = sqlx::query_scalar("SELECT gen_random_uuid()")
            .fetch_one(&mut *tx)
            .await
            .unwrap();

        let err = sqlx::query_as::<_, InsertedEventRow>(INSERT_EVENT_QUERY)
            .bind(missing_card)
            .bind(user_id)
            .bind(true)
            .bind(1.0_f64)
            .bind(5.0_f64)
            .bind(1_i32)
            .bind(Utc::now())
            .fetch_one(&mut *tx)
            .await
            .expect_err("a review for a card that does not exist must not be stored");

        // The handler's ownership check now answers 404 before the insert is
        // ever reached, so this documents the layer underneath it: if that
        // check were ever removed, the foreign key is the last thing standing
        // between a review and a card that does not exist, and
        // classify_db_error renders it as 400, not 404.
        let (status, message) = crate::handlers::classify_db_error(&err);
        assert_eq!(status, actix_web::http::StatusCode::BAD_REQUEST);
        assert!(message.contains("foreign key"), "message: {message}");
    }
}
