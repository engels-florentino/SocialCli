-- One-time conversion of this dedicated legacy cluster only.
-- Run as the separately verified administrator using private environment values.
\set ON_ERROR_STOP on
\getenv auth_password SOCIALCLI_AUTH_DB_PASSWORD
BEGIN;
DO $$ BEGIN
  IF current_user <> 'socialcli_db_admin' OR current_database() <> 'socialcli_auth'
     OR NOT EXISTS (SELECT FROM pg_roles WHERE rolname='socialcli_auth' AND oid=10)
     OR EXISTS (SELECT FROM pg_roles WHERE rolname='socialcli_bootstrap')
     OR EXISTS (SELECT FROM pg_auth_members WHERE member=10) THEN
    RAISE EXCEPTION 'Unexpected cluster, role or memberships; inspect before migration';
  END IF;
END $$;
ALTER DATABASE socialcli_auth OWNER TO socialcli_db_admin;
ALTER DATABASE postgres OWNER TO socialcli_db_admin;
ALTER DATABASE template0 OWNER TO socialcli_db_admin;
ALTER DATABASE template1 OWNER TO socialcli_db_admin;
-- PostgreSQL forbids removing SUPERUSER from the bootstrap role (OID 10).
-- Preserve it as a disabled login, then recreate the application's login.
ALTER ROLE socialcli_auth RENAME TO socialcli_bootstrap;
CREATE ROLE socialcli_auth LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOREPLICATION NOBYPASSRLS PASSWORD :'auth_password';
ALTER ROLE socialcli_auth IN DATABASE socialcli_auth SET search_path=auth;
GRANT CONNECT,TEMPORARY,CREATE ON DATABASE socialcli_auth TO socialcli_auth;
ALTER SCHEMA auth OWNER TO socialcli_auth;
-- Never REASSIGN OWNED from OID 10: it owns the system catalogs too.
DO $$ DECLARE obj record; BEGIN
  FOR obj IN
    SELECT c.relkind,format('%I.%I',n.nspname,c.relname) AS name
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='auth' AND c.relkind IN ('r','p','v','m','S')
    ORDER BY CASE WHEN c.relkind='S' THEN 1 ELSE 0 END
  LOOP
    EXECUTE 'ALTER ' || CASE obj.relkind
      WHEN 'S' THEN 'SEQUENCE ' WHEN 'v' THEN 'VIEW '
      WHEN 'm' THEN 'MATERIALIZED VIEW ' ELSE 'TABLE ' END
      || obj.name || ' OWNER TO socialcli_auth';
  END LOOP;
  FOR obj IN
    SELECT p.oid::regprocedure AS name FROM pg_proc p
    JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='auth' AND p.prokind IN ('f','p')
  LOOP EXECUTE 'ALTER ROUTINE ' || obj.name || ' OWNER TO socialcli_auth'; END LOOP;
  FOR obj IN
    SELECT format('%I.%I',n.nspname,t.typname) AS name FROM pg_type t
    JOIN pg_namespace n ON n.oid=t.typnamespace
    WHERE n.nspname='auth' AND t.typtype IN ('e','d')
  LOOP EXECUTE 'ALTER TYPE ' || obj.name || ' OWNER TO socialcli_auth'; END LOOP;
END $$;
-- This is the old Auth migration ledger, not any other application's table.
ALTER TABLE public.schema_migrations OWNER TO socialcli_auth;
ALTER ROLE socialcli_bootstrap NOLOGIN;
COMMIT;
