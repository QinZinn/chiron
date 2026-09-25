//! Weak-card detection, and the todo item that asks the learner to go back
//! over those cards.
//!
//! Runs at the end of `POST /review`, after the review is stored. When a card
//! keeps being failed, Mnemosyne opens — or extends — one `weak_card` item in
//! the learner's todo list (`todo_items`) for the card's study set. The
//! learner ticks it off in Chiron; nothing schedules it.
//!
//! ## Weak
//!
//! A card is weak for a learner when at least [`WEAK_CARD_ERROR_THRESHOLD`] of
//! their last [`WEAK_CARD_WINDOW`] reviews of it were "again". A card with
//! fewer reviews than that is not judged at all: the first few answers on a
//! new card say nothing yet, and flagging them would file an item after the
//! very first study session.
//!
//! ## One open item per study set
//!
//! The partial unique index `todo_items_open_weak_per_set` allows at most one
//! open weak-card item per set. A weak card is appended to that set's open
//! item (`todo_item_cards`), or opens one. Listing is append-only while the
//! item is open: a card that recovers stays listed. Once the learner ticks the
//! item off, the next weak review in that set opens a new one.
//!
//! The insert is a single `INSERT … ON CONFLICT` against that index, so two
//! reviews landing at once in the same set cannot open two items: the second
//! waits for the first to commit and then joins its item.
//!
//! ## Never breaks the review
//!
//! Nothing here returns an error to the handler. Every outcome, failures
//! included, comes back as a [`WeakCardSync`] and is logged here. The review
//! is already stored by the time this runs; a failure here costs the todo
//! item, never the review, and the next weak review of the card retries.

use sqlx::{Connection, PgConnection, PgPool};
use uuid::Uuid;

/// How many of a card's most recent reviews are judged.
pub const WEAK_CARD_WINDOW: i64 = 5;

/// Share of "again" in the window at which a card counts as weak: 2 of 5.
pub const WEAK_CARD_ERROR_THRESHOLD: f64 = 0.4;

/// Title of the item a weak set gets. The set's name is not baked in: it is
/// joined in when the list is read, so renaming a set renames its item too.
pub const WEAK_TODO_TITLE: &str = "Ôn lại các thẻ đang yếu";

// ---------------------------------------------------------------------------
// Outcomes
// ---------------------------------------------------------------------------

#[derive(Debug, PartialEq, Eq)]
pub enum Weakness {
    /// Fewer than [`WEAK_CARD_WINDOW`] reviews — not judged. Deliberately not
    /// the same thing as `NotWeak`.
    NotEnoughHistory { reviews: usize },
    NotWeak { wrong: usize },
    Weak { wrong: usize },
}

/// What happened to the card that was just reviewed.
#[derive(Debug, PartialEq, Eq)]
pub enum WeakCardSync {
    NotEnoughHistory,
    NotWeak,
    /// Weak, and the set had no open item: one was opened.
    Opened { todo_id: Uuid },
    /// Weak, and appended to the set's open item.
    CardAdded { todo_id: Uuid },
    /// Weak, and already on the set's open item; its `last_weak_card_at` was
    /// bumped.
    AlreadyListed { todo_id: Uuid },
    /// Nothing was recorded; the next weak review retries.
    Failed(String),
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

/// Judge a card from its recent answers, newest first (`true` = recalled).
pub fn assess(recent: &[bool]) -> Weakness {
    let window = WEAK_CARD_WINDOW as usize;
    if recent.len() < window {
        return Weakness::NotEnoughHistory { reviews: recent.len() };
    }
    let wrong = recent.iter().take(window).filter(|correct| !**correct).count();
    if wrong as f64 / window as f64 >= WEAK_CARD_ERROR_THRESHOLD {
        Weakness::Weak { wrong }
    } else {
        Weakness::NotWeak { wrong }
    }
}

// ---------------------------------------------------------------------------
// SQL
// ---------------------------------------------------------------------------

const RECENT_ANSWERS_QUERY: &str = r#"SELECT is_correct
   FROM learning_events
   WHERE card_id = $1 AND user_id = $2
   ORDER BY created_at DESC
   LIMIT $3"#;

/// Open the set's weak-card item, or bump the open one. The conflict target is
/// the partial unique index, so this is the only place the one-open-item rule
/// is enforced — and Postgres enforces it even for concurrent reviews.
/// `inserted` is true when this statement created the row.
const UPSERT_TODO_QUERY: &str = r#"INSERT INTO todo_items (user_id, study_set_id, title, source, last_weak_card_at)
   SELECT s.user_id, s.id, $2, 'weak_card', now()
   FROM cards c
   JOIN study_sets s ON s.id = c.set_id
   WHERE c.id = $1
   ON CONFLICT (study_set_id) WHERE done = false AND source = 'weak_card'
   DO UPDATE SET last_weak_card_at = EXCLUDED.last_weak_card_at
   RETURNING id, (xmax = 0) AS inserted"#;

/// `RETURNING` yields a row only when the card was not listed yet.
const LIST_CARD_QUERY: &str = r#"INSERT INTO todo_item_cards (todo_id, card_id)
   VALUES ($1, $2)
   ON CONFLICT (todo_id, card_id) DO NOTHING
   RETURNING card_id"#;

// ---------------------------------------------------------------------------
// Entry points
// ---------------------------------------------------------------------------

/// Called by `POST /review` once the review is stored. Never fails; see the
/// module docs.
pub async fn sync_after_review(pool: &PgPool, card_id: Uuid, user_id: Uuid) -> WeakCardSync {
    let outcome = match pool.acquire().await {
        Ok(mut conn) => sync_on(&mut conn, card_id, user_id).await,
        Err(e) => WeakCardSync::Failed(format!("database error: {e}")),
    };
    log_outcome(card_id, &outcome);
    outcome
}

/// [`sync_after_review`] on a given connection, so tests can run it inside a
/// transaction they never commit.
pub async fn sync_on(conn: &mut PgConnection, card_id: Uuid, user_id: Uuid) -> WeakCardSync {
    match check_card(conn, card_id, user_id).await {
        Ok(outcome) => outcome,
        Err(e) => WeakCardSync::Failed(format!("database error: {e}")),
    }
}

async fn check_card(
    conn: &mut PgConnection,
    card_id: Uuid,
    user_id: Uuid,
) -> Result<WeakCardSync, sqlx::Error> {
    let recent: Vec<bool> = sqlx::query_scalar(RECENT_ANSWERS_QUERY)
        .bind(card_id)
        .bind(user_id)
        .bind(WEAK_CARD_WINDOW)
        .fetch_all(&mut *conn)
        .await?;
    match assess(&recent) {
        Weakness::NotEnoughHistory { .. } => return Ok(WeakCardSync::NotEnoughHistory),
        Weakness::NotWeak { .. } => return Ok(WeakCardSync::NotWeak),
        Weakness::Weak { .. } => {}
    }

    // The item and its card listing commit together: an item that says a set
    // is weak must name at least the card that made it so.
    let mut tx = conn.begin().await?;
    let (todo_id, inserted): (Uuid, bool) = sqlx::query_as(UPSERT_TODO_QUERY)
        .bind(card_id)
        .bind(WEAK_TODO_TITLE)
        .fetch_one(&mut *tx)
        .await?;
    let newly_listed: Option<Uuid> = sqlx::query_scalar(LIST_CARD_QUERY)
        .bind(todo_id)
        .bind(card_id)
        .fetch_optional(&mut *tx)
        .await?;
    tx.commit().await?;

    Ok(match (inserted, newly_listed.is_some()) {
        (true, _) => WeakCardSync::Opened { todo_id },
        (false, true) => WeakCardSync::CardAdded { todo_id },
        (false, false) => WeakCardSync::AlreadyListed { todo_id },
    })
}

fn log_outcome(card_id: Uuid, outcome: &WeakCardSync) {
    match outcome {
        WeakCardSync::NotEnoughHistory | WeakCardSync::NotWeak => {}
        WeakCardSync::Opened { todo_id } => {
            eprintln!("[weak-cards] card {card_id} is weak; opened todo item {todo_id}")
        }
        WeakCardSync::CardAdded { todo_id } => {
            eprintln!("[weak-cards] card {card_id} is weak; added to todo item {todo_id}")
        }
        WeakCardSync::AlreadyListed { todo_id } => eprintln!(
            "[weak-cards] card {card_id} still weak; already on todo item {todo_id}"
        ),
        WeakCardSync::Failed(reason) => eprintln!(
            "[weak-cards] card {card_id}: nothing recorded, the review itself is stored: {reason}"
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::handlers::test_db;
    use chrono::{Duration, Utc};

    // -- pure -----------------------------------------------------------------

    #[test]
    fn two_misses_in_five_is_weak() {
        // The boundary itself: >= 0.4, not > 0.4.
        assert_eq!(assess(&[false, true, false, true, true]), Weakness::Weak { wrong: 2 });
        assert_eq!(assess(&[false; 5]), Weakness::Weak { wrong: 5 });
    }

    #[test]
    fn one_miss_in_five_is_not_weak() {
        assert_eq!(assess(&[true, true, false, true, true]), Weakness::NotWeak { wrong: 1 });
    }

    #[test]
    fn fewer_than_five_reviews_are_not_judged() {
        // Not "not weak": four straight misses on a new card is no verdict
        // either way, and must not be reported as one.
        assert_eq!(assess(&[false; 4]), Weakness::NotEnoughHistory { reviews: 4 });
        assert_eq!(assess(&[]), Weakness::NotEnoughHistory { reviews: 0 });
    }

    #[test]
    fn only_the_newest_five_count() {
        // Callers pass newest first; old misses beyond the window are history.
        assert_eq!(
            assess(&[true, true, true, true, true, false, false, false]),
            Weakness::NotWeak { wrong: 0 }
        );
    }

    // -- against the real database (see handlers::test_db) ---------------------

    /// Give `card` exactly these answers, oldest first, a minute apart and
    /// ending just now.
    async fn answer(conn: &mut PgConnection, card: Uuid, user: Uuid, answers: &[bool]) {
        let now = Utc::now();
        let n = answers.len() as i64;
        for (i, correct) in answers.iter().enumerate() {
            let at = now - Duration::minutes(n - i as i64);
            test_db::record_answer(conn, card, user, *correct, at).await;
        }
    }

    const WEAK: [bool; 5] = [true, false, true, false, true];

    async fn open_items(conn: &mut PgConnection, set_id: Uuid) -> Vec<Uuid> {
        sqlx::query_scalar(
            "SELECT id FROM todo_items \
             WHERE study_set_id = $1 AND source = 'weak_card' AND NOT done",
        )
        .bind(set_id)
        .fetch_all(&mut *conn)
        .await
        .unwrap()
    }

    async fn listed(conn: &mut PgConnection, todo_id: Uuid) -> i64 {
        sqlx::query_scalar("SELECT count(*) FROM todo_item_cards WHERE todo_id = $1")
            .bind(todo_id)
            .fetch_one(&mut *conn)
            .await
            .unwrap()
    }

    #[tokio::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn weak_cards_one_open_item_per_set_and_no_duplicate_listing() {
        let pool = test_db::pool().await;
        let mut tx = pool.begin().await.unwrap();
        let (user, set) = test_db::seed_learner(&mut tx).await;
        let a = test_db::seed_card(&mut tx, set, "ubiquitous là gì?").await;
        let b = test_db::seed_card(&mut tx, set, "resilience nghĩa gì?").await;

        // Card A turns weak: one item, owned by the set's learner, one card.
        answer(&mut tx, a, user, &WEAK).await;
        let WeakCardSync::Opened { todo_id } = sync_on(&mut tx, a, user).await else {
            panic!("the first weak card must open an item");
        };
        assert_eq!(open_items(&mut tx, set).await, vec![todo_id]);
        assert_eq!(listed(&mut tx, todo_id).await, 1);
        let (owner, title): (Uuid, String) =
            sqlx::query_as("SELECT user_id, title FROM todo_items WHERE id = $1")
                .bind(todo_id)
                .fetch_one(&mut *tx)
                .await
                .unwrap();
        assert_eq!((owner, title.as_str()), (user, WEAK_TODO_TITLE));

        // Card B in the same set: joins the same item instead of opening one.
        answer(&mut tx, b, user, &WEAK).await;
        assert_eq!(sync_on(&mut tx, b, user).await, WeakCardSync::CardAdded { todo_id });
        assert_eq!(open_items(&mut tx, set).await.len(), 1, "a second item was opened");
        assert_eq!(listed(&mut tx, todo_id).await, 2);

        // Card A weak again: already listed — no second row.
        test_db::record_answer(&mut tx, a, user, false, Utc::now()).await;
        assert_eq!(sync_on(&mut tx, a, user).await, WeakCardSync::AlreadyListed { todo_id });
        assert_eq!(listed(&mut tx, todo_id).await, 2);
    }

    #[tokio::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn weak_cards_a_ticked_off_item_is_replaced_by_the_next_weak_review() {
        let pool = test_db::pool().await;
        let mut tx = pool.begin().await.unwrap();
        let (user, set) = test_db::seed_learner(&mut tx).await;
        let card = test_db::seed_card(&mut tx, set, "still failing").await;

        answer(&mut tx, card, user, &WEAK).await;
        let WeakCardSync::Opened { todo_id: first } = sync_on(&mut tx, card, user).await else {
            panic!("expected an item");
        };
        sqlx::query("UPDATE todo_items SET done = true, done_at = now() WHERE id = $1")
            .bind(first)
            .execute(&mut *tx)
            .await
            .unwrap();

        // Done means done: the old item is not reopened, a new one starts.
        test_db::record_answer(&mut tx, card, user, false, Utc::now()).await;
        let WeakCardSync::Opened { todo_id: second } = sync_on(&mut tx, card, user).await else {
            panic!("a weak review after the item was ticked off must open a new one");
        };
        assert_ne!(first, second);
        assert_eq!(open_items(&mut tx, set).await, vec![second]);
        let first_done: bool = sqlx::query_scalar("SELECT done FROM todo_items WHERE id = $1")
            .bind(first)
            .fetch_one(&mut *tx)
            .await
            .unwrap();
        assert!(first_done);
    }

    #[tokio::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn weak_cards_separate_sets_get_separate_items() {
        let pool = test_db::pool().await;
        let mut tx = pool.begin().await.unwrap();
        let (user, set_a) = test_db::seed_learner(&mut tx).await;
        let set_b: Uuid = sqlx::query_scalar(
            "INSERT INTO study_sets (user_id, name, topic) VALUES ($1, 'second', 't') RETURNING id",
        )
        .bind(user)
        .fetch_one(&mut *tx)
        .await
        .unwrap();
        let a = test_db::seed_card(&mut tx, set_a, "a").await;
        let b = test_db::seed_card(&mut tx, set_b, "b").await;
        answer(&mut tx, a, user, &WEAK).await;
        answer(&mut tx, b, user, &WEAK).await;

        assert!(matches!(sync_on(&mut tx, a, user).await, WeakCardSync::Opened { .. }));
        assert!(matches!(sync_on(&mut tx, b, user).await, WeakCardSync::Opened { .. }));
        assert_eq!(open_items(&mut tx, set_a).await.len(), 1);
        assert_eq!(open_items(&mut tx, set_b).await.len(), 1);
    }

    /// Commits, because a race needs two connections that see each other's
    /// rows; deletes its learner at the end.
    #[tokio::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn weak_cards_concurrent_weak_cards_in_one_set_share_one_item() {
        let pool = test_db::pool().await;
        let mut conn = pool.acquire().await.unwrap();
        let (user, set) = test_db::seed_learner(&mut conn).await;
        let a = test_db::seed_card(&mut conn, set, "race a").await;
        let b = test_db::seed_card(&mut conn, set, "race b").await;
        answer(&mut conn, a, user, &WEAK).await;
        answer(&mut conn, b, user, &WEAK).await;
        drop(conn);

        let (mut c1, mut c2) = (pool.acquire().await.unwrap(), pool.acquire().await.unwrap());
        let (r1, r2) = tokio::join!(sync_on(&mut c1, a, user), sync_on(&mut c2, b, user));
        drop((c1, c2));

        let mut conn = pool.acquire().await.unwrap();
        let open = open_items(&mut conn, set).await;
        let cards = if let [id] = open[..] { listed(&mut conn, id).await } else { -1 };
        sqlx::query("DELETE FROM users WHERE id = $1").bind(user).execute(&mut *conn).await.unwrap();

        assert!(!matches!(r1, WeakCardSync::Failed(_)), "{r1:?}");
        assert!(!matches!(r2, WeakCardSync::Failed(_)), "{r2:?}");
        assert_eq!(open.len(), 1, "two items were opened: {r1:?} {r2:?}");
        assert_eq!(cards, 2, "one card's review filed nothing: {r1:?} {r2:?}");
    }

    #[tokio::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn weak_cards_four_misses_or_one_in_five_file_nothing() {
        let pool = test_db::pool().await;
        let mut tx = pool.begin().await.unwrap();
        let (user, set) = test_db::seed_learner(&mut tx).await;
        let new_card = test_db::seed_card(&mut tx, set, "new card").await;
        let fine_card = test_db::seed_card(&mut tx, set, "fine card").await;

        answer(&mut tx, new_card, user, &[false; 4]).await;
        assert_eq!(sync_on(&mut tx, new_card, user).await, WeakCardSync::NotEnoughHistory);
        answer(&mut tx, fine_card, user, &[true, true, false, true, true]).await;
        assert_eq!(sync_on(&mut tx, fine_card, user).await, WeakCardSync::NotWeak);
        assert!(open_items(&mut tx, set).await.is_empty());
    }
}
