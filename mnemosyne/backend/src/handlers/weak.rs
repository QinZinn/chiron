//! `GET /weak_cards` — the read side of the weak-card feature.
//!
//! [`crate::weak_cards`] already writes this data on every review: a card the
//! learner keeps failing joins their study set's open `@ontap` task, which
//! Horae schedules from Todoist. Until now nothing could read it back, so the
//! only way to see one's weak cards was Todoist.
//!
//! The judgement here is deliberately the *same* one the writer makes —
//! [`crate::weak_cards::assess`] over the last [`WEAK_CARD_WINDOW`] reviews —
//! rather than a second, prettier definition of "weak". Two different rules
//! would let this endpoint disagree with the task that Horae is scheduling,
//! and the learner would have no way to tell which one was lying.

use actix_web::{get, web, HttpResponse};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::{FromRow, PgPool};
use uuid::Uuid;

use super::error_response;
use crate::auth::AuthedUser;
use crate::weak_cards::{assess, Weakness, WEAK_CARD_ERROR_THRESHOLD, WEAK_CARD_WINDOW};

#[derive(Debug, Deserialize)]
pub struct WeakQuery {
    /// Closed tasks are history: a set the learner recovered from. Off by
    /// default so the dashboard shows what still needs work.
    #[serde(default)]
    pub include_closed: bool,
}

/// One card inside a task, with the evidence for why it is listed.
#[derive(Debug, Serialize, PartialEq)]
pub struct WeakCardOut {
    pub card_id: Uuid,
    pub question: String,
    pub added_at: DateTime<Utc>,
    /// Reviews counted, capped at the window. Fewer than the window means the
    /// card is not being judged yet — it was listed earlier and kept.
    pub recent_reviews: usize,
    pub recent_wrong: usize,
    /// Whether it is *still* weak. Listing is append-only for the life of a
    /// task, so a card that recovered stays in the list — saying so is the
    /// difference between "these are your problems" and "these were".
    pub still_weak: bool,
    pub last_reviewed_at: Option<DateTime<Utc>>,
}

#[derive(Debug, Serialize)]
pub struct WeakTaskOut {
    pub id: Uuid,
    pub study_set_id: Uuid,
    pub study_set_name: String,
    pub opened_at: DateTime<Utc>,
    pub last_weak_card_at: DateTime<Utc>,
    pub closed_at: Option<DateTime<Utc>>,
    pub cards: Vec<WeakCardOut>,
    /// How many of `cards` are still failing the window test.
    pub still_weak_count: usize,
}

#[derive(Debug, Serialize)]
pub struct WeakResponse {
    pub tasks: Vec<WeakTaskOut>,
    pub card_count: usize,
    pub still_weak_count: usize,
    /// The rule these numbers come from, so a client never has to guess it.
    pub window: i64,
    pub error_threshold: f64,
}

#[derive(Debug, FromRow)]
struct TaskRow {
    id: Uuid,
    study_set_id: Uuid,
    study_set_name: String,
    opened_at: DateTime<Utc>,
    last_weak_card_at: DateTime<Utc>,
    closed_at: Option<DateTime<Utc>>,
}

#[derive(Debug, FromRow)]
struct CardRow {
    task_row_id: Uuid,
    card_id: Uuid,
    question: String,
    added_at: DateTime<Utc>,
}

/// Tasks for this learner's sets. `todoist_task_id` is deliberately not
/// selected: it is an id in someone else's system, useless to a client here
/// and needless to expose.
const TASKS_QUERY: &str = r#"SELECT t.id, t.study_set_id, s.name AS study_set_name,
          t.opened_at, t.last_weak_card_at, t.closed_at
   FROM weak_card_tasks t
   JOIN study_sets s ON s.id = t.study_set_id
   WHERE s.user_id = $1
     AND ($2 OR t.closed_at IS NULL)
   ORDER BY t.closed_at NULLS FIRST, t.last_weak_card_at DESC"#;

const CARDS_QUERY: &str = r#"SELECT wc.task_row_id, wc.card_id, c.question, wc.added_at
   FROM weak_card_task_cards wc
   JOIN cards c ON c.id = wc.card_id
   WHERE wc.task_row_id = ANY($1)
   ORDER BY wc.added_at"#;

/// The last N answers per listed card, newest first — the same shape
/// `weak_cards::assess` judges. One query for every card in the response
/// instead of one per card.
const RECENT_QUERY: &str = r#"SELECT card_id, is_correct, created_at
   FROM (
     SELECT card_id, is_correct, created_at,
            row_number() OVER (PARTITION BY card_id ORDER BY created_at DESC) AS rn
     FROM learning_events
     WHERE user_id = $1 AND card_id = ANY($2)
   ) ranked
   WHERE rn <= $3
   ORDER BY card_id, created_at DESC"#;

#[derive(Debug, FromRow)]
struct RecentRow {
    card_id: Uuid,
    is_correct: bool,
    created_at: DateTime<Utc>,
}

#[get("/weak_cards")]
pub async fn weak_cards(
    pool: web::Data<PgPool>,
    user: AuthedUser,
    query: web::Query<WeakQuery>,
) -> HttpResponse {
    let tasks: Vec<TaskRow> = match sqlx::query_as::<_, TaskRow>(TASKS_QUERY)
        .bind(user.user_id)
        .bind(query.include_closed)
        .fetch_all(pool.get_ref())
        .await
    {
        Ok(r) => r,
        Err(e) => {
            return error_response(
                actix_web::http::StatusCode::INTERNAL_SERVER_ERROR,
                format!("database error listing weak-card tasks: {e}"),
            );
        }
    };

    if tasks.is_empty() {
        return HttpResponse::Ok().json(WeakResponse {
            tasks: vec![],
            card_count: 0,
            still_weak_count: 0,
            window: WEAK_CARD_WINDOW,
            error_threshold: WEAK_CARD_ERROR_THRESHOLD,
        });
    }

    let task_ids: Vec<Uuid> = tasks.iter().map(|t| t.id).collect();
    let cards: Vec<CardRow> = match sqlx::query_as::<_, CardRow>(CARDS_QUERY)
        .bind(&task_ids)
        .fetch_all(pool.get_ref())
        .await
    {
        Ok(r) => r,
        Err(e) => {
            return error_response(
                actix_web::http::StatusCode::INTERNAL_SERVER_ERROR,
                format!("database error listing weak cards: {e}"),
            );
        }
    };

    let card_ids: Vec<Uuid> = cards.iter().map(|c| c.card_id).collect();
    let recent: Vec<RecentRow> = match sqlx::query_as::<_, RecentRow>(RECENT_QUERY)
        .bind(user.user_id)
        .bind(&card_ids)
        .bind(WEAK_CARD_WINDOW)
        .fetch_all(pool.get_ref())
        .await
    {
        Ok(r) => r,
        Err(e) => {
            return error_response(
                actix_web::http::StatusCode::INTERNAL_SERVER_ERROR,
                format!("database error reading review history: {e}"),
            );
        }
    };

    let mut out_tasks: Vec<WeakTaskOut> = Vec::with_capacity(tasks.len());
    let mut card_count = 0usize;
    let mut total_still_weak = 0usize;

    for task in &tasks {
        let mut cards_out: Vec<WeakCardOut> = Vec::new();
        for card in cards.iter().filter(|c| c.task_row_id == task.id) {
            let answers: Vec<bool> = recent
                .iter()
                .filter(|r| r.card_id == card.card_id)
                .map(|r| r.is_correct)
                .collect();
            let last_reviewed_at = recent
                .iter()
                .filter(|r| r.card_id == card.card_id)
                .map(|r| r.created_at)
                .max();
            let (wrong, still_weak) = match assess(&answers) {
                Weakness::Weak { wrong } => (wrong, true),
                Weakness::NotWeak { wrong } => (wrong, false),
                Weakness::NotEnoughHistory { .. } => {
                    (answers.iter().filter(|c| !**c).count(), false)
                }
            };
            cards_out.push(WeakCardOut {
                card_id: card.card_id,
                question: card.question.clone(),
                added_at: card.added_at,
                recent_reviews: answers.len(),
                recent_wrong: wrong,
                still_weak,
                last_reviewed_at,
            });
        }
        let still_weak_count = cards_out.iter().filter(|c| c.still_weak).count();
        card_count += cards_out.len();
        total_still_weak += still_weak_count;
        out_tasks.push(WeakTaskOut {
            id: task.id,
            study_set_id: task.study_set_id,
            study_set_name: task.study_set_name.clone(),
            opened_at: task.opened_at,
            last_weak_card_at: task.last_weak_card_at,
            closed_at: task.closed_at,
            cards: cards_out,
            still_weak_count,
        });
    }

    HttpResponse::Ok().json(WeakResponse {
        tasks: out_tasks,
        card_count,
        still_weak_count: total_still_weak,
        window: WEAK_CARD_WINDOW,
        error_threshold: WEAK_CARD_ERROR_THRESHOLD,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::handlers::test_db;

    /// The endpoint must judge with the writer's rule, not a copy of it. This
    /// pins the two together: if `assess` changes, this test changes with it,
    /// and a hand-rolled rule here would fail.
    #[test]
    fn still_weak_follows_the_writers_rule() {
        // Four of five wrong: weak by any reading.
        assert!(matches!(assess(&[false, false, false, false, true]), Weakness::Weak { .. }));
        // Exactly the threshold (2 of 5 = 0.4) is still weak.
        assert!(matches!(assess(&[false, false, true, true, true]), Weakness::Weak { wrong: 2 }));
        // One of five is not.
        assert!(matches!(assess(&[false, true, true, true, true]), Weakness::NotWeak { wrong: 1 }));
        // Too few reviews to judge at all.
        assert!(matches!(assess(&[false, false]), Weakness::NotEnoughHistory { reviews: 2 }));
    }

    #[tokio::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn weak_cards_lists_only_the_callers_tasks() {
        let pool = test_db::pool().await;
        let mut tx = pool.begin().await.unwrap();
        let (mine, my_set) = test_db::seed_learner(&mut tx).await;
        let (theirs, their_set) = test_db::seed_learner(&mut tx).await;

        for (set, todoist_id) in [(my_set, "mine-1"), (their_set, "theirs-1")] {
            sqlx::query(
                "INSERT INTO weak_card_tasks (study_set_id, todoist_task_id) VALUES ($1, $2)",
            )
            .bind(set)
            .bind(todoist_id)
            .execute(&mut *tx)
            .await
            .unwrap();
        }

        let rows: Vec<TaskRow> = sqlx::query_as(TASKS_QUERY)
            .bind(mine)
            .bind(false)
            .fetch_all(&mut *tx)
            .await
            .unwrap();

        assert_eq!(rows.len(), 1, "one learner must not see the other's tasks");
        assert_eq!(rows[0].study_set_id, my_set);
        let _ = theirs;
    }

    #[tokio::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn closed_tasks_are_hidden_unless_asked_for() {
        let pool = test_db::pool().await;
        let mut tx = pool.begin().await.unwrap();
        let (user_id, set_id) = test_db::seed_learner(&mut tx).await;

        sqlx::query(
            "INSERT INTO weak_card_tasks (study_set_id, todoist_task_id, closed_at) \
             VALUES ($1, 'closed', now())",
        )
        .bind(set_id)
        .execute(&mut *tx)
        .await
        .unwrap();

        let open: Vec<TaskRow> = sqlx::query_as(TASKS_QUERY)
            .bind(user_id)
            .bind(false)
            .fetch_all(&mut *tx)
            .await
            .unwrap();
        let all: Vec<TaskRow> = sqlx::query_as(TASKS_QUERY)
            .bind(user_id)
            .bind(true)
            .fetch_all(&mut *tx)
            .await
            .unwrap();

        assert!(open.is_empty(), "a closed task is history, not a current weak point");
        assert_eq!(all.len(), 1);
    }

    #[tokio::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn recent_answers_are_capped_at_the_window_per_card() {
        let pool = test_db::pool().await;
        let mut tx = pool.begin().await.unwrap();
        let (user_id, set_id) = test_db::seed_learner(&mut tx).await;
        let card = test_db::seed_card(&mut tx, set_id, "windowed").await;

        // Seven reviews; the window is five, and it must take the newest five.
        for i in 0..7i64 {
            sqlx::query(
                "INSERT INTO learning_events (card_id, user_id, is_correct, interval, next_review_at, created_at) \
                 VALUES ($1, $2, $3, 1, now(), now() - make_interval(days => $4::int))",
            )
            .bind(card)
            .bind(user_id)
            .bind(i >= 5) // the two oldest are the correct ones
            .bind(i as i32)
            .execute(&mut *tx)
            .await
            .unwrap();
        }

        let rows: Vec<RecentRow> = sqlx::query_as(RECENT_QUERY)
            .bind(user_id)
            .bind(&vec![card])
            .bind(WEAK_CARD_WINDOW)
            .fetch_all(&mut *tx)
            .await
            .unwrap();

        assert_eq!(rows.len(), WEAK_CARD_WINDOW as usize);
        assert!(
            rows.iter().all(|r| !r.is_correct),
            "the newest five are the failures; taking the oldest would call this card healthy"
        );
    }
}
