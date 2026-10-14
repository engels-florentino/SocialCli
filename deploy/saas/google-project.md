# Isolated Google project — 14 October 2026

Project ID: `socialcli`. Existing Desktop client retained; a separate Web
application client named **SocialCli SaaS** was created for the hosted service.
Client ID and secret in the private root `.env` match the downloaded Web client
configuration; values are deliberately omitted here.

Authorized redirects:
- `https://socialcli.app/oauth/youtube/callback`
- `https://pilot.socialcli.app/oauth/youtube/callback`

Consent scopes saved: `youtube.readonly`, `youtube.upload`, `youtube.force-ssl`,
`yt-analytics.readonly`, each under `https://www.googleapis.com/auth/`.
YouTube Data API v3 and YouTube Analytics API were observed already enabled.

Branding uses SocialCli, the existing project logo, and authorized domain
`socialcli.app`. Homepage `/`, privacy `/privacy.html` and terms `/terms.html`
were checked over HTTPS with status200 before saving. Existing owner support
and developer contact retained. External/Testing retained; owner account added
as a test user and observed in the saved audience table.

The Web credentials were staged through SSH in the root-private candidate
`service.env` as `SOCIALCLI_GOOGLE_CLIENT_ID` and
`SOCIALCLI_GOOGLE_CLIENT_SECRET`; other settings preserved. No candidate restart
was performed: credentials load on its next controlled recreation. YouTube
remains disabled until the pilot is reachable. Public0.2.6 credentials and
existing grants were not replaced. Both containers remained healthy.

This is configuration evidence, not an end-to-end OAuth test or Google approval.
Pilot DNS/TLS, owner connection test, domain ownership verification and Google
production/verification remain pending. No Google Drive permission was added;
backup authorization remains a separate operational requirement.

Update: owner subsequently authorized Drive backups with the separate Desktop
client and `drive.file`. This does not add Drive to SaaS connection requests.
See backup-restore.md for verified upload and hourly-copy evidence and limits.
