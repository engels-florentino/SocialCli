# Connect your social accounts

SocialCli supports a shared connection service so a creator can authorize an account without registering a provider application or copying a token. The service operator configures the provider apps once. Each integration still depends on provider permissions, account eligibility and approval; a healthy server does not prove public production access.

The hosted service origin is `https://social.florentino.pro`. Check `/healthz` for configured connectors. If a connector is disabled, `connect` fails clearly; use independent-app authentication or wait for the operator to configure it. No shared connector is announced as publicly approved before a real external-creator pilot and provider review.

## Hosted access today

The CLI is publicly installable; hosted provider access remains restricted. TikTok is in sandbox and permits only authorized target accounts. Google is in Testing for authorized test users. Meta is unpublished and limited to development access. Installing SocialCli does not add your account to those allowed users. If your account is not eligible, wait for production availability or use a service whose operator has the required provider access. Independent-app mode requires your own provider setup and remains subject to provider approval.

## Creator workflow

Run inside your creator workspace. Explicitly name the brand:

```sh
socialcli brand new MyBrand
socialcli connect youtube --brand MyBrand
socialcli connections --brand MyBrand --json
```

Complete your brand's `brand.md` first. The `connect` command saves selected account identifiers in `accounts.yml` automatically; you do not need to fill those identifiers manually or provide a client ID, client secret or provider token in shared mode.

The command opens the provider's login page. Sign in there, grant the requested permissions, return to the terminal and confirm the displayed account. For Meta, select the Page you administer; Instagram requires a professional account linked to that Page in this integration. TikTok connects for inbox upload only. Connecting does not upload or publish content.

```sh
socialcli connect facebook --brand MyBrand
socialcli connect instagram --brand MyBrand
socialcli connect tiktok --brand MyBrand
```

YouTube requests upload and account discovery by default. Additional capabilities are explicit:

```sh
socialcli connect youtube --brand MyBrand --management --analytics
socialcli connect facebook --brand MyBrand --analytics
socialcli connect instagram --brand MyBrand --analytics
```

For Facebook, `--analytics` adds `read_insights`; for Instagram with Facebook Login it adds `instagram_manage_insights`. Existing discovery and publication permissions remain unchanged. The additional read permissions must be available in the operator app and granted during browser consent. TikTok analytics is not supported by this connection flow.

Disconnect an existing shared connection before changing permissions or reconnecting. Initial Google authorization must return a refresh token; if Google returns an incomplete grant, retry consent and check provider configuration. Authorize one YouTube channel in Google's account/channel selection screen.

```sh
socialcli disconnect youtube --brand MyBrand
socialcli connect youtube --brand MyBrand --management
```

Your OS credential store must be available and unlocked. Supported keyring backends are macOS Keychain, Windows Credential Manager, Secret Service, libsecret and KWallet; the underlying CLI currently targets macOS/Linux because other features use POSIX facilities. Plaintext and absent keyrings are rejected. Headless installations can explicitly select the encrypted capability store documented below, configure a secure system keyring, or use independent-app authentication.

## Credential handling

The connection service stores encrypted provider credentials and refresh tokens. Application secrets and provider refresh tokens never enter the creator's CLI. The local `.secrets/<platform>.json` contains versioned connection metadata; the connection capability lives only in the OS keyring. Provider access tokens are retrieved over HTTPS and remain only in the running CLI process. Their lifetime is determined by the provider, not universally short.

Google/TikTok renewal happens server-side during an operation that permits renewal. Read-only connection status does not renew. Existing identity/Analytics probes also decline implicit renewal. Meta tokens follow Meta's expiry rules and can require a new browser authorization. Revocation, changed permissions and expired refresh credentials always require reconnection.

Pairing lasts ten minutes and is single use. Newly issued connections remain provisional for ten minutes until the CLI saves its keyring credential and binding and activates the connection. Installed connection capabilities expire after ninety days. Expired records are cleaned on new connection attempts and by the deployed daily pruning timer. Disconnect removes server credentials and the local capability; it does not delete posts or guarantee immediate invalidation of previously issued provider access tokens. Revoke SocialCli in the provider's account settings to withdraw the provider authorization too.

## AI agents

AI agents must use `socialcli connect PLATFORM --brand MyBrand --no-browser` and give the printed link directly to the user. Never open, fetch, validate, preview or follow that link with a browser, HTTP client or link-unfurl tool: the user must open it and press Connect on the landing page to start a single-use authorization bound to their browser. Keep the CLI process running while the user signs in and confirms the account. The link expires after ten minutes. If it was already visited, expired or cancelled, restart `connect --no-browser` to generate a fresh link; do not reuse it. Account confirmation still requires the user’s explicit approval.

A terminal-enabled assistant can run `socialcli connections --brand MyBrand --json` and start `connect`. The creator completes provider login and explicitly confirms the account. Agents must never ask for social passwords, copy keyring credentials into context, or treat account connection as publication approval. Every post still requires a complete `--dry-run` preview and explicit approval for that exact submission.

## Independent applications

`socialcli auth PLATFORM --brand MyBrand` remains available for operators who own their provider applications. Disconnect a shared connection before replacing it with independent credentials. This mode keeps existing files and behavior; no credentials are automatically migrated and provider secrets are never exported from the shared service.

For self-hosting, see [connection service deployment](../deploy/connection-service/README.md). `--service https://your-service.example` selects your own trusted service; `--dev-local` permits HTTP only on loopback during local development. Never use an untrusted service: its operator handles the permissions you authorize.

## Interrupted installation

Catchable cancellation and installation failures restore the previous account configuration and credentials. Keep a private backup before replacing independent-app credentials: forced process termination, power loss or a filesystem failure can interrupt the local installation between file replacements. In that case, restore `accounts.yml` and the affected `.secrets/<platform>.json` from that backup before retrying. An uninstalled server connection expires after ten minutes; a connection activated immediately before a forced termination may need operator cleanup. Do not store backups in git.

## Hosted pilot status (October 6, 2026)

The HTTPS service is deployed at https://social.florentino.pro. Google is configured with a web OAuth client and is in Testing: access is restricted to the project's authorized test users. A real Histopast pilot verified account selection, OS keyring installation, provider identity, Google refresh and service disconnection. A second connection with `--analytics` successfully retrieved an authorized channel report. This does not establish approval for public access or a successful second independent creator test. TikTok is also configured with matching sandbox credentials; its authorized Histopast target completed real Web OAuth, identity verification, renewal and service disconnection. Its production approval and inbox demo remain pending. A dedicated Meta app, SocialCli (`1551964416681979`), was created in the verified Florentino.pro business portfolio. The `florentino.pro` domain is already verified in Meta. The legacy Histopast app remains in its original portfolio; its Facebook and Instagram identities still verify. The new app has the SocialCli logo, saved OAuth callbacks and matching server credentials. Both shared Meta connectors are enabled for the development pilot. Real Facebook and Instagram pilots verified selection of only Histopast, the exact account identities, OS keyring installation and service disconnection. No content was published, and publishing permissions were not exercised. The app remains unpublished; Tech Provider access verification, provider review and an independent creator pilot remain pending. Service disconnection does not revoke the provider authorization.

The service's daily retention timer is installed and its first manual run succeeded. No media was uploaded during these checks.

## Optional Meta management access

Use `--management` for comment management and webhook permissions. Combine it with `--analytics` to request statistics as well:

```bash
socialcli connect facebook --brand MyBrand --management --analytics
socialcli connect instagram --brand MyBrand --management --analytics
```

Facebook adds `pages_read_user_content`, `pages_manage_engagement` and `pages_manage_metadata`. Instagram adds `instagram_manage_comments` and `pages_manage_metadata`. These permissions are optional and are checked during the OAuth callback. If a requested permission is declined, SocialCli rejects the incomplete connection. Existing connections must be disconnected and reconnected to grant additional permissions. Update the client before using these flags with Meta.

Permission consent does not subscribe webhooks or approve comments, moderation or publication actions. Each action keeps its existing approval requirements. The Meta developer app also needs the corresponding permissions and, for external creators, the required Advanced Access and review. This does not enable advertising, shopping or private-message features.


For an agent without interactive stdin, obtain the user’s explicit authorization for the exact account ID and brand first, then use `socialcli connect PLATFORM --brand MyBrand --no-browser --account-id ACCOUNT_ID --yes`. `--yes` confirms this account connection only; it does not authorize publication. Never guess an ID or use its position in a returned list. Replacing independent credentials additionally requires `--replace-independent`. Without these explicit arguments, noninteractive execution stops before creating an authorization link.


### Agent connection release 0.2.1

Deploy the preview-resistant connection server before rolling out the client. Existing clients can open its landing page and the creator presses Connect; client 0.2.1 also accepts older servers that redirect immediately. Neither behavior approves publication. Build with exactly one SocialCli wheel in the Docker build context.

For noninteractive account binding, first obtain the creator’s approval for the exact account and brand, then run `socialcli connect PLATFORM --brand MyBrand --no-browser --account-id ACCOUNT_ID --yes`. Keep the process running while handing the link directly to the creator. The CLI checks its secure credential store before OAuth. Missing confirmation arguments fail before a link is issued. Independent-credential replacement additionally requires `--replace-independent`.

Catchable interruption restores previous local credentials and binding. Forced termination or power loss can interrupt installation between durable file writes; check `socialcli connections --brand MyBrand --json` and restore the affected account metadata from a private backup before retrying if it is inconsistent. Do not repeat an uncertain installation blindly. Provisional server connections expire if activation was not completed; cleanup failure is reported explicitly.

## Headless encrypted capability storage

The supported OS keyring remains the default. SocialCli never silently falls back.
Linux/macOS servers can explicitly install `socialcli[headless-credentials]` and
select authenticated encrypted-file storage. Only creator connection capabilities
are stored; provider application secrets remain on the connection service.

Create a private directory outside every Git repository (`chmod 700`) and an
external Fernet key file (`chmod 600`). Generate the key using Python's
`cryptography.fernet.Fernet.generate_key()` and write it directly to that file;
never put the key value in a command argument, chat, `.env` or Git.

```sh
socialcli credentials configure --backend encrypted-file \
  --store-file /secure/socialcli/capabilities.json \
  --key-file /secure/socialcli/master.key --brand Example --yes
socialcli credentials status --brand Example
```

Both files require a directory owned by the current user with mode 0700; files
must have mode 0600. Symlinks and hard links are rejected. Configuration stores
only paths/backend in the brand's `.secrets/credential-store.json`. Keep key backups
separate from encrypted capability backups. Losing every copy of the key makes
these capabilities unrecoverable; reconnect accounts to create new capabilities.

For an already connected brand use `credentials migrate --to encrypted-file` with
the same path options, or `credentials migrate --to keyring`. Migration verifies
all destination capabilities before switching configuration and deleting sources.
A failure can leave redundant encrypted destination records; the source stays
active until the configuration switches. After a reported source-cleanup failure,
the verified destination is active and redundant source records need removal.

Rotate with `credentials rotate --new-key-file /secure/socialcli/next.key --brand
Example --yes`. Supply a different protected key. Rotation keeps an encrypted
`.before-rotation` backup. Keep the old key separately until that backup is archived.
A process/power failure between store replacement and configuration switching may
require restoring that backup to the store path with mode 0600 and selecting the
old key. Ordinary caught failures restore the previous encrypted store. Archive
or remove the recovery generation before another rotation. Disconnect uses the
selected backend and does not require switching back to keyring.

Use one encrypted store per brand. Store/key paths must be outside the creator
workspace as well as Git repositories, so they cannot replace configuration,
platform metadata, locks or recovery files. A key cannot use a store's lock or
backup path. Shared stores across brands are rejected before configuration.


## Agent reliability release 0.2.3

Client 0.2.3 uses the existing version 1 broker protocol and is compatible with the
preview-resistant server deployed with client 0.2.1. No new provider permissions
are introduced by this reliability release. The local encrypted backend is an
explicit opt-in; existing keyring capabilities are not migrated automatically.
See [capabilities and limits](capabilities.md) and the [agent guide](ai-agents.md).
