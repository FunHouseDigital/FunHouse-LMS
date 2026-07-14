-- Feature: funhouse-api
-- Migration 003: widen users.role to include 'facilitator' (Req 3.3, 13.2).
--
-- The FunHouse Container API defines three access roles -- founder, manager,
-- facilitator (Req 3, Glossary) -- but the Phase 0 users.role CHECK only permits
-- ('founder','manager','coach','operator'), lacking 'facilitator'. This is the
-- ONLY schema touch the API needs and it is purely additive/idempotent: no
-- columns are added and re-running is safe.
--
-- The migration runner auto-discovers this file via the sql/ lexical glob
-- (migration_files()), so it applies in ordinal order after 001 and 002.

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;

ALTER TABLE users ADD CONSTRAINT users_role_check
    CHECK (role IN ('founder','manager','facilitator','coach','operator'));
