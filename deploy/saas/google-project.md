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

## Ownership verified — October 15, 2026

Added the Google verification TXT at Namecheap without removing existing TXT records. Public DNS returned the record and Search Console confirmed Ownership verified using Domain name provider; the property overview is `sc-domain:socialcli.app` under the project administrator account. Keep this TXT record. The Google audience remains External / Testing. This resolves domain ownership, not production approval.

## Backup isolation gate — October 15, 2026

Verified privately that the active rclone backup client belongs to project `socialcli`, the same project as the customer Web client. Google revocation for the same user can affect all clients in that project. The new project `socialcli-operator-backups` (SocialCli Backups) was created in Google Cloud; its OAuth configuration is prepared through the final Google User Data Policy acceptance screen. Acceptance, client creation, owner authorization, isolated Drive copy verification and migration remain incomplete. Do not deploy the revocation release until the backup migration succeeds. Existing backup credentials and the running rc8 service are unchanged.
