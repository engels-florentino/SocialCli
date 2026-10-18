# Independent availability and backup monitoring

The owner selected Google Cloud Monitoring in project `socialcli`. The HTTPS
check of `/healthz` runs globally every five minutes with TLS verification,
a ten-second timeout and the `socialcli-connections` response marker. Its alert
uses the owner's external Gmail channel, independent of the application host.
This is separate from backup freshness and does not certify provider approval.

## Backup status boundary

A host-only exporter reads the existing verified local/Drive receipts using
`backup_alert.issues`. It publishes only a fixed check name, evaluation timestamp,
completion timestamp and healthy/unhealthy status. It never exports repository
names, snapshot IDs, file paths, creator identifiers, credentials or error text.
No backup start, local-only receipt or HTTP health success renews completion.

A separate unprivileged HTTP process reads only that minimal exported file.
It has no backup, OAuth, database or mail credentials. Each GET independently
rejects a missing/malformed/future receipt, an evaluation older than ten minutes,
or a completion older than two hours. A stopped exporter therefore cannot leave
a permanently healthy static response. Cache-Control is no-store; requests do
not modify anything. Only the fixed `/monitoring/backup` route is public through
the existing TLS proxy. Other paths and methods are rejected.

Google checks this route separately, matching HTTP 200 and `backup-current`.
If the host, proxy or exporter stops, Google still detects the failure and sends
the notification using its own infrastructure. The existing host mail alerts
remain as an additional channel. No new authentication credential is needed:
the public response is only an opaque service-health result. Authentication
failure tests are consequently inapplicable to this status-only endpoint.

The exporter runs every minute; it does not run backups or send mail. Its
systemd unit can read the private receipts but writes only to a dedicated export
directory. The HTTP process mounts that directory read-only and runs without
root, capabilities, host ports or access to the private backup configuration.
Use the already-reviewed candidate image solely as the Python runtime, with its
entry point overridden and no application environment variables.

## Isolated alert drill

Use an isolated fixture receipt, leaving production backup receipts untouched.
For the October 18 drill, the existing service check temporarily targeted the
fixture; the unchanged backup check continued to detect whole-host failure.
The fixture expired, an incident opened, and the owner received Google's email.
Renewing the fixture automatically recovered the incident and delivered the
recovery email. The service check was then restored to `/healthz`, its original
content match and five-minute frequency. The temporary route, container and
fixture were removed. A future drill must restore configuration immediately
after recovery to avoid a second fixture-expiry notification.

## Recorded provisioning

2026-10-18: Google saved `SocialCli pilot HTTPS health` and its policy
`SocialCli — service unavailable`. The creation test returned HTTP 200 in
258 ms. The deployed backup exporter runs every minute and the isolated HTTP
reader exposes `/monitoring/backup`. The second global five-minute check is
`SocialCli verified off-host backup`, with policy
`SocialCli — verified backup unavailable or stale`. Both policies use the
independent owner email channel and notify on recovery.

The isolated drill delivered an alert at 17:13 UTC and automatic recovery at
17:17 UTC, after 3 minutes 52 seconds. Private operational receipts include the
incident and both email screenshots. This demonstrates external detection and
notification without the production mailer; it is not a full-host outage drill.
After removing the fixture, `/healthz` and `/monitoring/backup` returned HTTP 200;
`/monitoring/drill` returned 404. Production backups were not modified.

Cost: the planned checks fit within the published one-million monthly uptime
execution free allowance; each region counts as one execution. No billing
activation or paid subscription was performed. See
[Google Observability pricing](https://cloud.google.com/products/observability/pricing).
