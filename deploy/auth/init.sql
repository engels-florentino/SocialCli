-- Dedicated database owned by this Auth instance. Never run against another app.
CREATE SCHEMA IF NOT EXISTS auth AUTHORIZATION socialcli_auth;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
-- Auth migrations grant read permissions to the conventional postgres role.
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='postgres') THEN
    CREATE ROLE postgres NOLOGIN;
  END IF;
END $$;
