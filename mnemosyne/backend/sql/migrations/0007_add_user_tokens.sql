-- =============================================================================
-- Migration 0007: API tokens, one or more per learner
-- =============================================================================
-- Before this, every endpoint took `user_id` as a plain request parameter and
-- believed it: anyone who could reach the port could read anyone's cards. Now
-- the caller proves who they are with `Authorization: Bearer <token>` and the
-- server derives `user_id` from the token — a request can no longer name a
-- user it does not hold a token for.
--
-- Only the SHA-256 hash of a token is stored. The token itself is shown once,
-- when it is minted, and cannot be recovered afterwards. SHA-256 (not argon2)
-- is the right tool here precisely because these are not passwords: a token is
-- 32 bytes of CSPRNG output, so there is no dictionary to attack and no work
-- factor worth paying on every single request.
--
-- Apply with:
--   psql -h 127.0.0.1 -p 5432 -U postgres -d mnemosyne -f <this file>
-- =============================================================================

CREATE TABLE user_tokens (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    -- Hex SHA-256 of the token string. UNIQUE doubles as the lookup index.
    token_hash    TEXT NOT NULL UNIQUE,
    -- Free text so a learner can tell "laptop" from "KS card-sync job" when
    -- deciding which token to revoke.
    label         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Bumped by the auth lookup itself, so a revoked-by-mistake token can be
    -- told apart from one nothing has used in months.
    last_used_at  TIMESTAMPTZ
);

-- Covers the ON DELETE CASCADE from users and "list my tokens".
CREATE INDEX idx_user_tokens_user_id ON user_tokens (user_id);
