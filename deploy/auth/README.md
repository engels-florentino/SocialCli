# Dedicated SocialCli identity

This stack runs only Supabase Auth and PostgreSQL. It has its own network,
volume, JWT and database credentials. It does not reuse the Rent Supabase stack.
No host ports or public management endpoints are published. Only the SaaS
application joins `socialcli-identity` and reaches `http://socialcli-auth:9999`.
Set `SOCIALCLI_AUTH_INTERNAL_HTTP=1` explicitly for that private hop. The web
browser talks to SocialCli over HTTPS; identity tokens stay on the server.

Copy `auth.env.example` to private `auth.env`. Create private `database.env`
with POSTGRES_USER=socialcli_auth, POSTGRES_DB=socialcli_auth and a long random
POSTGRES_PASSWORD matching the Auth database URL. Set a separate random JWT
secret. Keep both env files mode 0600, directories mode 0700, outside Git.
Run `docker compose up -d`. `init.sql` initializes the Auth namespace and role
required by upstream migrations on an empty dedicated database.

SMTP is Stalwart at mail.florentino.pro:587 with verified STARTTLS, the dedicated
access@socialcli.app account and display name SocialCli. The existing Stalwart
relay configuration is preserved. The generated OTP email is served privately
by `templates`, not exposed to the internet. It shares canonical design tokens.

## Server preparation, 2026-10-13

- Location: `/home/blades3/projects/socialcli-saas/auth`.
- Containers: socialcli-auth (GoTrue v2.197.0), socialcli-auth-db (Postgres 17),
  socialcli-auth-templates. All separate from existing services.
- `/health` responded with GoTrue v2.197.0.
- Stalwart v0.16.14 domain socialcli.app ID `d`; dedicated access mailbox created.
- SMTP authentication succeeded with certificate verification; no external
  delivery has been claimed.
- Stalwart outbound strategy was confirmed to relay through SMTP2GO. The
  SocialCli sending domain has been added there; DNS verification is pending.
  The generated Stalwart zone file is not a ready-to-publish DNS configuration:
  configure the actual relay CNAME records plus Stalwart receiving MX, and
  verify sender authorization before testing delivery to an external inbox.

## Operations

Back up this dedicated Postgres database with pg_dump and keep JWT/SMTP
credentials separately. Restore into another isolated stack and exercise OTP
verification before relying on a backup. Monitor Auth health and delivery errors
without logging codes, JWTs or SMTP passwords. Pin and review Auth image updates.
The PostgreSQL image's bootstrap owner is privileged; restrict the runtime role
before public launch and test future Auth migrations with that role.

This preparation is private infrastructure, not a completed public SaaS rollout.

## Runtime schema repair and DNS verification, 2026-10-13

The first real `/otp` test failed before sending mail because runtime queries
looked for `users` in the default search path. Health alone did not detect this.
`init.sql` now sets the dedicated role/database search path to `auth`, matching
Supabase Auth's upstream initialization. Use `DB_NAMESPACE=auth` for the explicit
namespace setting.

On the already initialized private database, the prior migration ledger was in
`public.schema_migrations`. Before changing it, Auth was stopped and a private
custom-format database dump was saved. The 75 previously applied migration
versions were reconciled into `auth.schema_migrations` (20 missing entries),
without inventing versions or resetting tables. Auth was recreated and `/health`
and the user-authorized `/otp` request both returned 200. There were no Auth users
before the test. The provider accepting a request does not prove inbox delivery.
The actual first-email test was requested by the user. The recipient confirmed
delivery to the Gmail inbox and the expected SocialCli sign-in template with a
10-minute expiry. The code is not retained in test evidence. This verifies real
delivery; OTP redemption and the public browser flow still require their own test.

Namecheap now serves MX `10 mail.florentino.pro`, root SPF `v=spf1 mx -all`, and
DMARC `v=DMARC1; p=none`. SMTP2GO verified the domain's DKIM and return-path CNAMEs.
Its outbound route remains the existing Stalwart relay. Changing Email Forwarding
to Custom MX removed Namecheap's managed SPF; it was restored as an independent
host TXT record and verified on both authoritative nameservers. No web A record
was changed.

Upstream initialization reference:
https://github.com/supabase/auth/blob/master/init_postgres.sh
