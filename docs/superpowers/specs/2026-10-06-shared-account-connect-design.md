# Shared account connection design

Approved direction: the creator installs SocialCli, opens a browser, authorizes their own social account, chooses the account explicitly and returns to the CLI without registering a provider app or copying tokens. Product and documentation are English. Communication with the project owner is Spanish.

## Architecture

A dedicated HTTPS connection service at https://social.florentino.pro owns provider client credentials, OAuth callbacks, encrypted refresh tokens and connection authorization. The Python CLI remains local and performs existing platform operations, obtaining provider access tokens only in process memory from the service. The service never receives media or runs publication jobs. Existing independent-app authentication remains available.

The service has a single-process deployment with SQLite transactions and an encrypted credential vault. Google/YouTube is the first live connector; Meta Facebook Login and TikTok Web Login use the same protocol. Each connector stays disabled until its own server credentials and callback configuration are present. Production eligibility and provider reviews remain separate from implementation.

## Connection protocol

The CLI creates a random polling secret and submits only its SHA-256 challenge. The service creates a ten-minute authorization session. Opening its browser URL binds that session to a secure HttpOnly SameSite=Lax browser cookie and redirects to the fixed provider OAuth endpoint. A random single-use state binds the callback; Google also uses PKCE. Cancellation, cookie/state mismatches and expired sessions cannot create connections.

After exchange, the server discovers the actual authorized channels, Pages or TikTok profile. It exposes only account identifiers and names to the authenticated polling client. The creator explicitly chooses and confirms an account in the CLI. The server issues a random connection credential once, stores only its hash, and deletes the temporary authorization. That credential grants access only to this platform/account connection. The CLI stores it in a supported OS keyring; it never falls back to plaintext files. Brand-local metadata contains service URL, connection ID and verified account identifiers, never provider refresh tokens or application secrets.

Provider tokens are encrypted at rest using authenticated encryption; encryption keys live outside the database. Expiring Google/TikTok tokens are renewed server-side, with serialized renewal and refresh-token rotation. Meta follows its own expiration rules and may require reconnection. Provider-issued access tokens can outlive disconnect; deletion stops service access but is not a promise of provider-wide revocation. Users can also revoke permissions in the provider's settings.

## Controls

All service endpoints use a fixed HTTPS origin (loopback HTTP is development-only). Provider hosts and callbacks are server-controlled. OAuth state and pairing credentials are single use, constant-time compared and bounded in lifetime. Provider errors and logs never contain credentials or callback queries. Session creation has rate limits and bounded storage. Account selection is checked against discovery results. Partial permissions fail clearly. CLI reconnection never silently replaces a different account. Provider credentials never go in GitHub, HTML, command arguments or installer files.

Connecting never publishes. Existing complete dry-run preview and explicit approval requirements apply to all content operations, including inbox uploads. TikTok Direct Post and analytics are deferred. Google content management and analytics are explicit opt-ins. Meta initially requests publishing/account-discovery permissions only. All tests use fictional users and mocked provider HTTP; live uploads require separate exact-preview approval.

## Deployment

Use the existing social.florentino.pro server and reverse proxy. Run the connection service independently of the private Histopast executor, in a container with no published host port on the existing proxy network, with separate code, environment and state. Preserve existing media routes and retain web/legal backups. Serve matching SocialCli branding and policies on the connection domain. Register HTTPS callbacks only after the service passes local tests and a remote health probe. Do not rotate existing provider secrets or migrate creator tokens automatically.

## Acceptance

A clean creator installation connects without developer credentials. Two creators cannot access each other's connections. OAuth cancellation, forged callbacks, replays, expired sessions and missing scopes fail closed. Account choice is accurate and explicit. Refresh preserves rotated tokens. Keyring failure does not leave a usable orphan connection. Disconnect removes service access and local credentials. Legacy authentication and existing publication protections continue to pass their test suite. Provider rejection or unavailable credentials are reported honestly rather than called production approval.
