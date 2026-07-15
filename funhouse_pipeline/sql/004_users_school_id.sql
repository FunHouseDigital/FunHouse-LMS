-- Feature: funhouse-api
-- Migration 004: add nullable users.school_id for facilitator scope (Req 1.8, 3.3).
--
-- Facilitator access scope is defined by location_id AND school_id (Req 3.3),
-- and the RBAC_Enforcer already filters/asserts on the school_id JWT claim. But
-- the Phase 0 users table carries only role and location_id -- it has NO
-- school_id column -- so Auth_Service.issue_token has nothing to source a
-- facilitator's school from, leaving facilitator school-scoping unwired end to
-- end. This migration closes that gap.
--
-- It is purely additive/idempotent and adds NO table: the 14-table count is
-- unchanged. The column is NULLABLE so founders and managers stay NULL (they
-- have no assigned school); a facilitator's row carries their assigned school.
-- ADD COLUMN IF NOT EXISTS makes re-running safe with no data loss.
--
-- The migration runner auto-discovers this file via the sql/ lexical glob
-- (migration_files()), so it applies in ordinal order after 001, 002, and 003.

ALTER TABLE users ADD COLUMN IF NOT EXISTS school_id UUID REFERENCES schools(id);
