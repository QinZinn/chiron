//! Weak-card detection, and the Todoist task that asks the learner to go back
//! over those cards.
//!
//! Runs at the end of `POST /review`, after the review is stored. When a card
//! keeps being failed, Mnemosyne files — or extends — one `@ontap` task in
//! Todoist for the card's study set. Horae reads Todoist like any other task
//! list and schedules it. The two modules never call each other; Todoist is
//! the whole interface between them.
//!
//! ## Weak
//!
//! A card is weak for a learner when at least [`WEAK_CARD_ERROR_THRESHOLD`] of
//! their last [`WEAK_CARD_WINDOW`] reviews of it were "again". A card with
//! fewer reviews than that is not judged at all: the first few answers on a
//! new card say nothing yet, and flagging them would file a task after the
//! very first study session.
//!
//! ## One open task per study set
//!
//! `weak_card_tasks` holds at most one open row per set (a partial unique
//! index enforces it). A weak card is appended to that set's open task, or
//! opens one. Listing is append-only for the life of the task: a card that
//! recovers stays listed. The task closes itself — on Todoist too — once
//! [`WEAK_TASK_AUTO_CLOSE_DAYS`] pass with no weak review in the set. Every
//! weak review keeps the cycle alive, including one of a card already listed:
//! a card the learner is still failing is exactly what the task is for.
//!
//! The expiry sweep runs on every review, not only reviews in the expiring
//! set. A set the learner has abandoned produces no reviews of its own, so
//! checking only there would leave its task on the calendar forever.
//!
//! ## Never breaks the review
//!
//! Nothing here returns an error to the handler. Every outcome, failures
//! included, comes back as a [`WeakCardSync`] and is logged here. The review
//! is already stored by the time this runs; a Todoist outage costs the
//! reminder, never the review.
//!
//! ## Consistency
//!
//! All work on one set's task runs in a transaction holding a per-set
//! advisory lock, Todoist calls included. That serialises two reviews landing
//! at once in the same set, which would otherwise create two tasks, or each
//! write a description missing the other's card. And if a Todoist call fails,
//! the transaction rolls back, so the database never records a card that
//! Todoist does not show. The same review, or the next weak one, simply tries
//! again. The price is holding the lock for one Todoist round-trip (bounded by
//! the client's timeout), which in a 2–3 user app is nothing.

use chrono::{DateTime, FixedOffset, Utc};
use sqlx::{Connection, FromRow, PgConnection, PgPool};
use uuid::Uuid;

use crate::todoist_client::{RemoteTaskState, TodoistApi, TodoistError, ONTAP_LABEL};

/// How many of a card's most recent reviews are judged.
pub const WEAK_CARD_WINDOW: i64 = 5;

/// Share of "again" in the window at which a card counts as weak: 2 of 5.
pub const WEAK_CARD_ERROR_THRESHOLD: f64 = 0.4;

/// A set's task closes after this many days with no weak review in it.
pub const WEAK_TASK_AUTO_CLOSE_DAYS: i32 = 5;

/// Daily review time asked for per listed card, and the floor and cap on the
/// total. The cap keeps one struggling set from taking Horae's whole review
/// budget for the day.
const MINUTES_PER_WEAK_CARD: usize = 10;
const MIN_DAILY_MINUTES: usize = 20;
const MAX_DAILY_MINUTES: usize = 60;

/// Questions are shortened to this many characters in the task description.
const QUESTION_PREVIEW_CHARS: usize = 80;

/// At most this many cards are spelled out in the description; the rest are
/// counted. Keeps the description far below Todoist's size limits however
/// many cards a set accumulates.
const MAX_LISTED_CARDS: usize = 50;

/// Dates in the description are shown in Vietnam time. Asia/Ho_Chi_Minh has
/// no daylight saving, so a fixed offset is exact, and it saves a timezone
/// database dependency. A UTC date would be a day behind for every card that
/// turns weak between midnight and 07:00.
const DISPLAY_UTC_OFFSET_SECS: i32 = 7 * 3600;

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

#[derive(Debug)]
pub enum SyncFailure {
    Db(String),
    Todoist(TodoistError),
}

impl From<sqlx::Error> for SyncFailure {
    fn from(e: sqlx::Error) -> Self {
        SyncFailure::Db(e.to_string())
    }
}

impl std::fmt::Display for SyncFailure {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SyncFailure::Db(m) => write!(f, "database error: {m}"),
            SyncFailure::Todoist(e) => write!(f, "{}", describe_todoist_error(e)),
        }
    }
}

/// What happened to the card that was just reviewed.
#[derive(Debug)]
pub enum CardOutcome {
    NotEnoughHistory { reviews: usize },
    NotWeak,
    /// Weak, and already on the set's open task. Its cycle was kept alive; no
    /// Todoist call was needed.
    AlreadyListed { todoist_task_id: String },
    /// Weak, and the set had no open task: one was created. `replaced` is the
    /// task it supersedes when the old one turned out to have been completed
    /// or deleted in Todoist.
    TaskOpened { todoist_task_id: String, replaced: Option<String> },
    /// Weak, and appended to the set's open task.
    CardAdded { todoist_task_id: String },
    /// Nothing was recorded; the next weak review retries.
    Failed(SyncFailure),
}

#[derive(Debug, Default)]
pub struct SweepOutcome {
    /// Todoist ids of tasks closed for going quiet.
    pub closed: Vec<String>,
    /// Tasks that should have closed but did not; the next sweep retries.
    pub failed: Vec<SyncFailure>,
}

#[derive(Debug)]
pub enum WeakCardSync {
    /// No `TODOIST_TOKEN` — the feature is off.
    Disabled,
    Ran { sweep: SweepOutcome, card: CardOutcome },
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

fn daily_minutes(card_count: usize) -> usize {
    (card_count * MINUTES_PER_WEAK_CARD).clamp(MIN_DAILY_MINUTES, MAX_DAILY_MINUTES)
}

/// The task title. `[Nm/ngày]` is what Horae reads as the daily target of an
/// ongoing task. The `@ontap` label is **not** written here — Horae reads
/// labels from the task's `labels` field, and a title containing "@ontap"
/// would leave the task unlabelled.
fn task_content(set_name: &str, card_count: usize) -> String {
    format!("Ôn thẻ yếu — {set_name} [{}m/ngày]", daily_minutes(card_count))
}

/// One line, at most [`QUESTION_PREVIEW_CHARS`] characters, so a long or
/// multi-line question cannot swell the description.
fn question_preview(question: &str) -> String {
    let flat = question.split_whitespace().collect::<Vec<_>>().join(" ");
    if flat.chars().count() <= QUESTION_PREVIEW_CHARS {
        flat
    } else {
        let cut: String = flat.chars().take(QUESTION_PREVIEW_CHARS).collect();
        format!("{}…", cut.trim_end())
    }
}

fn display_date(at: DateTime<Utc>) -> String {
    let offset = FixedOffset::east_opt(DISPLAY_UTC_OFFSET_SECS).expect("offset is in range");
    at.with_timezone(&offset).format("%d/%m/%Y").to_string()
}

fn task_description(set_name: &str, cards: &[ListedCard]) -> String {
    let mut out = format!("Các thẻ đang yếu trong set \"{set_name}\":\n");
    for card in cards.iter().take(MAX_LISTED_CARDS) {
        out.push_str(&format!(
            "- Q: \"{}\" (yếu từ {})\n",
            question_preview(&card.question),
            display_date(card.added_at)
        ));
    }
    if cards.len() > MAX_LISTED_CARDS {
        out.push_str(&format!("- … và {} thẻ khác\n", cards.len() - MAX_LISTED_CARDS));
    }
    out.push_str(&format!(
        "\nMnemosyne tự tạo task này và tự đóng nó sau {WEAK_TASK_AUTO_CLOSE_DAYS} ngày \
         không có thẻ yếu mới trong set."
    ));
    out
}

/// The log line for a Todoist failure. Transient and permanent failures must
/// not read alike: the first heals itself on the next review, the second —
/// a rejected token — fails on every review until someone fixes `.env`.
fn describe_todoist_error(e: &TodoistError) -> String {
    match e {
        e if e.is_permanent() => format!(
            "PERMANENT — Todoist rejected the token; check TODOIST_TOKEN in .env. \
             This will fail on every review until it is fixed: {e}"
        ),
        TodoistError::Parse(_) => format!(
            "Todoist answered with a body this client does not understand (API change?) \
             — will retry on the next trigger: {e}"
        ),
        _ => format!("transient — will retry on the next trigger: {e}"),
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

const CARD_QUERY: &str = r#"SELECT c.set_id, c.question, s.name AS set_name
   FROM cards c
   JOIN study_sets s ON s.id = c.set_id
   WHERE c.id = $1"#;

/// Serialise all weak-card work on one set. Released on commit or rollback.
/// Two sets hashing to the same key only wait on each other — harmless.
const LOCK_SET_QUERY: &str = "SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))";

const OPEN_TASK_QUERY: &str = r#"SELECT id, todoist_task_id
   FROM weak_card_tasks
   WHERE study_set_id = $1 AND closed_at IS NULL"#;

const INSERT_TASK_QUERY: &str = r#"INSERT INTO weak_card_tasks (study_set_id, todoist_task_id, opened_at, last_weak_card_at)
   VALUES ($1, $2, $3, $3)
   RETURNING id"#;

/// `RETURNING` yields a row only when the card was not listed yet.
const LIST_CARD_QUERY: &str = r#"INSERT INTO weak_card_task_cards (task_row_id, card_id, added_at)
   VALUES ($1, $2, $3)
   ON CONFLICT (task_row_id, card_id) DO NOTHING
   RETURNING card_id"#;

const BUMP_TASK_QUERY: &str =
    "UPDATE weak_card_tasks SET last_weak_card_at = $2 WHERE id = $1";

const CLOSE_TASK_ROW_QUERY: &str =
    "UPDATE weak_card_tasks SET closed_at = now() WHERE id = $1 AND closed_at IS NULL";

const LISTED_CARDS_QUERY: &str = r#"SELECT c.question, wc.added_at
   FROM weak_card_task_cards wc
   JOIN cards c ON c.id = wc.card_id
   WHERE wc.task_row_id = $1
   ORDER BY wc.added_at, c.id"#;

const EXPIRED_TASKS_QUERY: &str = r#"SELECT id, study_set_id, todoist_task_id
   FROM weak_card_tasks
   WHERE closed_at IS NULL
     AND last_weak_card_at < now() - make_interval(days => $1)"#;

/// Re-check under the set lock: a weak review may have revived the task
/// between the sweep's scan and this point.
const STILL_EXPIRED_QUERY: &str = r#"SELECT todoist_task_id
   FROM weak_card_tasks
   WHERE id = $1
     AND closed_at IS NULL
     AND last_weak_card_at < now() - make_interval(days => $2)"#;

#[derive(Debug, FromRow)]
struct CardRow {
    set_id: Uuid,
    question: String,
    set_name: String,
}

#[derive(Debug, FromRow)]
struct OpenTaskRow {
    id: Uuid,
    todoist_task_id: String,
}

#[derive(Debug, FromRow)]
struct ListedCard {
    question: String,
    added_at: DateTime<Utc>,
}

#[derive(Debug, FromRow)]
struct ExpiredTaskRow {
    id: Uuid,
    study_set_id: Uuid,
    todoist_task_id: String,
}

// ---------------------------------------------------------------------------
// Entry points
// ---------------------------------------------------------------------------

/// Called by `POST /review` once the review is stored. Never fails; see the
/// module docs.
pub async fn sync_after_review(
    pool: &PgPool,
    todoist: Option<&dyn TodoistApi>,
    card_id: Uuid,
    user_id: Uuid,
) -> WeakCardSync {
    let Some(todoist) = todoist else {
        return WeakCardSync::Disabled;
    };
    let mut conn = match pool.acquire().await {
        Ok(conn) => conn,
        Err(e) => {
            let failure = SyncFailure::from(e);
            eprintln!("[weak-cards] skipped for card {card_id}: {failure}");
            return WeakCardSync::Ran {
                sweep: SweepOutcome::default(),
                card: CardOutcome::Failed(failure),
            };
        }
    };
    sync_on(&mut conn, todoist, card_id, user_id).await
}

/// [`sync_after_review`] on a given connection, so tests can run it inside a
/// transaction they never commit.
pub async fn sync_on(
    conn: &mut PgConnection,
    todoist: &dyn TodoistApi,
    card_id: Uuid,
    user_id: Uuid,
) -> WeakCardSync {
    // The card first: if it is weak it extends its set's task, and the sweep
    // then no longer finds that task quiet. The other way round, a task whose
    // card is failing right now would be closed and immediately reopened as a
    // new Todoist task.
    let card = match check_card(conn, todoist, card_id, user_id).await {
        Ok(outcome) => outcome,
        Err(failure) => CardOutcome::Failed(failure),
    };
    log_card_outcome(card_id, &card);
    let sweep = sweep_quiet_tasks(conn, todoist).await;
    WeakCardSync::Ran { sweep, card }
}

// ---------------------------------------------------------------------------
// Sweep
// ---------------------------------------------------------------------------

async fn sweep_quiet_tasks(conn: &mut PgConnection, todoist: &dyn TodoistApi) -> SweepOutcome {
    let mut outcome = SweepOutcome::default();
    let expired: Vec<ExpiredTaskRow> = match sqlx::query_as(EXPIRED_TASKS_QUERY)
        .bind(WEAK_TASK_AUTO_CLOSE_DAYS)
        .fetch_all(&mut *conn)
        .await
    {
        Ok(rows) => rows,
        Err(e) => {
            let failure = SyncFailure::from(e);
            eprintln!("[weak-cards] sweep could not list quiet tasks: {failure}");
            outcome.failed.push(failure);
            return outcome;
        }
    };

    for row in expired {
        match close_quiet_task(conn, todoist, &row).await {
            Ok(true) => {
                eprintln!(
                    "[weak-cards] closed Todoist task {} (set {}): no weak card for \
                     {WEAK_TASK_AUTO_CLOSE_DAYS} days",
                    row.todoist_task_id, row.study_set_id
                );
                outcome.closed.push(row.todoist_task_id);
            }
            Ok(false) => {} // revived or closed by a concurrent request
            Err(failure) => {
                eprintln!(
                    "[weak-cards] could not close quiet Todoist task {} (set {}); it stays \
                     open and the next review retries: {failure}",
                    row.todoist_task_id, row.study_set_id
                );
                outcome.failed.push(failure);
            }
        }
    }
    outcome
}

/// Close one quiet task under its set's lock. `Ok(false)` when it no longer
/// qualifies.
async fn close_quiet_task(
    conn: &mut PgConnection,
    todoist: &dyn TodoistApi,
    row: &ExpiredTaskRow,
) -> Result<bool, SyncFailure> {
    let mut tx = conn.begin().await?;
    sqlx::query(LOCK_SET_QUERY).bind(row.study_set_id).execute(&mut *tx).await?;

    let still: Option<String> = sqlx::query_scalar(STILL_EXPIRED_QUERY)
        .bind(row.id)
        .bind(WEAK_TASK_AUTO_CLOSE_DAYS)
        .fetch_optional(&mut *tx)
        .await?;
    let Some(todoist_task_id) = still else {
        return Ok(false);
    };

    match todoist.close_task(&todoist_task_id).await {
        Ok(()) => {}
        // Deleted in Todoist already: as closed as it will ever be.
        Err(e) if e.is_not_found() => {}
        Err(e) => return Err(SyncFailure::Todoist(e)),
    }
    sqlx::query(CLOSE_TASK_ROW_QUERY).bind(row.id).execute(&mut *tx).await?;
    tx.commit().await?;
    Ok(true)
}

// ---------------------------------------------------------------------------
// The reviewed card
// ---------------------------------------------------------------------------

async fn check_card(
    conn: &mut PgConnection,
    todoist: &dyn TodoistApi,
    card_id: Uuid,
    user_id: Uuid,
) -> Result<CardOutcome, SyncFailure> {
    let recent: Vec<bool> = sqlx::query_scalar(RECENT_ANSWERS_QUERY)
        .bind(card_id)
        .bind(user_id)
        .bind(WEAK_CARD_WINDOW)
        .fetch_all(&mut *conn)
        .await?;
    match assess(&recent) {
        Weakness::NotEnoughHistory { reviews } => {
            return Ok(CardOutcome::NotEnoughHistory { reviews })
        }
        Weakness::NotWeak { .. } => return Ok(CardOutcome::NotWeak),
        Weakness::Weak { .. } => {}
    }

    let card: CardRow = sqlx::query_as(CARD_QUERY).bind(card_id).fetch_one(&mut *conn).await?;
    let now = Utc::now();

    let mut tx = conn.begin().await?;
    sqlx::query(LOCK_SET_QUERY).bind(card.set_id).execute(&mut *tx).await?;

    let open: Option<OpenTaskRow> =
        sqlx::query_as(OPEN_TASK_QUERY).bind(card.set_id).fetch_optional(&mut *tx).await?;

    let outcome = match open {
        None => open_task(&mut tx, todoist, &card, card_id, now, None).await?,
        Some(task) => {
            sqlx::query(BUMP_TASK_QUERY).bind(task.id).bind(now).execute(&mut *tx).await?;
            let newly_listed: Option<Uuid> = sqlx::query_scalar(LIST_CARD_QUERY)
                .bind(task.id)
                .bind(card_id)
                .bind(now)
                .fetch_optional(&mut *tx)
                .await?;
            if newly_listed.is_none() {
                CardOutcome::AlreadyListed { todoist_task_id: task.todoist_task_id }
            } else {
                let cards: Vec<ListedCard> =
                    sqlx::query_as(LISTED_CARDS_QUERY).bind(task.id).fetch_all(&mut *tx).await?;
                let content = task_content(&card.set_name, cards.len());
                let description = task_description(&card.set_name, &cards);
                match todoist.update_task(&task.todoist_task_id, &content, &description).await {
                    Ok(RemoteTaskState::Open) => {
                        CardOutcome::CardAdded { todoist_task_id: task.todoist_task_id }
                    }
                    // Completed or deleted in Todoist by hand. It schedules
                    // nothing any more, so end this cycle and open a new one
                    // holding just this card.
                    Ok(RemoteTaskState::Gone) => {
                        sqlx::query(CLOSE_TASK_ROW_QUERY).bind(task.id).execute(&mut *tx).await?;
                        open_task(&mut tx, todoist, &card, card_id, now, Some(task.todoist_task_id))
                            .await?
                    }
                    Err(e) if e.is_not_found() => {
                        sqlx::query(CLOSE_TASK_ROW_QUERY).bind(task.id).execute(&mut *tx).await?;
                        open_task(&mut tx, todoist, &card, card_id, now, Some(task.todoist_task_id))
                            .await?
                    }
                    // Dropping `tx` rolls the listing back, so the next weak
                    // review of this card tries again.
                    Err(e) => return Err(SyncFailure::Todoist(e)),
                }
            }
        }
    };

    if let Err(e) = tx.commit().await {
        // A task opened in this transaction now exists in Todoist with no row
        // pointing at it, and nothing would ever close it.
        if let CardOutcome::TaskOpened { todoist_task_id, .. } = &outcome {
            close_orphan(todoist, todoist_task_id).await;
        }
        return Err(e.into());
    }
    Ok(outcome)
}

/// Create the set's task in Todoist and record it, inside the caller's
/// transaction.
async fn open_task(
    tx: &mut PgConnection,
    todoist: &dyn TodoistApi,
    card: &CardRow,
    card_id: Uuid,
    now: DateTime<Utc>,
    replaced: Option<String>,
) -> Result<CardOutcome, SyncFailure> {
    let listed = [ListedCard { question: card.question.clone(), added_at: now }];
    let content = task_content(&card.set_name, listed.len());
    let description = task_description(&card.set_name, &listed);
    let todoist_task_id = todoist
        .create_task(&content, &description, &[ONTAP_LABEL])
        .await
        .map_err(SyncFailure::Todoist)?;

    let recorded: Result<(), sqlx::Error> = async {
        let task_row_id: Uuid = sqlx::query_scalar(INSERT_TASK_QUERY)
            .bind(card.set_id)
            .bind(&todoist_task_id)
            .bind(now)
            .fetch_one(&mut *tx)
            .await?;
        sqlx::query(LIST_CARD_QUERY)
            .bind(task_row_id)
            .bind(card_id)
            .bind(now)
            .execute(&mut *tx)
            .await?;
        Ok(())
    }
    .await;

    if let Err(e) = recorded {
        close_orphan(todoist, &todoist_task_id).await;
        return Err(e.into());
    }
    Ok(CardOutcome::TaskOpened { todoist_task_id, replaced })
}

/// Best effort: a task the database failed to record would otherwise be
/// scheduled by Horae indefinitely.
async fn close_orphan(todoist: &dyn TodoistApi, todoist_task_id: &str) {
    match todoist.close_task(todoist_task_id).await {
        Ok(()) => eprintln!(
            "[weak-cards] closed Todoist task {todoist_task_id} again: the database did not record it"
        ),
        Err(e) => eprintln!(
            "[weak-cards] ORPHAN Todoist task {todoist_task_id}: the database did not record it and \
             closing it failed ({}). Close it by hand, or Horae keeps scheduling it.",
            describe_todoist_error(&e)
        ),
    }
}

fn log_card_outcome(card_id: Uuid, outcome: &CardOutcome) {
    match outcome {
        CardOutcome::NotEnoughHistory { .. } | CardOutcome::NotWeak => {}
        CardOutcome::AlreadyListed { todoist_task_id } => eprintln!(
            "[weak-cards] card {card_id} still weak; already on Todoist task {todoist_task_id}, cycle extended"
        ),
        CardOutcome::TaskOpened { todoist_task_id, replaced: None } => eprintln!(
            "[weak-cards] card {card_id} is weak; opened Todoist task {todoist_task_id}"
        ),
        CardOutcome::TaskOpened { todoist_task_id, replaced: Some(old) } => eprintln!(
            "[weak-cards] card {card_id} is weak; Todoist task {old} was completed or deleted by hand, \
             opened {todoist_task_id} in its place"
        ),
        CardOutcome::CardAdded { todoist_task_id } => eprintln!(
            "[weak-cards] card {card_id} is weak; added to Todoist task {todoist_task_id}"
        ),
        CardOutcome::Failed(failure) => eprintln!(
            "[weak-cards] card {card_id}: nothing recorded, the review itself is stored: {failure}"
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::handlers::test_db;
    use crate::todoist_client::fake::{Call, FakeTodoist};
    use chrono::Duration;

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

    #[test]
    fn daily_minutes_are_floored_and_capped() {
        assert_eq!(daily_minutes(1), 20);
        assert_eq!(daily_minutes(2), 20);
        assert_eq!(daily_minutes(4), 40);
        assert_eq!(daily_minutes(8), 60);
        assert_eq!(daily_minutes(30), 60);
    }

    #[test]
    fn the_title_carries_the_daily_target_and_no_label() {
        let content = task_content("Từ vựng Unit 5", 4);
        assert_eq!(content, "Ôn thẻ yếu — Từ vựng Unit 5 [40m/ngày]");
        assert!(!content.contains('@'), "the label belongs in `labels`, not the title");
    }

    #[test]
    fn long_and_multiline_questions_are_shortened_to_one_line() {
        assert_eq!(question_preview("ubiquitous\n  là gì?"), "ubiquitous là gì?");
        let long = "a ".repeat(100);
        let preview = question_preview(&long);
        assert!(preview.ends_with('…'));
        assert!(preview.chars().count() <= QUESTION_PREVIEW_CHARS + 1);
    }

    #[test]
    fn dates_are_shown_in_vietnam_time() {
        // 18:30 UTC on the 12th is already the 13th in Hà Nội.
        let at = DateTime::parse_from_rfc3339("2026-09-12T18:30:00Z").unwrap().with_timezone(&Utc);
        assert_eq!(display_date(at), "13/09/2026");
    }

    #[test]
    fn the_description_lists_cards_and_counts_the_overflow() {
        let at = DateTime::parse_from_rfc3339("2026-09-13T03:00:00Z").unwrap().with_timezone(&Utc);
        let cards: Vec<ListedCard> = (0..MAX_LISTED_CARDS + 3)
            .map(|i| ListedCard { question: format!("q{i}"), added_at: at })
            .collect();
        let description = task_description("Unit 5", &cards);
        assert!(description.starts_with("Các thẻ đang yếu trong set \"Unit 5\":\n"));
        assert!(description.contains("- Q: \"q0\" (yếu từ 13/09/2026)"));
        assert!(!description.contains(&format!("\"q{MAX_LISTED_CARDS}\"")));
        assert!(description.contains("và 3 thẻ khác"));
    }

    #[test]
    fn a_rejected_token_is_logged_as_permanent_and_a_timeout_is_not() {
        let auth = describe_todoist_error(&TodoistError::Http { status: 401, body: String::new() });
        assert!(auth.starts_with("PERMANENT"), "{auth}");
        assert!(auth.contains("TODOIST_TOKEN"));
        let blip = describe_todoist_error(&TodoistError::Unreachable("timeout".into()));
        assert!(blip.starts_with("transient"), "{blip}");
        let slow = describe_todoist_error(&TodoistError::Http { status: 503, body: String::new() });
        assert!(slow.starts_with("transient"), "{slow}");
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

    async fn open_rows(conn: &mut PgConnection, set_id: Uuid) -> Vec<Uuid> {
        sqlx::query_scalar(
            "SELECT id FROM weak_card_tasks WHERE study_set_id = $1 AND closed_at IS NULL",
        )
        .bind(set_id)
        .fetch_all(&mut *conn)
        .await
        .unwrap()
    }

    async fn listed(conn: &mut PgConnection, set_id: Uuid) -> i64 {
        sqlx::query_scalar(
            "SELECT count(*) FROM weak_card_task_cards wc \
             JOIN weak_card_tasks w ON w.id = wc.task_row_id \
             WHERE w.study_set_id = $1 AND w.closed_at IS NULL",
        )
        .bind(set_id)
        .fetch_one(&mut *conn)
        .await
        .unwrap()
    }

    fn card_outcome(sync: WeakCardSync) -> CardOutcome {
        match sync {
            WeakCardSync::Ran { card, .. } => card,
            WeakCardSync::Disabled => panic!("a configured client never reports Disabled"),
        }
    }

    #[tokio::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn weak_cards_one_open_task_per_set_and_no_duplicate_listing() {
        let pool = test_db::pool().await;
        let mut tx = pool.begin().await.unwrap();
        let (user, set) = test_db::seed_learner(&mut tx).await;
        let a = test_db::seed_card(&mut tx, set, "ubiquitous là gì?").await;
        let b = test_db::seed_card(&mut tx, set, "resilience nghĩa gì?").await;
        let todoist = FakeTodoist::default();

        // Card A turns weak: one task, one listed card, labelled via `labels`.
        answer(&mut tx, a, user, &WEAK).await;
        let outcome = card_outcome(sync_on(&mut tx, &todoist, a, user).await);
        assert!(matches!(outcome, CardOutcome::TaskOpened { replaced: None, .. }), "{outcome:?}");
        assert_eq!(open_rows(&mut tx, set).await.len(), 1);
        assert_eq!(listed(&mut tx, set).await, 1);
        match &todoist.calls()[..] {
            [Call::Create { content, description, labels }] => {
                assert_eq!(content, "Ôn thẻ yếu — test set [20m/ngày]");
                assert_eq!(labels, &vec!["@ontap".to_string()]);
                assert!(description.contains("ubiquitous là gì?"));
            }
            other => panic!("expected one create, got {other:?}"),
        }

        // Card B in the same set: joins the same task instead of opening one.
        answer(&mut tx, b, user, &WEAK).await;
        let outcome = card_outcome(sync_on(&mut tx, &todoist, b, user).await);
        assert!(matches!(outcome, CardOutcome::CardAdded { .. }), "{outcome:?}");
        assert_eq!(open_rows(&mut tx, set).await.len(), 1, "a second task was opened");
        assert_eq!(listed(&mut tx, set).await, 2);
        match todoist.calls().last() {
            Some(Call::Update { description, .. }) => {
                assert!(description.contains("ubiquitous") && description.contains("resilience"));
            }
            other => panic!("expected an update, got {other:?}"),
        }

        // Card A weak again: already listed — no second row, no Todoist call.
        let calls_before = todoist.calls().len();
        test_db::record_answer(&mut tx, a, user, false, Utc::now()).await;
        let outcome = card_outcome(sync_on(&mut tx, &todoist, a, user).await);
        assert!(matches!(outcome, CardOutcome::AlreadyListed { .. }), "{outcome:?}");
        assert_eq!(listed(&mut tx, set).await, 2);
        assert_eq!(todoist.calls().len(), calls_before);
    }

    #[tokio::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn weak_cards_a_quiet_task_is_closed_by_a_review_in_another_set() {
        let pool = test_db::pool().await;
        let mut tx = pool.begin().await.unwrap();
        let (user, set) = test_db::seed_learner(&mut tx).await;
        let card = test_db::seed_card(&mut tx, set, "abandoned").await;
        let todoist = FakeTodoist::default();

        answer(&mut tx, card, user, &WEAK).await;
        sync_on(&mut tx, &todoist, card, user).await;
        let row = open_rows(&mut tx, set).await[0];
        sqlx::query("UPDATE weak_card_tasks SET last_weak_card_at = now() - interval '6 days' WHERE id = $1")
            .bind(row)
            .execute(&mut *tx)
            .await
            .unwrap();

        // Any review at all — here one in a different set, not even weak.
        let (other_user, other_set) = test_db::seed_learner(&mut tx).await;
        let other = test_db::seed_card(&mut tx, other_set, "unrelated").await;
        answer(&mut tx, other, other_user, &[true]).await;
        let WeakCardSync::Ran { sweep, .. } = sync_on(&mut tx, &todoist, other, other_user).await
        else {
            panic!("expected Ran")
        };

        let closed_at: Option<DateTime<Utc>> =
            sqlx::query_scalar("SELECT closed_at FROM weak_card_tasks WHERE id = $1")
                .bind(row)
                .fetch_one(&mut *tx)
                .await
                .unwrap();
        assert!(closed_at.is_some(), "the quiet task was left open");
        assert_eq!(sweep.closed.len(), 1);
        assert!(matches!(todoist.calls().last(), Some(Call::Close { .. })));
    }

    #[tokio::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn weak_cards_a_card_still_failing_keeps_its_task_open() {
        // A listed card failing again is a weak review like any other: it
        // extends the cycle. Even on a task already past its quiet period, the
        // same task carries on — not closed and reopened as a new one.
        let pool = test_db::pool().await;
        let mut tx = pool.begin().await.unwrap();
        let (user, set) = test_db::seed_learner(&mut tx).await;
        let card = test_db::seed_card(&mut tx, set, "still failing").await;
        let todoist = FakeTodoist::default();

        answer(&mut tx, card, user, &WEAK).await;
        sync_on(&mut tx, &todoist, card, user).await;
        let row = open_rows(&mut tx, set).await[0];
        sqlx::query("UPDATE weak_card_tasks SET last_weak_card_at = now() - interval '6 days' WHERE id = $1")
            .bind(row)
            .execute(&mut *tx)
            .await
            .unwrap();

        test_db::record_answer(&mut tx, card, user, false, Utc::now()).await;
        let WeakCardSync::Ran { sweep, card: outcome } = sync_on(&mut tx, &todoist, card, user).await
        else {
            panic!("expected Ran")
        };
        assert!(matches!(outcome, CardOutcome::AlreadyListed { .. }), "{outcome:?}");
        assert!(sweep.closed.is_empty(), "the task in use was closed");
        assert_eq!(open_rows(&mut tx, set).await, vec![row], "not the same open task");
        assert!(!todoist.calls().iter().any(|c| matches!(c, Call::Close { .. })));
    }

    #[tokio::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn weak_cards_a_todoist_outage_records_nothing_and_the_next_review_retries() {
        let pool = test_db::pool().await;
        let mut tx = pool.begin().await.unwrap();
        let (user, set) = test_db::seed_learner(&mut tx).await;
        let card = test_db::seed_card(&mut tx, set, "q").await;
        let todoist = FakeTodoist::default();

        answer(&mut tx, card, user, &WEAK).await;
        todoist.fail_with(Some(TodoistError::Unreachable("connection refused".into())));
        let outcome = card_outcome(sync_on(&mut tx, &todoist, card, user).await);
        assert!(
            matches!(outcome, CardOutcome::Failed(SyncFailure::Todoist(TodoistError::Unreachable(_)))),
            "{outcome:?}"
        );
        assert!(open_rows(&mut tx, set).await.is_empty(), "a row without a Todoist task was kept");

        // Todoist back: the next weak review files the task.
        todoist.fail_with(None);
        test_db::record_answer(&mut tx, card, user, false, Utc::now()).await;
        let outcome = card_outcome(sync_on(&mut tx, &todoist, card, user).await);
        assert!(matches!(outcome, CardOutcome::TaskOpened { .. }), "{outcome:?}");
        assert_eq!(open_rows(&mut tx, set).await.len(), 1);
    }

    #[tokio::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn weak_cards_a_failed_update_does_not_list_the_card() {
        // Otherwise the card would read as "already listed" from then on and
        // never reach the Todoist description.
        let pool = test_db::pool().await;
        let mut tx = pool.begin().await.unwrap();
        let (user, set) = test_db::seed_learner(&mut tx).await;
        let a = test_db::seed_card(&mut tx, set, "a").await;
        let b = test_db::seed_card(&mut tx, set, "b").await;
        let todoist = FakeTodoist::default();

        answer(&mut tx, a, user, &WEAK).await;
        sync_on(&mut tx, &todoist, a, user).await;

        answer(&mut tx, b, user, &WEAK).await;
        todoist.fail_with(Some(TodoistError::Http { status: 503, body: "busy".into() }));
        let outcome = card_outcome(sync_on(&mut tx, &todoist, b, user).await);
        assert!(matches!(outcome, CardOutcome::Failed(_)), "{outcome:?}");
        assert_eq!(listed(&mut tx, set).await, 1);

        todoist.fail_with(None);
        test_db::record_answer(&mut tx, b, user, false, Utc::now()).await;
        let outcome = card_outcome(sync_on(&mut tx, &todoist, b, user).await);
        assert!(matches!(outcome, CardOutcome::CardAdded { .. }), "{outcome:?}");
        assert_eq!(listed(&mut tx, set).await, 2);
    }

    #[tokio::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn weak_cards_a_task_completed_in_todoist_is_replaced() {
        let pool = test_db::pool().await;
        let mut tx = pool.begin().await.unwrap();
        let (user, set) = test_db::seed_learner(&mut tx).await;
        let a = test_db::seed_card(&mut tx, set, "a").await;
        let b = test_db::seed_card(&mut tx, set, "b").await;
        let todoist = FakeTodoist::default();

        answer(&mut tx, a, user, &WEAK).await;
        let CardOutcome::TaskOpened { todoist_task_id: first, .. } =
            card_outcome(sync_on(&mut tx, &todoist, a, user).await)
        else {
            panic!("expected a task")
        };

        // The learner ticks it off in Todoist; the next weak card finds out.
        todoist.report_on_update(RemoteTaskState::Gone);
        answer(&mut tx, b, user, &WEAK).await;
        let outcome = card_outcome(sync_on(&mut tx, &todoist, b, user).await);
        match outcome {
            CardOutcome::TaskOpened { todoist_task_id, replaced: Some(old) } => {
                assert_eq!(old, first);
                assert_ne!(todoist_task_id, first);
            }
            other => panic!("expected a replacement task, got {other:?}"),
        }
        assert_eq!(open_rows(&mut tx, set).await.len(), 1);
        assert_eq!(listed(&mut tx, set).await, 1, "the new cycle starts with just card b");
    }

    #[tokio::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn weak_cards_a_failed_close_leaves_the_task_for_the_next_sweep() {
        let pool = test_db::pool().await;
        let mut tx = pool.begin().await.unwrap();
        let (user, set) = test_db::seed_learner(&mut tx).await;
        let card = test_db::seed_card(&mut tx, set, "q").await;
        let todoist = FakeTodoist::default();

        answer(&mut tx, card, user, &WEAK).await;
        sync_on(&mut tx, &todoist, card, user).await;
        sqlx::query("UPDATE weak_card_tasks SET last_weak_card_at = now() - interval '6 days' WHERE study_set_id = $1")
            .bind(set)
            .execute(&mut *tx)
            .await
            .unwrap();

        // Triggered from another set, so the reviewed card cannot revive it.
        let (other_user, other_set) = test_db::seed_learner(&mut tx).await;
        let other = test_db::seed_card(&mut tx, other_set, "unrelated").await;
        todoist.fail_with(Some(TodoistError::Http { status: 401, body: "unauthorized".into() }));
        let WeakCardSync::Ran { sweep, .. } = sync_on(&mut tx, &todoist, other, other_user).await
        else {
            panic!("expected Ran")
        };
        assert_eq!(sweep.failed.len(), 1);
        assert!(sweep.closed.is_empty());
        assert_eq!(open_rows(&mut tx, set).await.len(), 1, "closed in the DB but not in Todoist");
    }

    /// Two cards in one set turning weak at the same moment, on two
    /// connections. Without the set lock both see "no open task" and both
    /// create one in Todoist; the partial unique index then rejects the
    /// second row, and that card's review files nothing.
    ///
    /// Commits, because a race needs two connections that see each other's
    /// rows; deletes its learners at the end.
    #[tokio::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn weak_cards_concurrent_weak_cards_in_one_set_share_one_task() {
        let pool = test_db::pool().await;
        let mut conn = pool.acquire().await.unwrap();
        let (user, set) = test_db::seed_learner(&mut conn).await;
        let a = test_db::seed_card(&mut conn, set, "race a").await;
        let b = test_db::seed_card(&mut conn, set, "race b").await;
        answer(&mut conn, a, user, &WEAK).await;
        answer(&mut conn, b, user, &WEAK).await;
        drop(conn);

        let todoist = FakeTodoist::default();
        // Long enough that both requests are inside Todoist at once, if the
        // lock lets them.
        todoist.slow_down(std::time::Duration::from_millis(300));

        let (mut c1, mut c2) = (pool.acquire().await.unwrap(), pool.acquire().await.unwrap());
        let (r1, r2) = tokio::join!(
            sync_on(&mut c1, &todoist, a, user),
            sync_on(&mut c2, &todoist, b, user),
        );
        drop((c1, c2));
        let outcomes = [card_outcome(r1), card_outcome(r2)];

        let mut conn = pool.acquire().await.unwrap();
        let open = open_rows(&mut conn, set).await.len();
        let cards = listed(&mut conn, set).await;
        sqlx::query("DELETE FROM users WHERE id = $1").bind(user).execute(&mut *conn).await.unwrap();

        let creates = todoist.calls().iter().filter(|c| matches!(c, Call::Create { .. })).count();
        assert_eq!(creates, 1, "two Todoist tasks were created: {outcomes:?}");
        assert_eq!(open, 1);
        assert_eq!(cards, 2, "one card's review filed nothing: {outcomes:?}");
    }

    #[tokio::test]
    #[ignore = "requires the local Postgres cluster"]
    async fn weak_cards_four_misses_file_nothing() {
        let pool = test_db::pool().await;
        let mut tx = pool.begin().await.unwrap();
        let (user, set) = test_db::seed_learner(&mut tx).await;
        let card = test_db::seed_card(&mut tx, set, "new card").await;
        let todoist = FakeTodoist::default();

        answer(&mut tx, card, user, &[false; 4]).await;
        let outcome = card_outcome(sync_on(&mut tx, &todoist, card, user).await);
        assert!(matches!(outcome, CardOutcome::NotEnoughHistory { reviews: 4 }), "{outcome:?}");
        assert!(open_rows(&mut tx, set).await.is_empty());
        assert!(todoist.calls().is_empty());
    }
}
