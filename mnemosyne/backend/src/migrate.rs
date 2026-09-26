//! `backend migrate` — apply the SQL files in `backend/sql/migrations/`.
//!
//! Until now these were applied by hand with psql, and nothing recorded which
//! ones had run: the only way to know a database's shape was to look at it.
//! The Knowledge Store has had `ks migrate` and a `ks_schema_migrations` table
//! from the start; this is the same idea, so both modules answer "which
//! migrations has this database seen?" the same way.
//!
//! Files are embedded in the binary at compile time rather than read from disk.
//! A deployed binary then carries its own migrations, and cannot be run against
//! a directory of files from a different version.
//!
//! Each file runs inside a transaction together with the row that records it,
//! so a failure leaves neither a half-applied migration nor a false record.

use sqlx::AssertSqlSafe;
use sqlx::{PgPool, Row};

/// Every migration, in order. `include_str!` is what makes the binary
/// self-contained; adding a file here is the one step needed to ship it.
const MIGRATIONS: &[(&str, &str)] = &[
    ("0001_add_fsrs_fields", include_str!("../sql/migrations/0001_add_fsrs_fields.sql")),
    ("0002_add_socratic_tables", include_str!("../sql/migrations/0002_add_socratic_tables.sql")),
    ("0003_add_feynman_evaluations", include_str!("../sql/migrations/0003_add_feynman_evaluations.sql")),
    ("0004_add_quiz_tables", include_str!("../sql/migrations/0004_add_quiz_tables.sql")),
    ("0005_add_card_source_tracking", include_str!("../sql/migrations/0005_add_card_source_tracking.sql")),
    ("0006_add_weak_card_tasks", include_str!("../sql/migrations/0006_add_weak_card_tasks.sql")),
    ("0007_add_user_tokens", include_str!("../sql/migrations/0007_add_user_tokens.sql")),
    ("0008_add_chat_sessions", include_str!("../sql/migrations/0008_add_chat_sessions.sql")),
    ("0009_socratic_ended_at", include_str!("../sql/migrations/0009_socratic_ended_at.sql")),
    ("0010_replace_weak_card_tasks_with_todos", include_str!("../sql/migrations/0010_replace_weak_card_tasks_with_todos.sql")),
    ("0011_add_blurting", include_str!("../sql/migrations/0011_add_blurting.sql")),
];

const CREATE_TABLE: &str = r#"CREATE TABLE IF NOT EXISTS schema_migrations (
    name        TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)"#;

/// The full current schema. Migrations are incremental `ALTER`s on top of an
/// existing database, so they cannot build one from nothing — this can.
const SCHEMA: &str = include_str!("../sql/schema.sql");

/// Apply everything not yet recorded. Returns the names it applied.
///
/// On an empty database (no `users` table) it first builds the schema from
/// `schema.sql` and records every migration as applied, since that file
/// already contains all of them. That is what lets a fresh container come up
/// with nothing more than `backend migrate && backend`.
pub async fn run(pool: &PgPool) -> Result<Vec<String>, String> {
    let exists: bool = sqlx::query_scalar("SELECT to_regclass('public.users') IS NOT NULL")
        .fetch_one(pool)
        .await
        .map_err(|e| format!("could not inspect the database: {e}"))?;
    if !exists {
        let mut tx = pool
            .begin()
            .await
            .map_err(|e| format!("could not start a transaction for the schema: {e}"))?;
        // AssertSqlSafe: a file compiled into this binary, not caller input.
        sqlx::raw_sql(AssertSqlSafe(SCHEMA))
            .execute(&mut *tx)
            .await
            .map_err(|e| format!("building the schema from schema.sql failed: {e}"))?;
        tx.commit()
            .await
            .map_err(|e| format!("could not commit the schema: {e}"))?;
        mark_all_applied(pool).await?;
        return Ok(vec!["schema.sql (empty database)".to_string()]);
    }

    sqlx::query(CREATE_TABLE)
        .execute(pool)
        .await
        .map_err(|e| format!("could not create schema_migrations: {e}"))?;

    let applied: Vec<String> = sqlx::query("SELECT name FROM schema_migrations")
        .fetch_all(pool)
        .await
        .map_err(|e| format!("could not read schema_migrations: {e}"))?
        .into_iter()
        .map(|r| r.get::<String, _>("name"))
        .collect();

    let mut done = Vec::new();
    for (name, sql) in MIGRATIONS {
        if applied.iter().any(|a| a == name) {
            continue;
        }
        let mut tx = pool
            .begin()
            .await
            .map_err(|e| format!("could not start a transaction for {name}: {e}"))?;
        // AssertSqlSafe because the "dynamic" string is a file compiled into
        // this binary, not anything a caller supplied.
        sqlx::raw_sql(AssertSqlSafe(*sql))
            .execute(&mut *tx)
            .await
            .map_err(|e| format!("migration {name} failed: {e}"))?;
        sqlx::query("INSERT INTO schema_migrations (name) VALUES ($1)")
            .bind(name)
            .execute(&mut *tx)
            .await
            .map_err(|e| format!("could not record {name}: {e}"))?;
        tx.commit()
            .await
            .map_err(|e| format!("could not commit {name}: {e}"))?;
        done.push((*name).to_string());
    }
    Ok(done)
}

/// Record every migration as applied without running any of them.
///
/// For the one database that was migrated by hand before this existed: its
/// schema is already correct, and re-running the files would fail on objects
/// that exist. Deliberately a separate command, because using it on a database
/// that is NOT up to date would hide the fact permanently.
pub async fn mark_all_applied(pool: &PgPool) -> Result<Vec<String>, String> {
    sqlx::query(CREATE_TABLE)
        .execute(pool)
        .await
        .map_err(|e| format!("could not create schema_migrations: {e}"))?;

    let mut marked = Vec::new();
    for (name, _) in MIGRATIONS {
        let res = sqlx::query("INSERT INTO schema_migrations (name) VALUES ($1) ON CONFLICT DO NOTHING")
            .bind(name)
            .execute(pool)
            .await
            .map_err(|e| format!("could not record {name}: {e}"))?;
        if res.rows_affected() > 0 {
            marked.push((*name).to_string());
        }
    }
    Ok(marked)
}

/// What the database has seen, for `backend migrate-status`.
pub async fn status(pool: &PgPool) -> Result<Vec<(String, bool)>, String> {
    let applied: Vec<String> = match sqlx::query("SELECT name FROM schema_migrations").fetch_all(pool).await {
        Ok(rows) => rows.into_iter().map(|r| r.get::<String, _>("name")).collect(),
        // No table yet means nothing has been applied through this runner.
        Err(_) => Vec::new(),
    };
    Ok(MIGRATIONS
        .iter()
        .map(|(name, _)| ((*name).to_string(), applied.iter().any(|a| a == name)))
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn migrations_are_listed_in_order_and_none_is_empty() {
        // Order is the contract: 0008 adds a column 0009 then alters. A file
        // that sorted out of place would apply against the wrong schema.
        let mut names: Vec<&str> = MIGRATIONS.iter().map(|(n, _)| *n).collect();
        let sorted = {
            let mut c = names.clone();
            c.sort_unstable();
            c
        };
        assert_eq!(names, sorted, "migrations must be listed in filename order");

        names.dedup();
        assert_eq!(names.len(), MIGRATIONS.len(), "duplicate migration name");

        for (name, sql) in MIGRATIONS {
            assert!(!sql.trim().is_empty(), "{name} is empty — include_str! pointed at nothing useful");
        }
    }
}
