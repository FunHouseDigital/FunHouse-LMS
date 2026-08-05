-- Feature: dedicated FastAPI database identity
-- Migration 007: grant the least-privilege runtime role explicit access.
--
-- Role creation, passwords, memberships, and object-ownership transfer are
-- intentionally out of band. Set `funhouse.enable_runtime_role=on` in the
-- first controlled migration session so a missing role fails closed. Once the
-- role exists, every replay restores its policy/grant contract automatically,
-- even without the setting. Migrations 005 and 007 run in one transaction: 005
-- removes all policies, then this file restores only those required by FastAPI.
-- Targets without the role remain a compatibility no-op unless activation was
-- explicitly requested.
--
-- RLS remains non-forced for this transition. The non-owner runtime login is
-- policy-bound, while the offline owner/migrator can still migrate and seed.

DO $$
DECLARE
    target_schema text := current_schema();
    runtime_role constant text := 'funhouse_runtime';
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
    select_tables text[] := ARRAY[
        'users',
        'players',
        'consents',
        'products',
        'entitlements',
        'sessions',
        'attendance',
        'payments',
        'student_metrics',
        'sync_log'
    ];
    insert_tables text[] := ARRAY[
        'players',
        'consents',
        'entitlements',
        'sessions',
        'attendance',
        'payments',
        'student_metrics',
        'sync_log'
    ];
    update_tables text[] := ARRAY[
        'players',
        'entitlements',
        'sessions',
        'attendance',
        'payments',
        'student_metrics'
    ];
    runtime_oid oid;
    object_oid oid;
    schema_oid oid;
    object_name text;
    sequence_table_name text;
    owner_name text;
    privilege_name text;
    granted_privileges text[];
    expected_privilege boolean;
    actual_privilege boolean;
    expected_policy_count integer;
    actual_policy_count integer;
    role_can_login boolean;
    role_is_superuser boolean;
    role_can_create_db boolean;
    role_can_create_role boolean;
    role_can_replicate boolean;
    role_bypasses_rls boolean;
    runtime_role_activation text := lower(
        COALESCE(current_setting('funhouse.enable_runtime_role', true), '')
    );
BEGIN
    IF target_schema IS NULL THEN
        RAISE EXCEPTION
            'Cannot configure runtime access because current_schema() is NULL';
    END IF;

    SELECT n.oid
      INTO schema_oid
      FROM pg_namespace AS n
     WHERE n.nspname = target_schema;

    SELECT r.oid,
           r.rolcanlogin,
           r.rolsuper,
           r.rolcreatedb,
           r.rolcreaterole,
           r.rolreplication,
           r.rolbypassrls
      INTO runtime_oid,
           role_can_login,
           role_is_superuser,
           role_can_create_db,
           role_can_create_role,
           role_can_replicate,
           role_bypasses_rls
      FROM pg_roles AS r
     WHERE r.rolname = runtime_role;

    IF runtime_oid IS NULL THEN
        IF runtime_role_activation IN ('1', 'on', 'true') THEN
            RAISE EXCEPTION
                'Required runtime role % does not exist; provision it out of band before migrating',
                runtime_role;
        END IF;
        RAISE NOTICE
            'Skipping dedicated runtime-role grants because role % is not provisioned',
            runtime_role;
        RETURN;
    END IF;

    IF NOT role_can_login
       OR role_is_superuser
       OR role_can_create_db
       OR role_can_create_role
       OR role_can_replicate
       OR role_bypasses_rls THEN
        RAISE EXCEPTION
            'Runtime role % must be LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
            runtime_role;
    END IF;

    IF EXISTS (
        SELECT 1
          FROM pg_auth_members AS membership
         WHERE membership.member = runtime_oid
            OR membership.roleid = runtime_oid
    ) THEN
        RAISE EXCEPTION
            'Runtime role % must neither inherit from nor be granted to any other role',
            runtime_role;
    END IF;

    IF current_user = runtime_role THEN
        RAISE EXCEPTION
            'Runtime role % must never execute schema migrations',
            runtime_role;
    END IF;

    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON DATABASE %I FROM %I',
        current_database(),
        runtime_role
    );
    EXECUTE format(
        'GRANT CONNECT ON DATABASE %I TO %I',
        current_database(),
        runtime_role
    );
    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON SCHEMA %I FROM %I',
        target_schema,
        runtime_role
    );
    EXECUTE format(
        'GRANT USAGE ON SCHEMA %I TO %I',
        target_schema,
        runtime_role
    );

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

        IF owner_name <> current_user THEN
            RAISE EXCEPTION
                'FunHouse table %.% is owned by %, not active maintenance role %',
                target_schema,
                object_name,
                owner_name,
                current_user;
        END IF;

        IF owner_name = runtime_role THEN
            RAISE EXCEPTION
                'Runtime role % must not own FunHouse table %.%',
                runtime_role,
                target_schema,
                object_name;
        END IF;

        IF NOT EXISTS (
            SELECT 1
              FROM pg_class AS c
             WHERE c.oid = object_oid
               AND c.relrowsecurity
               AND NOT c.relforcerowsecurity
        ) THEN
            RAISE EXCEPTION
                'FunHouse table %.% must have non-forced RLS enabled before runtime access is granted',
                target_schema,
                object_name;
        END IF;

        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON TABLE %I.%I FROM %I',
            target_schema,
            object_name,
            runtime_role
        );

        granted_privileges := ARRAY[]::text[];
        IF object_name = ANY(select_tables) THEN
            granted_privileges := array_append(granted_privileges, 'SELECT');
        END IF;
        IF object_name = ANY(insert_tables) THEN
            granted_privileges := array_append(granted_privileges, 'INSERT');
        END IF;
        IF object_name = ANY(update_tables) THEN
            granted_privileges := array_append(granted_privileges, 'UPDATE');
        END IF;
        IF cardinality(granted_privileges) > 0 THEN
            EXECUTE format(
                'GRANT %s ON TABLE %I.%I TO %I',
                array_to_string(granted_privileges, ', '),
                target_schema,
                object_name,
                runtime_role
            );
        END IF;

        EXECUTE format(
            'DROP POLICY IF EXISTS funhouse_runtime_select ON %I.%I',
            target_schema,
            object_name
        );
        EXECUTE format(
            'DROP POLICY IF EXISTS funhouse_runtime_insert ON %I.%I',
            target_schema,
            object_name
        );
        EXECUTE format(
            'DROP POLICY IF EXISTS funhouse_runtime_update ON %I.%I',
            target_schema,
            object_name
        );

        expected_policy_count := 0;
        IF object_name = ANY(select_tables) THEN
            EXECUTE format(
                'CREATE POLICY funhouse_runtime_select ON %I.%I FOR SELECT TO %I USING (true)',
                target_schema,
                object_name,
                runtime_role
            );
            expected_policy_count := expected_policy_count + 1;
        END IF;
        IF object_name = ANY(insert_tables) THEN
            EXECUTE format(
                'CREATE POLICY funhouse_runtime_insert ON %I.%I FOR INSERT TO %I WITH CHECK (true)',
                target_schema,
                object_name,
                runtime_role
            );
            expected_policy_count := expected_policy_count + 1;
        END IF;
        IF object_name = ANY(update_tables) THEN
            EXECUTE format(
                'CREATE POLICY funhouse_runtime_update ON %I.%I FOR UPDATE TO %I USING (true) WITH CHECK (true)',
                target_schema,
                object_name,
                runtime_role
            );
            expected_policy_count := expected_policy_count + 1;
        END IF;

        SELECT count(*)
          INTO actual_policy_count
          FROM pg_policy AS policy
         WHERE policy.polrelid = object_oid;

        IF actual_policy_count <> expected_policy_count THEN
            RAISE EXCEPTION
                'Table %.% has % policies; expected exactly % runtime policies',
                target_schema,
                object_name,
                actual_policy_count,
                expected_policy_count;
        END IF;

        IF EXISTS (
            SELECT 1
              FROM pg_policy AS policy
             WHERE policy.polrelid = object_oid
               AND (
                   policy.polroles <> ARRAY[runtime_oid]::oid[]
                   OR NOT (
                       (
                           policy.polname = 'funhouse_runtime_select'
                           AND policy.polcmd = 'r'
                           AND pg_get_expr(policy.polqual, policy.polrelid) = 'true'
                           AND policy.polwithcheck IS NULL
                       )
                       OR (
                           policy.polname = 'funhouse_runtime_insert'
                           AND policy.polcmd = 'a'
                           AND policy.polqual IS NULL
                           AND pg_get_expr(policy.polwithcheck, policy.polrelid) = 'true'
                       )
                       OR (
                           policy.polname = 'funhouse_runtime_update'
                           AND policy.polcmd = 'w'
                           AND pg_get_expr(policy.polqual, policy.polrelid) = 'true'
                           AND pg_get_expr(policy.polwithcheck, policy.polrelid) = 'true'
                       )
                   )
               )
        ) THEN
            RAISE EXCEPTION
                'Table %.% contains an unexpected RLS policy',
                target_schema,
                object_name;
        END IF;

        FOREACH privilege_name IN ARRAY ARRAY[
            'SELECT',
            'INSERT',
            'UPDATE',
            'DELETE',
            'TRUNCATE',
            'REFERENCES',
            'TRIGGER'
        ] LOOP
            expected_privilege :=
                (privilege_name = 'SELECT' AND object_name = ANY(select_tables))
                OR (privilege_name = 'INSERT' AND object_name = ANY(insert_tables))
                OR (privilege_name = 'UPDATE' AND object_name = ANY(update_tables));
            SELECT has_table_privilege(runtime_oid, object_oid, privilege_name)
              INTO actual_privilege;
            IF actual_privilege IS DISTINCT FROM expected_privilege THEN
                RAISE EXCEPTION
                    'Runtime role % has unexpected % privilege state on %.%',
                    runtime_role,
                    privilege_name,
                    target_schema,
                    object_name;
            END IF;
        END LOOP;
    END LOOP;

    -- UUID keys currently use no sequences. If an owned sequence is introduced,
    -- only an API insert path receives the minimum nextval/currval privileges.
    FOR object_name, sequence_table_name, object_oid IN
        SELECT sequence_class.relname, table_class.relname, sequence_class.oid
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
            'REVOKE ALL PRIVILEGES ON SEQUENCE %I.%I FROM %I',
            target_schema,
            object_name,
            runtime_role
        );
        IF sequence_table_name = ANY(insert_tables) THEN
            EXECUTE format(
                'GRANT USAGE, SELECT ON SEQUENCE %I.%I TO %I',
                target_schema,
                object_name,
                runtime_role
            );
        END IF;

        FOREACH privilege_name IN ARRAY ARRAY['USAGE', 'SELECT', 'UPDATE'] LOOP
            expected_privilege :=
                sequence_table_name = ANY(insert_tables)
                AND privilege_name IN ('USAGE', 'SELECT');
            SELECT has_sequence_privilege(runtime_oid, object_oid, privilege_name)
              INTO actual_privilege;
            IF actual_privilege IS DISTINCT FROM expected_privilege THEN
                RAISE EXCEPTION
                    'Runtime role % has unexpected sequence % privilege state on %.%',
                    runtime_role,
                    privilege_name,
                    target_schema,
                    object_name;
            END IF;
        END LOOP;
    END LOOP;

    -- Trigger execution does not require direct EXECUTE on the trigger function.
    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON FUNCTION %I.funhouse_reject_consents_mutation() FROM %I',
        target_schema,
        runtime_role
    );

    IF NOT has_database_privilege(runtime_oid, current_database(), 'CONNECT')
       OR has_database_privilege(runtime_oid, current_database(), 'CREATE') THEN
        RAISE EXCEPTION
            'Runtime role % must have CONNECT but not CREATE on database %',
            runtime_role,
            current_database();
    END IF;

    IF NOT has_schema_privilege(runtime_oid, schema_oid, 'USAGE')
       OR has_schema_privilege(runtime_oid, schema_oid, 'CREATE') THEN
        RAISE EXCEPTION
            'Runtime role % must have USAGE but not CREATE on schema %',
            runtime_role,
            target_schema;
    END IF;

    IF has_function_privilege(
        runtime_oid,
        format('%I.funhouse_reject_consents_mutation()', target_schema)::regprocedure,
        'EXECUTE'
    ) THEN
        RAISE EXCEPTION
            'Runtime role % must not directly execute %.funhouse_reject_consents_mutation()',
            runtime_role,
            target_schema;
    END IF;
END;
$$;
