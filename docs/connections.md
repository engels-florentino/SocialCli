# Connect your social accounts

SocialCli supports a shared connection service so a creator can authorize an account without registering a provider application or copying a token. The service operator configures the provider apps once. Each integration still depends on provider permissions, account eligibility and approval; a healthy server does not prove public production access.

The hosted service origin is `https://social.florentino.pro`. Check `/healthz` for configured connectors. If a connector is disabled, `connect` fails clearly; use independent-app authentication or wait for the operator to configure it. No shared connector is announced as publicly approved before a real external-creator pilot and provider review.

## Creator workflow

Run inside your creator workspace. Explicitly name the brand:

```sh
socialcli brand new MyBrand
socialcli connect youtube --brand MyBrand
socialcli connections --brand MyBrand --json
```

The command opens the provider's login page. Sign in there, grant the requested permissions, return to the terminal and confirm the displayed account. For Meta, select the Page you administer; Instagram requires a professional account linked to that Page in this integration. TikTok connects for inbox upload only. Connecting does not upload or publish content.

```sh
socialcli connect facebook --brand MyBrand
socialcli connect instagram --brand MyBrand
socialcli connect tiktok --brand MyBrand
```

YouTube requests upload and account discovery by default. Additional capabilities are explicit:

```sh
socialcli connect youtube --brand MyBrand --management --analytics
```

Disconnect an existing shared connection before changing permissions or reconnecting. Initial Google authorization must return a refresh token; if Google returns an incomplete grant, retry consent and check provider configuration. Authorize one YouTube channel in Google's account/channel selection screen.

```sh
socialcli disconnect youtube --brand MyBrand
socialcli connect youtube --brand MyBrand --management
```

Your OS credential store must be available and unlocked. Supported keyring backends are macOS Keychain, Windows Credential Manager, Secret Service, libsecret and KWallet; the underlying CLI currently targets macOS/Linux because other features use POSIX facilities. Plaintext and absent keyrings are rejected. Headless installations need a configured secure system keyring or the existing independent-app authentication mode.

## Credential handling

The connection service stores encrypted provider credentials and refresh tokens. Application secrets and provider refresh tokens never enter the creator's CLI. The local `.secrets/<platform>.json` contains versioned connection metadata; the connection capability lives only in the OS keyring. Provider access tokens are retrieved over HTTPS and remain only in the running CLI process. Their lifetime is determined by the provider, not universally short.

Google/TikTok renewal happens server-side during an operation that permits renewal. Read-only connection status does not renew. Existing identity/Analytics probes also decline implicit renewal. Meta tokens follow Meta's expiry rules and can require a new browser authorization. Revocation, changed permissions and expired refresh credentials always require reconnection.

Pairing lasts ten minutes and is single use. Newly issued connections remain provisional for ten minutes until the CLI saves its keyring credential and binding and activates the connection. Installed connection capabilities expire after ninety days. Expired records are cleaned on new connection attempts and by the deployed daily pruning timer. Disconnect removes server credentials and the local capability; it does not delete posts or guarantee immediate invalidation of previously issued provider access tokens. Revoke SocialCli in the provider's account settings to withdraw the provider authorization too.

## AI agents

A terminal-enabled assistant can run `socialcli connections --brand MyBrand --json` and start `connect`. The creator completes provider login and explicitly confirms the account. Agents must never ask for social passwords, copy keyring credentials into context, or treat account connection as publication approval. Every post still requires a complete `--dry-run` preview and explicit approval for that exact submission.

## Independent applications

`socialcli auth PLATFORM --brand MyBrand` remains available for operators who own their provider applications. Disconnect a shared connection before replacing it with independent credentials. This mode keeps existing files and behavior; no credentials are automatically migrated and provider secrets are never exported from the shared service.

For self-hosting, see [connection service deployment](../deploy/connection-service/README.md). `--service https://your-service.example` selects your own trusted service; `--dev-local` permits HTTP only on loopback during local development. Never use an untrusted service: its operator handles the permissions you authorize.

## Interrupted installation

Catchable cancellation and installation failures restore the previous account configuration and credentials. Keep a private backup before replacing independent-app credentials: forced process termination, power loss or a filesystem failure can interrupt the local installation between file replacements. In that case, restore `accounts.yml` and the affected `.secrets/<platform>.json` from that backup before retrying. An uninstalled server connection expires after ten minutes; a connection activated immediately before a forced termination may need operator cleanup. Do not store backups in git.

## Hosted pilot status (October 6, 2026)

The HTTPS service is deployed at https://social.florentino.pro. Google is configured with a web OAuth client and is in Testing: access is restricted to the project's authorized test users. A real Histopast pilot verified account selection, OS keyring installation, provider identity, Google refresh and service disconnection. A second connection with `--analytics` successfully retrieved an authorized channel report. This does not establish approval for public access or a successful second independent creator test. TikTok is also configured with matching sandbox credentials; its authorized Histopast target completed real Web OAuth, identity verification, renewal and service disconnection. Its production approval and inbox demo remain pending. A dedicated Meta app, SocialCli (`1551964416681979`), was created in the verified Florentino.pro business portfolio. The `florentino.pro` domain is already verified in Meta. The legacy Histopast app remains in its original portfolio; its Facebook and Instagram identities still verify. OAuth callbacks were saved for the new app, but shared Meta connectors remain disabled pending verified app settings, matching credentials, any required login configuration, live pilots, Tech Provider access verification and provider approval.

The service's daily retention timer is installed and its first manual run succeeded. No media was uploaded during these checks.
