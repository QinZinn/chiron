//! `GET /stats` — the numbers the dashboard shows: how much was reviewed, how
//! much of it was right, how many days in a row, how much is waiting.
//!
//! Every figure is counted from `learning_events` and `quiz_attempts` at read
//! time. None of it is cached or stored, so it cannot disagree with the rows it
//! summarises — the point of this endpoint is that the dashboard stops being a
//! set of plausible-looking numbers and starts being a report.
//!
//! ## Days are the learner's days
//!
//! A "day" here is a calendar day in the learner's own offset, not UTC: a
//! review at 23:30 in Hanoi belongs to that evening, and counting it as the
//! next day would break a study streak that the learner did not break. The
//! offset comes from the caller (`tz_offset_minutes`), defaulting to UTC+7.

use actix_web::{get, web, HttpResponse};
use chrono::{DateTime, NaiveDate, Utc};
use serde::{Deserialize, Serialize};
use sqlx::{FromRow, PgPool};

use super::error_response;
use crate::auth::AuthedUser;

const DEFAULT_RANGE_DAYS: i64 = 14;
const MAX_RANGE_DAYS: i64 = 365;
/// Asia/Ho_Chi_Minh, the learners' timezone.
const DEFAULT_TZ_OFFSET_MINUTES: i32 = 7 * 60;

#[derive(Debug, Deserialize)]
pub struct StatsQuery {
    pub days: Option<i64>,
    /// Minutes east of UTC. The browser knows this; the server should not guess.
    pub tz_offset_minutes: Option<i32>,
}

#[derive(Debug, Serialize, FromRow, PartialEq)]
pub struct DayCount {
    pub day: NaiveDate,
    pub total: i64,
    pub correct: i64,
}

#[derive(Debug, Serialize)]
pub struct ReviewStats {
    pub total: i64,
    pub correct: i64,
    /// `None` rather than 0.0 when nothing was reviewed: "no data" and "got
    /// everything wrong" must not render as the same bar.
    pub accuracy: Option<f64>,
    pub by_day: Vec<DayCount>,
}

#[derive(Debug, Serialize)]
pub struct QuizStats {
    pub attempts: i64,
    pub correct: i64,
    pub accuracy: Option<f64>,
}

#[derive(Debug, Serialize)]
pub struct CardStats {
    pub total: i64,
    pub due_now: i64,
    pub never_reviewed: i64,
    pub study_sets: i64,
}

#[derive(Debug, Serialize)]
pub struct StatsResponse {
    pub range_days: i64,
    pub tz_offset_minutes: i32,
    pub reviews: ReviewStats,
    pub quiz: QuizStats,
    pub cards: CardStats,
    /// Consecutive days with at least one review, counting back from today. A
    /// day that has not finished yet does not break it: if nothing has been
    /// reviewed today, the streak is measured to yesterday instead of reset.
    pub streak_days: i64,
    pub socratic_sessions: i64,
    pub chat_sessions: i64,
}

const REVIEWS_BY_DAY: &str = r#"SELECT (created_at + make_interval(mins => $2))::date AS day,
          count(*) AS total,
          count(*) FILTER (WHERE is_correct) AS correct
   FROM learning_events
   WHERE user_id = $1
     AND created_at >= now() - make_interval(days => $3::int)
   GROUP BY day
   ORDER BY day"#;

/// Distinct review days, newest first — enough to walk a streak back without
/// pulling every event.
const REVIEW_DAYS: &str = r#"SELECT DISTINCT (created_at + make_interval(mins => $2))::date AS day
   FROM learning_events
   WHERE user_id = $1
   ORDER BY day DESC
   LIMIT 400"#;

const QUIZ_STATS: &str = r#"SELECT count(*) AS total,
          count(*) FILTER (WHERE is_correct) AS correct
   FROM quiz_attempts
   WHERE user_id = $1
     AND created_at >= now() - make_interval(days => $2::int)"#;

/// Cards, and how many of them are waiting. The due half repeats `due.rs`'s
/// rule (latest event per card, NULL means never reviewed) because that is the
/// definition of "due"; a second, looser rule here would make the dashboard
/// disagree with the review queue the learner then opens.
const CARD_STATS: &str = r#"SELECT
     (SELECT count(*) FROM cards c JOIN study_sets s ON s.id = c.set_id WHERE s.user_id = $1) AS total,
     (SELECT count(*) FROM study_sets WHERE user_id = $1) AS study_sets,
     (SELECT count(*) FROM cards c
        JOIN study_sets s ON s.id = c.set_id
        LEFT JOIN LATERAL (
          SELECT next_review_at FROM learning_events
          WHERE card_id = c.id AND user_id = $1
          ORDER BY created_at DESC LIMIT 1
        ) le ON true
       WHERE s.user_id = $1
         AND (le.next_review_at IS NULL OR le.next_review_at <= now())) AS due_now,
     (SELECT count(*) FROM cards c
        JOIN study_sets s ON s.id = c.set_id
       WHERE s.user_id = $1
         AND NOT EXISTS (SELECT 1 FROM learning_events e WHERE e.card_id = c.id AND e.user_id = $1)) AS never_reviewed"#;

const SESSION_COUNTS: &str = r#"SELECT
     (SELECT count(*) FROM socratic_sessions WHERE user_id = $1) AS socratic,
     (SELECT count(*) FROM chat_sessions WHERE user_id = $1) AS chat"#;

/// Walk back from today over the days that have at least one review.
///
/// Today not being in the list does not end the streak — the day is still in
/// progress. Yesterday missing does.
pub fn streak_from_days(mut days: Vec<NaiveDate>, today: NaiveDate) -> i64 {
    days.sort_unstable();
    days.dedup();
    days.reverse();
    let mut streak = 0i64;
    let mut expected = today;
    for day in days {
        if day > today {
            continue; // clock skew; ignore rather than credit a future day
        }
        if day == expected {
            streak += 1;
            expected = expected.pred_opt().unwrap_or(expected);
        } else if streak == 0 && day == today.pred_opt().unwrap_or(today) {
            // Nothing today yet: start the count at yesterday.
            streak = 1;
            expected = day.pred_opt().unwrap_or(day);
        } else {
            break;
        }
    }
    streak
}

fn accuracy(correct: i64, total: i64) -> Option<f64> {
    if total == 0 {
        None
    } else {
        Some((correct as f64 / total as f64 * 1000.0).round() / 1000.0)
    }
}

#[get("/stats")]
pub async fn stats(
    pool: web::Data<PgPool>,
    user: AuthedUser,
    query: web::Query<StatsQuery>,
) -> HttpResponse {
    let days = query.days.unwrap_or(DEFAULT_RANGE_DAYS).clamp(1, MAX_RANGE_DAYS);
    let tz = query
        .tz_offset_minutes
        .unwrap_or(DEFAULT_TZ_OFFSET_MINUTES)
        .clamp(-14 * 60, 14 * 60);
    let uid = user.user_id;

    let by_day: Vec<DayCount> = match sqlx::query_as::<_, DayCount>(REVIEWS_BY_DAY)
        .bind(uid)
        .bind(tz)
        .bind(days as i32)
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

    let review_days: Vec<NaiveDate> = match sqlx::query_scalar::<_, NaiveDate>(REVIEW_DAYS)
        .bind(uid)
        .bind(tz)
        .fetch_all(pool.get_ref())
        .await
    {
        Ok(r) => r,
        Err(e) => {
            return error_response(
                actix_web::http::StatusCode::INTERNAL_SERVER_ERROR,
                format!("database error reading study days: {e}"),
            );
        }
    };

    let (quiz_total, quiz_correct): (i64, i64) = match sqlx::query_as(QUIZ_STATS)
        .bind(uid)
        .bind(days as i32)
        .fetch_one(pool.get_ref())
        .await
    {
        Ok(r) => r,
        Err(e) => {
            return error_response(
                actix_web::http::StatusCode::INTERNAL_SERVER_ERROR,
                format!("database error reading quiz attempts: {e}"),
            );
        }
    };

    let (total, study_sets, due_now, never_reviewed): (i64, i64, i64, i64) =
        match sqlx::query_as(CARD_STATS).bind(uid).fetch_one(pool.get_ref()).await {
            Ok(r) => r,
            Err(e) => {
                return error_response(
                    actix_web::http::StatusCode::INTERNAL_SERVER_ERROR,
                    format!("database error counting cards: {e}"),
                );
            }
        };

    let (socratic_sessions, chat_sessions): (i64, i64) =
        match sqlx::query_as(SESSION_COUNTS).bind(uid).fetch_one(pool.get_ref()).await {
            Ok(r) => r,
            Err(e) => {
                return error_response(
                    actix_web::http::StatusCode::INTERNAL_SERVER_ERROR,
                    format!("database error counting sessions: {e}"),
                );
            }
        };

    let reviews_total: i64 = by_day.iter().map(|d| d.total).sum();
    let reviews_correct: i64 = by_day.iter().map(|d| d.correct).sum();
    let today: NaiveDate = (Utc::now() + chrono::Duration::minutes(tz as i64)).date_naive();

    HttpResponse::Ok().json(StatsResponse {
        range_days: days,
        tz_offset_minutes: tz,
        reviews: ReviewStats {
            total: reviews_total,
            correct: reviews_correct,
            accuracy: accuracy(reviews_correct, reviews_total),
            by_day,
        },
        quiz: QuizStats {
            attempts: quiz_total,
            correct: quiz_correct,
            accuracy: accuracy(quiz_correct, quiz_total),
        },
        cards: CardStats { total, due_now, never_reviewed, study_sets },
        streak_days: streak_from_days(review_days, today),
        socratic_sessions,
        chat_sessions,
    })
}

/// Unused outside tests, but keeps the type honest about what it parses.
#[allow(dead_code)]
fn parse_day(s: &str) -> NaiveDate {
    s.parse().expect("test dates are literals")
}

#[allow(dead_code)]
fn utc(s: &str) -> DateTime<Utc> {
    s.parse().expect("test timestamps are literals")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::handlers::test_db;

    #[test]
    fn a_streak_counts_consecutive_days_back_from_today() {
        let today = parse_day("2026-09-16");
        let days = vec![parse_day("2026-09-16"), parse_day("2026-09-15"), parse_day("2026-09-14")];
        assert_eq!(streak_from_days(days, today), 3);
    }

    #[test]
    fn a_day_still_in_progress_does_not_break_the_streak() {
        // Nothing reviewed today yet. Resetting to 0 here would tell a learner
        // at 9am that they had lost an eleven-day streak they still have.
        let today = parse_day("2026-09-16");
        let days = vec![parse_day("2026-09-15"), parse_day("2026-09-14")];
        assert_eq!(streak_from_days(days, today), 2);
    }

    #[test]
    fn a_missed_day_ends_the_streak() {
        let today = parse_day("2026-09-16");
        let days = vec![parse_day("2026-09-16"), parse_day("2026-09-13"), parse_day("2026-09-12")];
        assert_eq!(streak_from_days(days, today), 1);
    }

    #[test]
    fn no_reviews_at_all_is_a_streak_of_zero() {
        assert_eq!(streak_from_days(vec![], parse_day("2026-09-16")), 0);
    }

    #[test]
    fn duplicate_and_future_days_do_not_inflate_the_streak() {
        let today = parse_day("2026-09-16");
        let days = vec![
            parse_day("2026-09-17"), // clock skew on a client
            parse_day("2026-09-16"),
            parse_day("2026-09-16"),
            parse_day("2026-09-15"),
        ];
        assert_eq!(streak_from_days(days, today), 2);
    }

    #[test]
    fn accuracy_is_absent_rather_than_zero_when_nothing_was_answered() {
        assert_eq!(accuracy(0, 0), None);
        assert_eq!(accuracy(0, 4), Some(0.0));
        assert_eq!(accuracy(3, 4), Some(0.75));
    }

    #[tokio::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn review_days_use_the_learners_offset_not_utc() {
        let pool = test_db::pool().await;
        let mut tx = pool.begin().await.unwrap();
        let (user_id, set_id) = test_db::seed_learner(&mut tx).await;
        let card = test_db::seed_card(&mut tx, set_id, "late night").await;

        // 23:30 in Hanoi on the 15th is 16:30 UTC on the 15th; 00:30 on the
        // 16th in Hanoi is 17:30 UTC on the 15th. Counted in UTC both land on
        // the 15th and the learner loses a day of their streak.
        for ts in ["2026-09-15T16:30:00Z", "2026-09-15T17:30:00Z"] {
            sqlx::query(
                "INSERT INTO learning_events (card_id, user_id, is_correct, interval, next_review_at, created_at) \
                 VALUES ($1, $2, true, 1, now(), $3::timestamptz)",
            )
            .bind(card)
            .bind(user_id)
            .bind(ts)
            .execute(&mut *tx)
            .await
            .unwrap();
        }

        let days: Vec<NaiveDate> = sqlx::query_scalar(REVIEW_DAYS)
            .bind(user_id)
            .bind(7 * 60)
            .fetch_all(&mut *tx)
            .await
            .unwrap();

        assert_eq!(days.len(), 2, "two local days, not one: {days:?}");
        assert!(days.contains(&parse_day("2026-09-15")));
        assert!(days.contains(&parse_day("2026-09-16")));
    }

    #[tokio::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn card_stats_count_only_the_callers_cards() {
        let pool = test_db::pool().await;
        let mut tx = pool.begin().await.unwrap();
        let (mine, my_set) = test_db::seed_learner(&mut tx).await;
        let (_theirs, their_set) = test_db::seed_learner(&mut tx).await;
        test_db::seed_card(&mut tx, my_set, "mine").await;
        test_db::seed_card(&mut tx, their_set, "theirs").await;
        test_db::seed_card(&mut tx, their_set, "theirs too").await;

        let (total, sets, due, never): (i64, i64, i64, i64) =
            sqlx::query_as(CARD_STATS).bind(mine).fetch_one(&mut *tx).await.unwrap();

        assert_eq!(total, 1);
        assert_eq!(sets, 1);
        assert_eq!(due, 1, "a never-reviewed card is due");
        assert_eq!(never, 1);
        let _ = utc("2026-09-16T00:00:00Z");
    }
}
