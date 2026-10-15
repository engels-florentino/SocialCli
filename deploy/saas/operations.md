# Private SaaS maintenance and diagnostics

Candidate implementation; these commands and timers are not active on the public
0.2.6 service. Deploy the reviewed SaaS image and private configuration first.

The five-minute maintenance worker reconciles Stripe, completes requested account
deletions, prunes expired local records and processes trial reminders. It does not
publish social content or reconnect social accounts.

```sh
python -m socialctl.saas.maintenance
python -m socialctl.saas.operations /data/connections.sqlite3
```

Both commands run inside the service container with its existing private environment.
The diagnostics command requires `SOCIALCLI_ENCRYPTION_KEYS` to classify saved
renewal states. Never pass keys as command-line arguments. Keep stdout and the
systemd journal private. No new HTTP endpoint exposes this report.

## Scheduling and deadlines

Install the existing `socialcli-saas-candidate-billing.service` and `.timer` only with the
SaaS deployment. Billing, deletion and reminders each receive a separate 45-second
cooperative budget. Stripe event processing receives at most half its phase, so
periodic reconciliation also gets a turn. Network-operation timeouts shrink to
remaining budget; workers stop claiming jobs when it expires. These are not
strict wall-clock guarantees for every library or database operation.

The one-shot Python process has an independent 230-second termination timer
(exit 124). This is necessary because stopping the host's `docker exec` client
alone does not reliably terminate its process inside the container. Systemd's
240-second limit remains an outer guard. Never run this entry point inside the
web server process. Durable leases recover after a stopped worker; SMTP DATA
already started becomes uncertain and is never automatically replayed.

The worker writes an atomic mode-0600 heartbeat beside its existing database. It
refuses to create a missing database. Successful completion, attention, running
and failure are distinct. Unexpected exceptions produce only a fixed diagnostic,
not exception bodies, SQL, email addresses or tokens.

## Interpreting the private report

Exit 0 means the inspected local operational state is healthy. Exit 1 requests
operator attention. The read-only inspector neither initializes/migrates a database
nor contacts Stripe, Auth, SMTP or a social provider. Its SQLite work has a five-second
cooperative limit and a one-second lock wait. It reports only fixed labels and counts.

- Missing/invalid heartbeat: `maintenance_unknown`.
- Running for over 240 seconds: `maintenance_stalled`.
- No heartbeat for over 900 seconds: `maintenance_stale`.
- Failed or incomplete run: `maintenance_failed` or `maintenance_attention`.
- Billing review, jobs overdue by 15 minutes, uncertain/rejected mail and uncertain
  or misconfigured renewal: corresponding counts and issue labels.
- Renewal needed, pending or explicitly requiring reconnection: counts only;
  never an instruction to replay token refresh or reconnect automatically.

A due counter describes the whole current queue, including work omitted by the
batch limit. Billing due is event work plus customer reconciliation work, so it
is not a count of unique users. Already accepted SMTP replies and completed Auth
deletions remain acknowledged even if they arrive at the deadline.

This report does not prove current external-service availability, backup freshness,
off-host recoverability or successful delivery of alerts. Check Auth's internal
health endpoint and the public service health separately during rollout. An external
scheduler must poll diagnostics even when the maintenance timer itself stops.
The candidate has a separate hourly encrypted backup timer; see
[backup and recovery](backup-restore.md). Automatic alert delivery and an off-host
recovery destination remain rollout work; no operational email has been
authorized/sent by this module.

## Isolated candidate scheduling

On Florentino, `socialcli-saas-candidate-billing.service` and `.timer` target only
`socialcli-saas-candidate`. The five-minute timer is enabled. The reviewed
configuration accepts Stripe test keys, disables provider connections and disables
reminder delivery. Both manual execution and actual systemd execution exited zero;
private diagnostics reported no outstanding work. The public 0.2.6 container and
its schedules were not changed. This does not verify public webhook delivery or
external alerting.
