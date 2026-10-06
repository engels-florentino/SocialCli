# Scheduling and evidence

See `socialcli native-schedule --help` for the complete workflow. Preparing a
manifest with `--dry-run` shows the proposal; persistence or approval requires
its digest. Content, account or file changes invalidate approval.

States distinguish preparation, native delivery, handoff and reconciliation.
A local queue does not prove that a post appears in the provider's calendar.
Verify its identifier and remote result before retrying an uncertain delivery.
Do not use a manual marker as proof of publication without evidence.

Compatibility commands `schedule`, `schedule-status` and `run-due` preserve
older queues. Remote runner configuration belongs to each operator; another
creator's server or calendar is not distributed.
