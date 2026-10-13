-- Empty dedicated database only. Never run against another app or existing volume.
\set ON_ERROR_STOP on
DO $$ BEGIN
  IF current_user <> 'socialcli_db_admin' THEN
    RAISE EXCEPTION 'Bootstrap must use the separate socialcli_db_admin role';
  END IF;
END $$;
\getenv auth_password SOCIALCLI_AUTH_DB_PASSWORD
CREATE ROLE socialcli_auth LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOREPLICATION NOBYPASSRLS PASSWORD :'auth_password';
CREATE SCHEMA auth AUTHORIZATION socialcli_auth;
GRANT CREATE ON DATABASE socialcli_auth TO socialcli_auth;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
-- Auth migrations grant read permissions to the conventional postgres role.
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='postgres') THEN
    CREATE ROLE postgres NOLOGIN;
  END IF;
END $$;
ALTER ROLE socialcli_auth IN DATABASE socialcli_auth SET search_path = auth;
