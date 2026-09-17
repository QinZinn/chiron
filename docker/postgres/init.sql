-- Runs once, when the postgres volume is first created (docker-entrypoint-initdb.d).
-- One server, separate databases: Mnemosyne and the Knowledge Store share the
-- instance to avoid running two, never their schemas.
--
-- Extensions are NOT created here — each module's own schema/migrations create
-- what it needs (pgcrypto in mnemosyne/backend/sql/schema.sql, pg_trgm in
-- knowledge-store/ks/migrations/0001_init.sql), so a database built outside
-- Docker gets them the same way.
CREATE DATABASE mnemosyne;
CREATE DATABASE chiron_ks;
-- The KS test suite runs against a real database on purpose (trigram
-- similarity is Postgres behaviour; mocking it would test the mock).
CREATE DATABASE chiron_ks_test;
