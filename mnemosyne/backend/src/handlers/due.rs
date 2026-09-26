//! `GET /due` — read-only review queue for the authenticated learner.
//!
//! Returns cards the user should review now: either never-reviewed cards, or
//! cards whose most recent `learning_events` row has `next_review_at <= now()`.
//!
//! Ordering: never-reviewed cards (NULL `next_review_at`) surface first, then
//! cards by `next_review_at ASC` (most overdue first). This is a deliberate
//! product decision — see the prompt spec: don't change it.
//!
//! `?order=` only rearranges that batch, never changes which cards are in it:
//!
//! - `due` (default): the order above, untouched.
//! - `interleave`: consecutive cards come from different study sets whenever
//!   the batch allows it, staying as close to the `due` order as it can.
//! - `hardest`: weak cards first, most-missed first, judged by
//!   [`crate::weak_cards::assess`] — the same rule the weak-card todo items and
//!   `GET /weak_cards` use, not a second definition of "hard". Cards too new to
//!   be judged keep their `due` order at the end.

use actix_web::{get, web, HttpResponse};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::{FromRow, PgPool};
use uuid::Uuid;

use super::error_response;
use crate::auth::AuthedUser;
use crate::weak_cards::{assess, Weakness, RECENT_ANSWERS_FOR_CARDS_QUERY, WEAK_CARD_WINDOW};

/// Cap on `limit` to bound query cost.
const DEFAULT_LIMIT: i64 = 20;
const MAX_LIMIT: i64 = 100;

/// The due-card query. Named so tests run the same text the handler does.
///
/// Three separate decisions live in here, and each of them is silent when
/// wrong — a broken one returns a plausible list rather than an error:
///
/// - the LATERAL picks the **latest** event per card, not the first;
/// - `next_review_at <= now()` withholds cards that are scheduled but not yet
///   due, while `IS NULL` lets never-reviewed cards through;
/// - `ss.user_id = $1` scopes to the study set's owner, so one learner's queue
///   cannot show another's cards.
const DUE_QUERY: &str = r#"SELECT c.id AS card_id, c.set_id, c.question, c.answer,
          le.stability, le.difficulty, le.next_review_at
   FROM cards c
   JOIN study_sets ss ON ss.id = c.set_id
   LEFT JOIN LATERAL (
       SELECT stability, difficulty, next_review_at
       FROM learning_events
       WHERE card_id = c.id AND user_id = $1
       ORDER BY created_at DESC
       LIMIT 1
   ) le ON true
   WHERE ss.user_id = $1
     AND (le.next_review_at IS NULL OR le.next_review_at <= now())
   ORDER BY le.next_review_at ASC NULLS FIRST
   LIMIT $2"#;

#[derive(Debug, Deserialize)]
pub struct DueQuery {
    pub limit: Option<i64>,
    pub order: Option<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum DueOrder {
    Due,
    Interleave,
    Hardest,
}

impl DueOrder {
    fn parse(raw: Option<&str>) -> Result<Self, String> {
        match raw.map(str::trim) {
            None | Some("") | Some("due") => Ok(DueOrder::Due),
            Some("interleave") => Ok(DueOrder::Interleave),
            Some("hardest") => Ok(DueOrder::Hardest),
            Some(other) => Err(format!("unknown order {other:?}; expected due, interleave or hardest")),
        }
    }
}

/// Reorder so no two neighbours share a set whenever that is possible.
///
/// Greedy, and as close to the input order as it can be: each step takes the
/// earliest card whose set differs from the previous one. The exception is a
/// set holding more than half of what is left — it must go now, or the tail
/// would end up as a run of that set. With that rule, a run is left only when
/// the batch forces one (one set holds more than half of the whole batch).
pub fn interleave<T>(items: Vec<T>, set_of: impl Fn(&T) -> Uuid) -> Vec<T> {
    let mut left: Vec<Option<T>> = items.into_iter().map(Some).collect();
    let mut out = Vec::with_capacity(left.len());
    let mut last: Option<Uuid> = None;
    for remaining in (1..=left.len()).rev() {
        // Count per set, remembering where each set first appears so a tie
        // goes to the set the `due` order reaches first.
        let mut counts: Vec<(Uuid, usize, usize)> = Vec::new();
        for (i, item) in left.iter().enumerate() {
            let Some(item) = item else { continue };
            let set = set_of(item);
            match counts.iter_mut().find(|(s, _, _)| *s == set) {
                Some(entry) => entry.1 += 1,
                None => counts.push((set, 1, i)),
            }
        }
        let (big, big_count, _) = counts
            .iter()
            .copied()
            .max_by(|a, b| a.1.cmp(&b.1).then(b.2.cmp(&a.2)))
            .expect("at least one card is left");
        let forced = big_count * 2 > remaining && Some(big) != last;
        let pick = left
            .iter()
            .position(|item| {
                item.as_ref().is_some_and(|it| {
                    let set = set_of(it);
                    if forced { set == big } else { Some(set) != last }
                })
            })
            // Only one set left and it is the previous one: a run is unavoidable.
            .or_else(|| left.iter().position(Option::is_some))
            .expect("at least one card is left");
        let item = left[pick].take().expect("picked a card that is still there");
        last = Some(set_of(&item));
        out.push(item);
    }
    out
}

/// How hard a card is by the weak-card rule, for sorting: judged cards first,
/// more misses in the window first. `None` means too few reviews to judge.
fn hardness(recent_newest_first: &[bool]) -> Option<usize> {
    match assess(recent_newest_first) {
        Weakness::Weak { wrong } | Weakness::NotWeak { wrong } => Some(wrong),
        Weakness::NotEnoughHistory { .. } => None,
    }
}

/// Stable: cards equally hard keep their `due` order.
pub fn hardest_first<T>(items: Vec<T>, hardness_of: impl Fn(&T) -> Option<usize>) -> Vec<T> {
    let mut keyed: Vec<(Option<usize>, T)> = items.into_iter().map(|t| (hardness_of(&t), t)).collect();
    // Some(n) before None; larger n first.
    keyed.sort_by(|a, b| match (a.0, b.0) {
        (Some(x), Some(y)) => y.cmp(&x),
        (Some(_), None) => std::cmp::Ordering::Less,
        (None, Some(_)) => std::cmp::Ordering::Greater,
        (None, None) => std::cmp::Ordering::Equal,
    });
    keyed.into_iter().map(|(_, t)| t).collect()
}

/// One row of the result. `stability`, `difficulty`, `next_review_at` are
/// nullable because never-reviewed cards have no FSRS state yet.
#[derive(Debug, Serialize, FromRow)]
struct DueCardRow {
    card_id: Uuid,
    set_id: Uuid,
    question: String,
    answer: String,
    stability: Option<f64>,
    difficulty: Option<f64>,
    next_review_at: Option<DateTime<Utc>>,
}

/// Response shape returned on 200.
#[derive(Debug, Serialize)]
struct DueResponse {
    due_cards: Vec<DueCardOut>,
    count: i64,
    order: DueOrder,
}

/// Per-card output with `is_new` derived from whether `next_review_at` is NULL.
#[derive(Debug, Serialize)]
struct DueCardOut {
    card_id: Uuid,
    set_id: Uuid,
    question: String,
    answer: String,
    is_new: bool,
    stability: Option<f64>,
    difficulty: Option<f64>,
    next_review_at: Option<DateTime<Utc>>,
}

impl From<DueCardRow> for DueCardOut {
    fn from(r: DueCardRow) -> Self {
        let is_new = r.next_review_at.is_none();
        DueCardOut {
            card_id: r.card_id,
            set_id: r.set_id,
            question: r.question,
            answer: r.answer,
            is_new,
            stability: r.stability,
            difficulty: r.difficulty,
            next_review_at: r.next_review_at,
        }
    }
}

/// Default 20, clamped to [1, 100]. A limit of 0 is silly; it is silently
/// raised to 1 rather than rejected — that saves the caller a round trip and
/// costs nothing, since no caller wants an empty page on purpose.
fn effective_limit(requested: Option<i64>) -> i64 {
    requested.unwrap_or(DEFAULT_LIMIT).clamp(1, MAX_LIMIT)
}

#[get("/due")]
pub async fn due(pool: web::Data<PgPool>, user: AuthedUser, query: web::Query<DueQuery>) -> HttpResponse {
    // 1. Whose queue this is comes from the token, not from the request. A
    //    learner with zero due cards still gets 200 and an empty array — the
    //    same SQL naturally returns [] — because that is not an error.
    let user_id = user.user_id;

    // 2. limit: default 20, clamp to [1, 100]. A limit of 0 is silly;
    //    silently raise it to 1 rather than 400 — saves the caller a round
    //    trip for no benefit, and the spec doesn't mention a 0 case.
    let limit = effective_limit(query.limit);
    let order = match DueOrder::parse(query.order.as_deref()) {
        Ok(o) => o,
        Err(msg) => return error_response(actix_web::http::StatusCode::BAD_REQUEST, msg),
    };

    // 3. Run the due-card query. LATERAL join fetches the latest
    //    learning_events row per (card, user) pair in a single round trip;
    //    the LEFT JOIN lets cards with no events through (NULL columns),
    //    which is how we mark them as `is_new`.
    let rows: Vec<DueCardRow> = match sqlx::query_as::<_, DueCardRow>(DUE_QUERY)
    .bind(user_id)
    .bind(limit)
    .fetch_all(pool.get_ref())
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

    let mut due_cards: Vec<DueCardOut> = rows.into_iter().map(DueCardOut::from).collect();

    // 4. Rearrange the batch if asked. Only the order changes.
    match order {
        DueOrder::Due => {}
        DueOrder::Interleave => due_cards = interleave(due_cards, |c| c.set_id),
        DueOrder::Hardest => {
            let ids: Vec<Uuid> = due_cards.iter().map(|c| c.card_id).collect();
            let recent: Vec<(Uuid, bool, DateTime<Utc>)> =
                match sqlx::query_as(RECENT_ANSWERS_FOR_CARDS_QUERY)
                    .bind(user_id)
                    .bind(&ids)
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
            // Rows come newest first within each card, the order `assess` expects.
            let answers = |card: Uuid| -> Vec<bool> {
                recent.iter().filter(|r| r.0 == card).map(|r| r.1).collect()
            };
            due_cards = hardest_first(due_cards, |c| hardness(&answers(c.card_id)));
        }
    }
    let count = due_cards.len() as i64;

    HttpResponse::Ok().json(DueResponse { due_cards, count, order })
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::handlers::test_db;
    use chrono::Duration;

    // -- pure -----------------------------------------------------------------

    #[test]
    fn limit_defaults_and_clamps() {
        assert_eq!(effective_limit(None), DEFAULT_LIMIT);
        assert_eq!(effective_limit(Some(50)), 50);
        assert_eq!(effective_limit(Some(MAX_LIMIT)), MAX_LIMIT);
        // Above the cap: bounded rather than rejected, so one careless caller
        // cannot ask for the whole table.
        assert_eq!(effective_limit(Some(10_000)), MAX_LIMIT);
        // Zero and negatives mean nobody wants an empty page; give them one card.
        assert_eq!(effective_limit(Some(0)), 1);
        assert_eq!(effective_limit(Some(-5)), 1);
    }

    fn row(next_review_at: Option<DateTime<Utc>>) -> DueCardRow {
        DueCardRow {
            card_id: Uuid::nil(),
            set_id: Uuid::nil(),
            question: "q".into(),
            answer: "a".into(),
            stability: next_review_at.map(|_| 1.5),
            difficulty: next_review_at.map(|_| 5.0),
            next_review_at,
        }
    }

    #[test]
    fn is_new_is_derived_from_the_absence_of_a_schedule() {
        // A card is "new" to the learner exactly when no review has ever set a
        // next date for it. The flag is not stored anywhere; it is this.
        assert!(DueCardOut::from(row(None)).is_new);
        assert!(!DueCardOut::from(row(Some(Utc::now()))).is_new);
    }

    // -- against the real database (see handlers::test_db) ---------------------

    #[tokio::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn due_puts_never_reviewed_cards_before_scheduled_ones() {
        let pool = test_db::pool().await;
        let mut tx = pool.begin().await.unwrap();
        let (user_id, set_id) = test_db::seed_learner(&mut tx).await;

        let seen = test_db::seed_card(&mut tx, set_id, "already reviewed").await;
        let fresh = test_db::seed_card(&mut tx, set_id, "never reviewed").await;
        let now = Utc::now();
        test_db::record_review(&mut tx, seen, user_id, 1.0, 5.0, now - Duration::days(1), now)
            .await;

        let rows: Vec<DueCardRow> = sqlx::query_as(DUE_QUERY)
            .bind(user_id)
            .bind(20_i64)
            .fetch_all(&mut *tx)
            .await
            .unwrap();

        // NULLS FIRST: a card the learner has never seen outranks one that is
        // merely overdue. Deliberate product decision, documented at the top of
        // this module — this test is what keeps it from being "optimised" away.
        let order: Vec<Uuid> = rows.iter().map(|r| r.card_id).collect();
        assert_eq!(order, vec![fresh, seen], "new card should come first");
    }

    #[tokio::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn due_reads_the_latest_review_of_a_card_not_the_first() {
        let pool = test_db::pool().await;
        let mut tx = pool.begin().await.unwrap();
        let (user_id, set_id) = test_db::seed_learner(&mut tx).await;
        let card = test_db::seed_card(&mut tx, set_id, "reviewed twice").await;

        let now = Utc::now();
        // Values chosen far apart so a wrong pick cannot look like rounding.
        test_db::record_review(&mut tx, card, user_id, 9.0, 2.0, now - Duration::days(2), now - Duration::days(2)).await;
        test_db::record_review(&mut tx, card, user_id, 0.5, 8.0, now - Duration::days(1), now - Duration::days(1)).await;

        let rows: Vec<DueCardRow> = sqlx::query_as(DUE_QUERY)
            .bind(user_id)
            .bind(20_i64)
            .fetch_all(&mut *tx)
            .await
            .unwrap();

        assert_eq!(rows.len(), 1);
        // FSRS compounds: scheduling from the first review instead of the
        // latest would reset the learner's progress on every card, silently.
        assert_eq!(rows[0].stability, Some(0.5), "took the wrong review");
        assert_eq!(rows[0].difficulty, Some(8.0));
    }

    #[tokio::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn due_withholds_a_card_scheduled_for_the_future() {
        let pool = test_db::pool().await;
        let mut tx = pool.begin().await.unwrap();
        let (user_id, set_id) = test_db::seed_learner(&mut tx).await;
        let card = test_db::seed_card(&mut tx, set_id, "not due yet").await;

        let now = Utc::now();
        test_db::record_review(&mut tx, card, user_id, 4.0, 5.0, now + Duration::days(5), now)
            .await;

        let rows: Vec<DueCardRow> = sqlx::query_as(DUE_QUERY)
            .bind(user_id)
            .bind(20_i64)
            .fetch_all(&mut *tx)
            .await
            .unwrap();

        // Spaced repetition is the whole product: showing a card before its
        // interval has elapsed is not a harmless extra, it undoes the spacing.
        assert!(rows.is_empty(), "a card due in 5 days must not be offered now");
    }

    #[tokio::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn due_never_shows_another_learners_cards() {
        let pool = test_db::pool().await;
        let mut tx = pool.begin().await.unwrap();
        let (mine, my_set) = test_db::seed_learner(&mut tx).await;
        let (_theirs, their_set) = test_db::seed_learner(&mut tx).await;

        let my_card = test_db::seed_card(&mut tx, my_set, "mine").await;
        test_db::seed_card(&mut tx, their_set, "theirs").await;

        let rows: Vec<DueCardRow> = sqlx::query_as(DUE_QUERY)
            .bind(mine)
            .bind(20_i64)
            .fetch_all(&mut *tx)
            .await
            .unwrap();

        // There is no auth layer, so this WHERE clause is the only thing
        // separating two learners' queues.
        let ids: Vec<Uuid> = rows.iter().map(|r| r.card_id).collect();
        assert_eq!(ids, vec![my_card]);
    }

    // -- ordering: pure ---------------------------------------------------------

    fn set(n: u8) -> Uuid {
        Uuid::from_bytes([n; 16])
    }

    fn adjacent_pairs(sets: &[Uuid]) -> usize {
        sets.windows(2).filter(|w| w[0] == w[1]).count()
    }

    #[test]
    fn order_parses_the_three_names_and_refuses_the_rest() {
        assert_eq!(DueOrder::parse(None), Ok(DueOrder::Due));
        assert_eq!(DueOrder::parse(Some("")), Ok(DueOrder::Due));
        assert_eq!(DueOrder::parse(Some("due")), Ok(DueOrder::Due));
        assert_eq!(DueOrder::parse(Some("interleave")), Ok(DueOrder::Interleave));
        assert_eq!(DueOrder::parse(Some("hardest")), Ok(DueOrder::Hardest));
        assert!(DueOrder::parse(Some("random")).is_err());
        assert!(DueOrder::parse(Some("Hardest")).is_err(), "names are exact");
    }

    #[test]
    fn interleave_separates_sets_and_keeps_every_card() {
        let a = set(1);
        let b = set(2);
        let input = vec![(0, a), (1, a), (2, b), (3, b)];
        let out = interleave(input, |c| c.1);
        assert_eq!(out.iter().map(|c| c.0).collect::<Vec<_>>(), vec![0, 2, 1, 3]);
    }

    #[test]
    fn interleave_leaves_an_already_mixed_batch_alone() {
        let input: Vec<(u32, Uuid)> = vec![(0, set(1)), (1, set(2)), (2, set(1)), (3, set(3))];
        let out = interleave(input.clone(), |c| c.1);
        assert_eq!(out, input);
    }

    #[test]
    fn interleave_of_one_set_is_the_due_order() {
        let input: Vec<(u32, Uuid)> = (0..5).map(|i| (i, set(7))).collect();
        assert_eq!(interleave(input.clone(), |c| c.1), input);
    }

    /// Over many random batches: the output is a permutation of the input, has
    /// no neighbours from the same set whenever the batch allows that, and
    /// otherwise has the fewest runs any arrangement could have.
    #[test]
    fn interleave_is_optimal_on_random_batches() {
        // A small LCG: deterministic, and no dev-dependency for one test.
        let mut seed: u64 = 0x2545_F491_4F6C_DD1D;
        let mut next = |m: u64| {
            seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
            (seed >> 33) % m
        };
        for _ in 0..3000 {
            let n = 1 + next(14) as usize;
            let kinds = 1 + next(4) as u8;
            let input: Vec<(usize, Uuid)> = (0..n).map(|i| (i, set(1 + next(kinds as u64) as u8))).collect();
            let out = interleave(input.clone(), |c| c.1);

            let mut ids: Vec<usize> = out.iter().map(|c| c.0).collect();
            ids.sort_unstable();
            assert_eq!(ids, (0..n).collect::<Vec<_>>(), "not a permutation: {input:?}");

            let mut counts = std::collections::HashMap::new();
            for c in &input {
                *counts.entry(c.1).or_insert(0usize) += 1;
            }
            let max = *counts.values().max().unwrap();
            // The fewest same-set neighbours any order can have.
            let least = (2 * max).saturating_sub(n + 1);
            let sets: Vec<Uuid> = out.iter().map(|c| c.1).collect();
            assert_eq!(adjacent_pairs(&sets), least, "input {input:?} -> {out:?}");
        }
    }

    #[test]
    fn hardness_is_the_weak_card_rule() {
        // Newest first, as `assess` reads it. 2 of 5 is the weak threshold.
        assert_eq!(hardness(&[false, true, false, true, true]), Some(2));
        assert_eq!(hardness(&[true; 5]), Some(0));
        // Four reviews: not judged, however badly they went.
        assert_eq!(hardness(&[false; 4]), None);
    }

    #[test]
    fn hardest_first_puts_most_missed_first_and_keeps_ties_in_order() {
        let input = vec![(0, None), (1, Some(1)), (2, Some(3)), (3, Some(1)), (4, None), (5, Some(0))];
        let out = hardest_first(input, |c| c.1);
        assert_eq!(out.iter().map(|c| c.0).collect::<Vec<_>>(), vec![2, 1, 3, 5, 0, 4]);
    }

    // -- ordering: through the handler, against the real database ---------------

    /// Commits (the handler takes its own connections) and deletes its learner.
    #[actix_web::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn due_order_only_rearranges_the_default_batch() {
        use actix_web::{test, App};

        let pool = test_db::pool().await;
        let mut conn = pool.acquire().await.unwrap();
        let (user, set_a) = test_db::seed_learner(&mut conn).await;
        let set_b: Uuid = sqlx::query_scalar(
            "INSERT INTO study_sets (user_id, name, topic) VALUES ($1, 'second', 't') RETURNING id",
        )
        .bind(user)
        .fetch_one(&mut *conn)
        .await
        .unwrap();
        let now = Utc::now();
        // Set A: a new card and three reviewed ones; set B: two reviewed ones.
        // Answers are oldest first; each card's last answer sets when it is due.
        let mut cards = Vec::new();
        for (set, question, answers, days_overdue) in [
            (set_a, "a-new", vec![], 0),
            (set_a, "a-hard", vec![false, false, false, true, false], 5),
            (set_a, "a-fine", vec![true, true, true, true, true], 4),
            (set_a, "a-weak", vec![true, false, true, false, true], 3),
            (set_b, "b-few", vec![false, false], 2),
            (set_b, "b-once-missed", vec![true, true, false, true, true], 1),
        ] {
            let card = test_db::seed_card(&mut conn, set, question).await;
            let n = answers.len() as i64;
            for (i, ok) in answers.iter().enumerate() {
                let at = now - Duration::days(days_overdue) - Duration::minutes(n - i as i64);
                test_db::record_answer(&mut conn, card, user, *ok, at).await;
            }
            cards.push((card, question));
        }
        drop(conn);
        let token = crate::auth::mint_token(&pool, user, Some("test")).await.unwrap();
        let app = test::init_service(App::new().app_data(web::Data::new(pool.clone())).service(due)).await;

        let mut fetch = |uri: &'static str| {
            let req = test::TestRequest::get()
                .uri(uri)
                .insert_header(("Authorization", format!("Bearer {token}")))
                .to_request();
            test::call_service(&app, req)
        };
        let mut bodies = Vec::new();
        for uri in ["/due", "/due?order=due", "/due?order=interleave", "/due?order=hardest"] {
            let resp = fetch(uri).await;
            assert_eq!(resp.status(), actix_web::http::StatusCode::OK, "{uri}");
            let body: serde_json::Value = test::read_body_json(resp).await;
            bodies.push(body);
        }
        let bad = fetch("/due?order=random").await.status();
        sqlx::query("DELETE FROM users WHERE id = $1").bind(user).execute(&pool).await.unwrap();

        let name = |id: &serde_json::Value| -> &str {
            cards.iter().find(|(c, _)| c.to_string() == id.as_str().unwrap()).unwrap().1
        };
        let names = |b: &serde_json::Value| -> Vec<&str> {
            b["due_cards"].as_array().unwrap().iter().map(|c| name(&c["card_id"])).collect()
        };
        let [plain, due_named, mixed, hard] = [&bodies[0], &bodies[1], &bodies[2], &bodies[3]];

        // Regression: the default is the documented order — new first, then
        // most overdue first — and naming it changes nothing.
        let expected_default = vec!["a-new", "a-hard", "a-fine", "a-weak", "b-few", "b-once-missed"];
        assert_eq!(names(plain), expected_default);
        assert_eq!(names(due_named), expected_default);
        assert_eq!(plain["order"], "due");

        // Interleave: same cards; 4 from A and 2 from B force exactly one pair
        // of A neighbours (A B A B A A), and no more than that.
        let mut sorted_mixed = names(mixed);
        sorted_mixed.sort_unstable();
        let mut sorted_default = expected_default.clone();
        sorted_default.sort_unstable();
        assert_eq!(sorted_mixed, sorted_default, "interleave changed which cards are due");
        let sets: Vec<String> =
            mixed["due_cards"].as_array().unwrap().iter().map(|c| c["set_id"].as_str().unwrap().to_string()).collect();
        assert_eq!(sets.windows(2).filter(|w| w[0] == w[1]).count(), 1, "{:?}", names(mixed));
        assert_eq!(names(mixed), vec!["a-new", "b-few", "a-hard", "b-once-missed", "a-fine", "a-weak"]);

        // Hardest: by misses in the last five, the weak-card rule; cards with
        // under five reviews (new, b-few) keep their due order at the end.
        assert_eq!(names(hard), vec!["a-hard", "a-weak", "b-once-missed", "a-fine", "a-new", "b-few"]);
        assert_eq!(hard["order"], "hardest");

        assert_eq!(bad, actix_web::http::StatusCode::BAD_REQUEST);
    }
}
