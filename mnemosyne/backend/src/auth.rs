//! Bearer-token authentication.
//!
//! Every learner-facing endpoint now takes [`AuthedUser`] instead of a
//! `user_id` field. The difference is not cosmetic: a request used to *assert*
//! whose data it wanted, and this one *proves* it. Handlers can no longer read
//! or write another learner's rows by accident, because the id they act on is
//! the one the token resolved to and there is no other id in scope.
//!
//! Tokens are stored as SHA-256 hashes (see migration 0007 for why not argon2)
//! and shown exactly once, when minted by the `mint-token` CLI command.

use std::future::Future;
use std::pin::Pin;

use actix_web::{dev::Payload, http::header, web, FromRequest, HttpRequest, HttpResponse, ResponseError};
use chrono::{DateTime, Utc};
use rand::Rng as _;
use serde::Serialize;
use sha2::{Digest, Sha256};
use sqlx::{FromRow, PgPool};
use uuid::Uuid;

/// Prefix on every minted token. Purely a readability aid: a string starting
/// with `mnem_` in a log or an .env file is recognisable as this system's
/// credential, and secret scanners can be taught one pattern.
pub const TOKEN_PREFIX: &str = "mnem_";

/// 32 bytes of CSPRNG output. Far past guessing range, and the reason the
/// stored hash can be a plain SHA-256.
const TOKEN_BYTES: usize = 32;

/// Mint a new token string. Returned once to the caller; only its hash is kept.
///
/// `rand::rng()` is the thread-local CSPRNG, seeded from the OS and reseeded
/// periodically; it is infallible by construction, which is why this returns a
/// String rather than a Result.
pub fn generate_token() -> String {
    let mut buf = [0u8; TOKEN_BYTES];
    rand::rng().fill_bytes(&mut buf);
    format!("{TOKEN_PREFIX}{}", hex::encode(buf))
}

/// Hex SHA-256 of a token, as stored in `user_tokens.token_hash`.
pub fn hash_token(token: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(token.as_bytes());
    hex::encode(hasher.finalize())
}

/// The authenticated learner. Obtained only by presenting a valid token, so a
/// handler holding one of these is holding a proven identity.
#[derive(Debug, Clone, Copy)]
pub struct AuthedUser {
    pub user_id: Uuid,
}

#[derive(Debug, Serialize)]
struct AuthErrorBody {
    error: String,
}

#[derive(Debug)]
pub enum AuthError {
    /// No `Authorization: Bearer …` header at all.
    Missing,
    /// Header present but no token matches it.
    Invalid,
    /// The database could not be asked. Deliberately NOT reported as "invalid
    /// token": a learner would otherwise be told their credential is wrong
    /// while the real problem is an outage.
    Backend(String),
}

impl std::fmt::Display for AuthError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            AuthError::Missing => write!(f, "missing Authorization: Bearer <token> header"),
            AuthError::Invalid => write!(f, "invalid or revoked token"),
            AuthError::Backend(e) => write!(f, "could not verify token: {e}"),
        }
    }
}

impl ResponseError for AuthError {
    fn error_response(&self) -> HttpResponse {
        let body = AuthErrorBody { error: self.to_string() };
        match self {
            AuthError::Backend(_) => HttpResponse::ServiceUnavailable().json(body),
            _ => HttpResponse::Unauthorized()
                .insert_header((header::WWW_AUTHENTICATE, "Bearer"))
                .json(body),
        }
    }
}

/// Look a token up and mark it used, in one round trip. The `UPDATE … RETURNING`
/// is what makes `last_used_at` free: a separate write would double the cost of
/// every authenticated request.
const LOOKUP_QUERY: &str = r#"UPDATE user_tokens
   SET last_used_at = now()
   WHERE token_hash = $1
   RETURNING user_id"#;

impl FromRequest for AuthedUser {
    type Error = AuthError;
    type Future = Pin<Box<dyn Future<Output = Result<Self, Self::Error>>>>;

    fn from_request(req: &HttpRequest, _: &mut Payload) -> Self::Future {
        let pool = req.app_data::<web::Data<PgPool>>().cloned();
        let header_value = req
            .headers()
            .get(header::AUTHORIZATION)
            .and_then(|v| v.to_str().ok())
            .map(str::to_owned);

        Box::pin(async move {
            let Some(pool) = pool else {
                return Err(AuthError::Backend("no database pool configured".into()));
            };
            let Some(raw) = header_value else {
                return Err(AuthError::Missing);
            };
            // Case-insensitive scheme, exactly one space, per RFC 7235.
            let token = raw
                .strip_prefix("Bearer ")
                .or_else(|| raw.strip_prefix("bearer "))
                .ok_or(AuthError::Missing)?
                .trim();
            if token.is_empty() {
                return Err(AuthError::Missing);
            }

            match sqlx::query_scalar::<_, Uuid>(LOOKUP_QUERY)
                .bind(hash_token(token))
                .fetch_optional(pool.get_ref())
                .await
            {
                Ok(Some(user_id)) => Ok(AuthedUser { user_id }),
                Ok(None) => Err(AuthError::Invalid),
                Err(e) => Err(AuthError::Backend(e.to_string())),
            }
        })
    }
}

/// Env var holding the operator token that guards user administration.
pub const ADMIN_TOKEN_ENV: &str = "MNEMOSYNE_ADMIN_TOKEN";

/// Proof that the caller holds the operator token from [`ADMIN_TOKEN_ENV`].
///
/// Creating users is not a learner action — a learner token must not be able
/// to mint colleagues — but it still needs to be reachable over HTTP for
/// setup scripts. When the env var is unset the endpoints refuse everyone and
/// point at the `create-user` CLI command, which needs only database access.
#[derive(Debug, Clone, Copy)]
pub struct AdminToken;

impl FromRequest for AdminToken {
    type Error = AuthError;
    type Future = std::future::Ready<Result<Self, Self::Error>>;

    fn from_request(req: &HttpRequest, _: &mut Payload) -> Self::Future {
        let expected = std::env::var(ADMIN_TOKEN_ENV).unwrap_or_default();
        if expected.is_empty() {
            return std::future::ready(Err(AuthError::Backend(format!(
                "{ADMIN_TOKEN_ENV} is not configured; use the `create-user` CLI command instead"
            ))));
        }
        let given = req
            .headers()
            .get(header::AUTHORIZATION)
            .and_then(|v| v.to_str().ok())
            .and_then(|v| v.strip_prefix("Bearer ").or_else(|| v.strip_prefix("bearer ")))
            .unwrap_or("")
            .trim()
            .to_owned();
        if given.is_empty() {
            return std::future::ready(Err(AuthError::Missing));
        }
        // Constant-time compare on bytes: the admin token is a fixed secret, so
        // a byte-by-byte early exit would leak it a character at a time.
        let ok = constant_time_eq(given.as_bytes(), expected.as_bytes());
        std::future::ready(if ok { Ok(AdminToken) } else { Err(AuthError::Invalid) })
    }
}

fn constant_time_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

// ---------------------------------------------------------------------------
// Ownership
// ---------------------------------------------------------------------------

/// Does this learner own this study set?
///
/// Every set-scoped endpoint asks before doing anything. A set that exists but
/// belongs to someone else answers the same as one that does not exist at all,
/// and handlers turn both into 404 — telling them apart would let any token
/// enumerate which set ids are real.
pub async fn owns_study_set(pool: &PgPool, user_id: Uuid, set_id: Uuid) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar::<_, bool>(
        "SELECT EXISTS(SELECT 1 FROM study_sets WHERE id = $1 AND user_id = $2)",
    )
    .bind(set_id)
    .bind(user_id)
    .fetch_one(pool)
    .await
}

/// Does this learner own the study set this card belongs to?
pub async fn owns_card(pool: &PgPool, user_id: Uuid, card_id: Uuid) -> Result<bool, sqlx::Error> {
    sqlx::query_scalar::<_, bool>(
        r#"SELECT EXISTS(
             SELECT 1 FROM cards c
             JOIN study_sets s ON s.id = c.set_id
             WHERE c.id = $1 AND s.user_id = $2)"#,
    )
    .bind(card_id)
    .bind(user_id)
    .fetch_one(pool)
    .await
}

// ---------------------------------------------------------------------------
// Token administration (used by the CLI commands in main.rs)
// ---------------------------------------------------------------------------

#[derive(Debug, FromRow)]
pub struct TokenRow {
    pub id: Uuid,
    pub label: Option<String>,
    pub created_at: DateTime<Utc>,
    pub last_used_at: Option<DateTime<Utc>>,
}

/// Mint a token for a learner and return the plaintext — the only moment it
/// exists outside the caller's hands.
pub async fn mint_token(pool: &PgPool, user_id: Uuid, label: Option<&str>) -> Result<String, String> {
    let token = generate_token();
    sqlx::query("INSERT INTO user_tokens (user_id, token_hash, label) VALUES ($1, $2, $3)")
        .bind(user_id)
        .bind(hash_token(&token))
        .bind(label)
        .execute(pool)
        .await
        .map_err(|e| format!("could not store token: {e}"))?;
    Ok(token)
}

pub async fn list_tokens(pool: &PgPool, user_id: Uuid) -> Result<Vec<TokenRow>, sqlx::Error> {
    sqlx::query_as::<_, TokenRow>(
        r#"SELECT id, label, created_at, last_used_at
           FROM user_tokens WHERE user_id = $1 ORDER BY created_at"#,
    )
    .bind(user_id)
    .fetch_all(pool)
    .await
}

pub async fn revoke_token(pool: &PgPool, token_id: Uuid) -> Result<bool, sqlx::Error> {
    let done = sqlx::query("DELETE FROM user_tokens WHERE id = $1")
        .bind(token_id)
        .execute(pool)
        .await?;
    Ok(done.rows_affected() > 0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_minted_token_carries_the_prefix_and_32_random_bytes() {
        let t = generate_token();
        assert!(t.starts_with(TOKEN_PREFIX), "{t}");
        assert_eq!(t.len(), TOKEN_PREFIX.len() + TOKEN_BYTES * 2);
    }

    #[test]
    fn two_tokens_are_never_the_same() {
        // Not a probabilistic nicety: a repeat would mean the RNG is broken and
        // every learner could end up sharing one credential.
        let a = generate_token();
        let b = generate_token();
        assert_ne!(a, b);
    }

    #[test]
    fn the_hash_is_stable_and_does_not_contain_the_token() {
        let t = "mnem_0123456789abcdef";
        let h = hash_token(t);
        assert_eq!(h, hash_token(t));
        assert_eq!(h.len(), 64);
        assert!(!h.contains("0123456789abcdef"));
    }

    #[test]
    fn a_different_token_hashes_differently() {
        assert_ne!(hash_token("mnem_a"), hash_token("mnem_b"));
    }

    #[test]
    fn constant_time_eq_still_compares_correctly() {
        // Constant time is the point, but it is worthless if it gets the
        // answer wrong, so check the answer.
        assert!(constant_time_eq(b"secret", b"secret"));
        assert!(!constant_time_eq(b"secret", b"secreT"));
        assert!(!constant_time_eq(b"secret", b"secret-longer"));
        assert!(constant_time_eq(b"", b""));
    }
}
