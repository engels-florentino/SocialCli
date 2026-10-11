# Shared connection service deployment

This service is independent of a private publication executor. It receives no creator media, publishes no posts and holds no creator workspaces. Run a single instance with the encrypted SQLite vault. A larger deployment needs a database/locking design change.

## Prepare a release

Build the wheel with `uv build`. Copy the Dockerfile, compose.yaml, service.env.example and built wheel into a private server release directory, placing the wheel under `dist/`. Do not transfer source `.env`, creator folders or tokens. Create a state directory owned by the container UID (1000), mode 0700.

Copy service.env.example to service.env outside git and set mode 0600. Generate a Fernet key through the Python cryptography library into that file or a secret manager without printing it. Keep encrypted backups and the key in separate protected storage. Multiple comma-separated encryption keys are supported: newest first for writes, older keys retained for reads. Back up the vault consistently using SQLite's backup API; never copy a live database blindly.

Configure provider application credentials in the environment. A connector remains disabled unless its `SOCIALCLI_ENABLE_*` flag is `1` and its complete credentials are present. Never place provider secrets in Dockerfiles, image layers, command arguments or system logs.

Register these exact HTTPS callbacks in the matching provider web applications:

- Google: `https://social.florentino.pro/oauth/youtube/callback`
- Meta Facebook: `https://social.florentino.pro/oauth/facebook/callback`
- Meta Instagram through Facebook Login: `https://social.florentino.pro/oauth/instagram/callback`
- TikTok Web Login Kit: `https://social.florentino.pro/oauth/tiktok/callback`

The legacy Google/TikTok localhost desktop client is a separate flow. A Google web client is required for the shared service. For TikTok, keep sandbox and production applications/credentials aligned with the environment being demonstrated; never assume a successful production-key call is proof of a sandbox test.

## Run and proxy

The supplied compose file joins the existing `nginx-proxy-manager_default` network and publishes no host port. Adapt the network name for your server. Run `docker compose up -d --build` from the isolated service release directory. The container uses a read-only root, dropped capabilities and a private writable state mount.

The health check must send the configured Host header. Insert nginx-locations.conf into only the social domain's proxy-manager Advanced configuration. Back up its database and existing generated configuration first, validate `nginx -t`, then reload. Keep existing root/static/media routes. Disable both access and error query logging for these OAuth/service locations; the app also disables access/outbound OAuth logs. The 16 KB request-body limit is mandatory. Proxy identities are not trusted from arbitrary forwarded headers: the application currently rate-limits the proxy peer, so the hosted pilot admits five new pairings per minute globally and at most 1,000 active pairing records.

```sh
curl --fail https://social.florentino.pro/healthz
```

Configured connectors are reported separately from approval; never expose a provider credential in this response. The service does not claim production review success.

## Daily pruning and rollback

Run `docker exec socialcli-connections python -m socialctl.connection_service --prune` daily using a separate systemd timer. It removes expired pairing and connection records without calling providers. Do not reuse publication timers.

To roll back, retain the previous image/release and protected env/state. Disable new proxy routes, validate/reload nginx, and stop the new service. Restore web pages from the pre-deployment backup if necessary. Do not touch the private executor, existing creator secrets, queues, approved schedules or media.

## Acceptance

Verify HTTPS health, disabled-connector errors, query-free logs and existing public media byte hashes. Complete one real browser connection using an authorized test creator; confirm account identity and status without publishing. Then test a second independent creator and permission revocation. Upload demos require an exact dry-run preview and explicit approval. The repository's mocked integration tests establish code behavior, not a completed live pilot or platform approval.


### Agent connection release 0.2.1

Deploy the preview-resistant connection server before rolling out the client. Existing clients can open its landing page and the creator presses Connect; client 0.2.1 also accepts older servers that redirect immediately. Neither behavior approves publication. Build with exactly one SocialCli wheel in the Docker build context.

For noninteractive account binding, first obtain the creator’s approval for the exact account and brand, then run `socialcli connect PLATFORM --brand MyBrand --no-browser --account-id ACCOUNT_ID --yes`. Keep the process running while handing the link directly to the creator. The CLI checks its secure credential store before OAuth. Missing confirmation arguments fail before a link is issued. Independent-credential replacement additionally requires `--replace-independent`.

Catchable interruption restores previous local credentials and binding. Forced termination or power loss can interrupt installation between durable file writes; check `socialcli connections --brand MyBrand --json` and restore the affected account metadata from a private backup before retrying if it is inconsistent. Do not repeat an uncertain installation blindly. Provisional server connections expire if activation was not completed; cleanup failure is reported explicitly.
