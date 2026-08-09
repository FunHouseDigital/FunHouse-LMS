-- Feature: persist session console and quick-field references
-- Migration 008: add the session reference carried by offline sync actions.
--
-- Lounge sessions use this field for PS5/PS4. School sessions use it for the
-- kit module, esports match particulars, or lesson reference. ADD COLUMN IF
-- NOT EXISTS keeps migration replay safe and upgrades existing deployments.

ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS reference TEXT;
