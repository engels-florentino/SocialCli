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

## Backup isolation — October 15, 2026

The former operator Desktop client belonged to the customer project `socialcli`.
Because same-user Google revocation can affect all clients in one project, the
operator explicitly approved a separate project, its Google API Services User
Data Policy, Desktop credential creation, and production activation.

Project `socialcli-operator-backups` (**SocialCli Backups**) is now **In production**,
verified by reloading Google's Audience screen. Its Desktop client is
**SocialCli Server Backup**. Drive API is enabled; Data Access contains only the
non-sensitive `drive.file` scope. Branding links `socialcli.app` and its policies;
developer contact is `admin@socialcli.app`. The customer project remains Testing.

The owner's Drive authorization completed. Private token inspection verified
that its audience is the new client, its only scope is `drive.file`, and a refresh
token exists. Credentials were saved root-only on florentino.pro. No credential
values belong in this document. The new app initially saw zero Drive root items;
its app-specific permission does not expose the former client's files.

Both encrypted repositories were copied directly from the server to the isolated
Drive destination and checked against the local repositories. The active backup
configuration now uses `socialcli_backup_isolated:SocialCli-Backups/candidate`.
A complete scheduled-service invocation succeeded with verified remote mirroring
and a healthy freshness report. No backup repository was downloaded to the Mac.
Old app-visible encrypted backup files were then removed permanently after a
second check of the new repositories and distinct folder IDs. Recursive file
listings of both old repositories returned zero files. Google denied deleting
whole folders (`appNotAuthorizedToChild`), so directory shells were preserved:
`drive.file` cannot establish the absence of unseen children. The active rclone
configuration now contains only the isolated remote.

This operator-only production switch is not customer OAuth verification or
approval. No customer grant was revoked and the running app remains rc8.


After the verified backup migration, `drive.file` was removed from the customer
project's declared Data Access scopes. Saving and reloading the Google console
confirmed exactly `youtube.readonly`, `youtube.upload`, `youtube.force-ssl`, and
`yt-analytics.readonly`; no restricted scopes. This changes the declared review
scope inventory, not existing user grants. The customer Audience was independently
checked as External / Testing. The isolated rclone configuration was added to
the encrypted recovery backup inventory so the new authorization can be restored.
