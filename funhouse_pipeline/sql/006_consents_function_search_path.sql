-- Feature: Supabase public-schema security
-- Migration 006: make the consent trigger function's name resolution immutable.
--
-- An empty function-level search_path prevents caller-controlled schemas from
-- affecting unqualified name resolution. PostgreSQL still searches pg_catalog
-- implicitly. The current trigger body references no database objects; future
-- object references must be schema-qualified.
--
-- ALTER FUNCTION preserves the function owner, ACL, trigger dependency, and
-- body. The statement is idempotent and follows migration 002 on every replay.

DO $$
DECLARE
    target_schema text := current_schema();
    function_oid oid;
BEGIN
    IF target_schema IS NULL THEN
        RAISE EXCEPTION
            'Cannot secure consent trigger function because current_schema() is NULL';
    END IF;

    SELECT p.oid
      INTO function_oid
      FROM pg_proc AS p
      JOIN pg_namespace AS n ON n.oid = p.pronamespace
     WHERE n.nspname = target_schema
       AND p.proname = 'funhouse_reject_consents_mutation'
       AND pg_get_function_identity_arguments(p.oid) = '';

    IF function_oid IS NULL THEN
        RAISE EXCEPTION
            'Required FunHouse function %.funhouse_reject_consents_mutation() is missing',
            target_schema;
    END IF;

    EXECUTE format(
        'ALTER FUNCTION %I.funhouse_reject_consents_mutation() SET search_path = %L',
        target_schema,
        ''
    );

    IF NOT EXISTS (
        SELECT 1
          FROM pg_proc AS p
          CROSS JOIN LATERAL unnest(COALESCE(p.proconfig, ARRAY[]::text[])) AS setting
         WHERE p.oid = function_oid
           AND setting = 'search_path=""'
    ) THEN
        RAISE EXCEPTION
            'Failed to fix search_path for %.funhouse_reject_consents_mutation()',
            target_schema;
    END IF;
END;
$$;
