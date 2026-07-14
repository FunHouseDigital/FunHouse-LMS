-- Feature: phase0-data-foundation
-- Migration 002: enforce the append-only consent ledger (Req 11.3).
--
-- The consents table is an append-only ledger: revocations are appended as new
-- rows and no row is ever deleted or overwritten (Req 11.1-11.3). This is
-- enforced at two layers, per the design's Error Handling section:
--   1. A trigger that rejects UPDATE and DELETE on the consents table.
--   2. Restricted role grants: the pipeline role receives INSERT/SELECT only.
--
-- Idempotent: the function is CREATE OR REPLACE and the trigger is dropped
-- before (re)creation, so this migration is safe to re-run (Req 1.6 spirit).

CREATE OR REPLACE FUNCTION funhouse_reject_consents_mutation()
    RETURNS trigger
    LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'consents is an append-only ledger: % is not permitted (Req 11.3)',
        TG_OP
        USING ERRCODE = 'raise_exception';
END;
$$;

DROP TRIGGER IF EXISTS trg_consents_append_only ON consents;

CREATE TRIGGER trg_consents_append_only
    BEFORE UPDATE OR DELETE ON consents
    FOR EACH ROW
    EXECUTE FUNCTION funhouse_reject_consents_mutation();

-- Restricted role grants for the pipeline role.
--
-- The role is created only if a name is provided via the psql variable
-- :pipeline_role. When run without that variable set (the default), the grant
-- block is skipped so the migration still succeeds on a fresh/local database
-- where no dedicated role exists. Provide the variable in production:
--   psql -v pipeline_role=funhouse_pipeline ...
DO $$
DECLARE
    role_name text := current_setting('funhouse.pipeline_role', true);
BEGIN
    IF role_name IS NULL OR role_name = '' THEN
        RAISE NOTICE 'No funhouse.pipeline_role configured; skipping consents grants.';
        RETURN;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
        RAISE NOTICE 'Role % does not exist; skipping consents grants.', role_name;
        RETURN;
    END IF;

    -- Append-only: INSERT + SELECT only. No UPDATE/DELETE grant.
    EXECUTE format('REVOKE UPDATE, DELETE, TRUNCATE ON consents FROM %I', role_name);
    EXECUTE format('GRANT INSERT, SELECT ON consents TO %I', role_name);
END;
$$;
