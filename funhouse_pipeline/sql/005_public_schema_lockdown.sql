-- Feature: Supabase public-schema security
-- Migration 005: deny direct Data API access to all FunHouse tables.
--
-- Supabase exposes the public schema through PostgREST. The application does
-- not use that path: FastAPI connects directly with psycopg and applies its own
-- authentication and RBAC. This migration therefore clears all policies as a
-- fail-closed baseline; activated migration 007 later restores policies only for the
-- dedicated FastAPI runtime role while Data API roles remain denied.
--
-- RLS is intentionally not forced. The dedicated non-owner runtime role is
-- policy-bound after migration 007, while the offline table owner can still run
-- migrations, seed operations, and bootstrap tasks without maintenance
-- policies or provider-specific BYPASSRLS privileges.
--
-- All statements are idempotent. Role-specific statements are conditional so
-- the same migration remains portable to PostgreSQL installations without the
-- Supabase roles (including RDS and CI).

DO $$
DECLARE
    target_schema text := current_schema();
    expected_tables text[] := ARRAY[
        'locations',
        'schools',
        'users',
        'guardians',
        'players',
        'consents',
        'products',
        'entitlements',
        'sessions',
        'attendance',
        'payments',
        'lessons',
        'student_metrics',
        'sync_log'
    ];
    data_api_roles text[] := ARRAY[
        'anon',
        'authenticated',
        'service_role',
        'authenticator'
    ];
    object_name text;
    role_name text;
    policy_name text;
    object_oid oid;
    owner_name text;
    privileged_role text;
BEGIN
    IF target_schema IS NULL THEN
        RAISE EXCEPTION 'Cannot secure FunHouse tables because current_schema() is NULL';
    END IF;

    IF current_user = ANY(data_api_roles) THEN
        RAISE EXCEPTION
            'Migration role % must not be a Supabase Data API role',
            current_user;
    END IF;

    FOREACH object_name IN ARRAY expected_tables LOOP
        SELECT c.oid, pg_get_userbyid(c.relowner)
          INTO object_oid, owner_name
          FROM pg_class AS c
          JOIN pg_namespace AS n ON n.oid = c.relnamespace
         WHERE n.nspname = target_schema
           AND c.relname = object_name
           AND c.relkind IN ('r', 'p');

        IF object_oid IS NULL THEN
            RAISE EXCEPTION
                'Required FunHouse table %.% is missing or is not a base table',
                target_schema,
                object_name;
        END IF;

        IF owner_name = ANY(data_api_roles) THEN
            RAISE EXCEPTION
                'FunHouse table %.% must not be owned by Data API role %',
                target_schema,
                object_name,
                owner_name;
        END IF;

        IF owner_name <> current_user THEN
            RAISE EXCEPTION
                'FunHouse table %.% is owned by %, not active maintenance role %; '
                'verify the migration identity or DB_MAINTENANCE_ROLE before applying RLS',
                target_schema,
                object_name,
                owner_name,
                current_user;
        END IF;

        -- Remove any manually-created policy before enabling default-deny RLS.
        -- The direct FastAPI architecture requires no PostgREST policy.
        FOR policy_name IN
            SELECT pol.polname
              FROM pg_policy AS pol
             WHERE pol.polrelid = object_oid
        LOOP
            EXECUTE format(
                'DROP POLICY %I ON %I.%I',
                policy_name,
                target_schema,
                object_name
            );
        END LOOP;

        EXECUTE format(
            'ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY',
            target_schema,
            object_name
        );
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON TABLE %I.%I FROM PUBLIC',
            target_schema,
            object_name
        );

        FOREACH role_name IN ARRAY data_api_roles LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
                EXECUTE format(
                    'REVOKE ALL PRIVILEGES ON TABLE %I.%I FROM %I',
                    target_schema,
                    object_name,
                    role_name
                );

                -- REVOKE affects only direct grants. A Data API role can also
                -- inherit privileges or SET ROLE to any granted parent role.
                -- Fail the transaction if any such path still reaches DML or
                -- the owning role (whose implicit privileges appear here).
                privileged_role := NULL;
                WITH RECURSIVE reachable(role_oid) AS (
                    SELECT starting_role.oid
                      FROM pg_roles AS starting_role
                     WHERE starting_role.rolname = role_name
                    UNION
                    SELECT membership.roleid
                      FROM pg_auth_members AS membership
                      JOIN reachable
                        ON reachable.role_oid = membership.member
                )
                SELECT reachable_role.rolname
                  INTO privileged_role
                  FROM reachable
                  JOIN pg_roles AS reachable_role
                    ON reachable_role.oid = reachable.role_oid
                 WHERE has_table_privilege(
                           reachable_role.oid,
                           object_oid,
                           'SELECT'
                       )
                    OR has_table_privilege(
                           reachable_role.oid,
                           object_oid,
                           'INSERT'
                       )
                    OR has_table_privilege(
                           reachable_role.oid,
                           object_oid,
                           'UPDATE'
                       )
                    OR has_table_privilege(
                           reachable_role.oid,
                           object_oid,
                           'DELETE'
                       )
                    OR has_table_privilege(
                           reachable_role.oid,
                           object_oid,
                           'TRUNCATE'
                       )
                    OR has_table_privilege(
                           reachable_role.oid,
                           object_oid,
                           'REFERENCES'
                       )
                    OR has_table_privilege(
                           reachable_role.oid,
                           object_oid,
                           'TRIGGER'
                       )
                 ORDER BY (reachable_role.rolname = role_name), reachable_role.rolname
                 LIMIT 1;

                IF privileged_role IS NOT NULL THEN
                    RAISE EXCEPTION
                        'Data API role % can reach table %.% through privileged role %',
                        role_name,
                        target_schema,
                        object_name,
                        privileged_role;
                END IF;
            END IF;
        END LOOP;
    END LOOP;

    -- No sequence exists today (UUID keys use gen_random_uuid), but revoke
    -- applicable privileges if a sequence is later attached to a FunHouse table.
    FOR object_name IN
        SELECT sequence_class.relname
          FROM pg_class AS sequence_class
          JOIN pg_namespace AS sequence_namespace
            ON sequence_namespace.oid = sequence_class.relnamespace
          JOIN pg_depend AS dependency
            ON dependency.classid = 'pg_class'::regclass
           AND dependency.objid = sequence_class.oid
           AND dependency.deptype IN ('a', 'i')
          JOIN pg_class AS table_class
            ON dependency.refclassid = 'pg_class'::regclass
           AND dependency.refobjid = table_class.oid
          JOIN pg_namespace AS table_namespace
            ON table_namespace.oid = table_class.relnamespace
         WHERE sequence_class.relkind = 'S'
           AND sequence_namespace.nspname = target_schema
           AND table_namespace.nspname = target_schema
           AND table_class.relname = ANY(expected_tables)
    LOOP
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON SEQUENCE %I.%I FROM PUBLIC',
            target_schema,
            object_name
        );
        FOREACH role_name IN ARRAY data_api_roles LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
                EXECUTE format(
                    'REVOKE ALL PRIVILEGES ON SEQUENCE %I.%I FROM %I',
                    target_schema,
                    object_name,
                    role_name
                );
            END IF;
        END LOOP;
    END LOOP;

    SELECT p.oid
      INTO object_oid
      FROM pg_proc AS p
      JOIN pg_namespace AS n ON n.oid = p.pronamespace
     WHERE n.nspname = target_schema
       AND p.proname = 'funhouse_reject_consents_mutation'
       AND pg_get_function_identity_arguments(p.oid) = '';

    IF object_oid IS NULL THEN
        RAISE EXCEPTION
            'Required FunHouse function %.funhouse_reject_consents_mutation() is missing',
            target_schema;
    END IF;

    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON FUNCTION %I.funhouse_reject_consents_mutation() FROM PUBLIC',
        target_schema
    );
    FOREACH role_name IN ARRAY data_api_roles LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON FUNCTION %I.funhouse_reject_consents_mutation() FROM %I',
                target_schema,
                role_name
            );

            privileged_role := NULL;
            WITH RECURSIVE reachable(role_oid) AS (
                SELECT starting_role.oid
                  FROM pg_roles AS starting_role
                 WHERE starting_role.rolname = role_name
                UNION
                SELECT membership.roleid
                  FROM pg_auth_members AS membership
                  JOIN reachable
                    ON reachable.role_oid = membership.member
            )
            SELECT reachable_role.rolname
              INTO privileged_role
              FROM reachable
              JOIN pg_roles AS reachable_role
                ON reachable_role.oid = reachable.role_oid
             WHERE has_function_privilege(
                       reachable_role.oid,
                       object_oid,
                       'EXECUTE'
                   )
             ORDER BY (reachable_role.rolname = role_name), reachable_role.rolname
             LIMIT 1;

            IF privileged_role IS NOT NULL THEN
                RAISE EXCEPTION
                    'Data API role % can execute %.funhouse_reject_consents_mutation() through privileged role %',
                    role_name,
                    target_schema,
                    privileged_role;
            END IF;
        END IF;
    END LOOP;

    -- Default privileges are scoped to objects subsequently created by the
    -- migration role in this schema. This prevents future FunHouse migrations
    -- from silently restoring Data API access.
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES IN SCHEMA %I REVOKE ALL PRIVILEGES ON TABLES FROM PUBLIC',
        target_schema
    );
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES IN SCHEMA %I REVOKE ALL PRIVILEGES ON SEQUENCES FROM PUBLIC',
        target_schema
    );
    -- PostgreSQL's built-in function default grants EXECUTE to PUBLIC. Unlike
    -- object grants, that built-in default cannot be revoked for only one
    -- schema, so this applies to every future function created by the migration
    -- role. Existing functions outside this schema are not changed.
    EXECUTE
        'ALTER DEFAULT PRIVILEGES REVOKE ALL PRIVILEGES ON FUNCTIONS FROM PUBLIC';

    FOREACH role_name IN ARRAY data_api_roles LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES IN SCHEMA %I REVOKE ALL PRIVILEGES ON TABLES FROM %I',
                target_schema,
                role_name
            );
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES IN SCHEMA %I REVOKE ALL PRIVILEGES ON SEQUENCES FROM %I',
                target_schema,
                role_name
            );
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES IN SCHEMA %I REVOKE ALL PRIVILEGES ON FUNCTIONS FROM %I',
                target_schema,
                role_name
            );
        END IF;
    END LOOP;
END;
$$;
