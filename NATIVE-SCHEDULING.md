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

## Candidate API submission (not a completed live pilot)

`native-schedule submit-api JOB_ID --brand BRAND --eligibility operator-pilot.json`
submits an already persisted and explicitly approved `route=api` job directly from
the customer's machine. It does not copy media onto the SocialCli SaaS server and
rejects a workspace configured for an implicit remote executor. YouTube supports
the existing private-upload/publishAt flow; Facebook currently supports Reels only.
Instagram and TikTok have no automatic native API scheduling route here.

The eligibility file lives inside the explicit brand folder, contains no credentials
and must be created by the operator from real project/account/pilot evidence. Agents
must not invent it, enable flags themselves or use a mock response as evidence:

```json
{
  "version": 1,
  "platform": "facebook",
  "account_id": "EXACT_PAGE_ID",
  "api_enabled": true,
  "page_pilot_verified": true,
  "evidence_ref": "operator-pilot-evidence.txt"
}
```

The evidence reference must name an existing nonempty file in that brand folder.
For YouTube, replace `page_pilot_verified` with both `project_verified` and
`studio_pilot_verified`, and supply the exact channel ID and `platform: youtube`.
This is an operator attestation, not independent proof of Google/Meta approval.
Omitting eligibility leaves the API route closed without uploading anything.

Facebook uploads the original bytes using START/upload/FINISH, finishing as
SCHEDULED rather than PUBLISHED. It checks authenticated Page identity and reads
back the same Reel's account, description, unpublished state and exact time.
The local policy requires whole seconds, more than ten minutes and at most 28 days
ahead, checked again after upload. Supported options are `format: reel` and an
omitted/false `share_to_facebook_story`; unsupported options are rejected.

An accepted response is `pending_verification`, with a recorded remote ID. It is
not proof of Business Suite/Studio calendar visibility; use the existing fresh
observation/reconcile flow. An uncertain attempt retains its checkpoint and must
never be re-uploaded automatically. Comments are separate work after public verification.

New manifests select the customer's date and time with an explicit schedule object:

```json
"schedule": {"mode": "exact", "timezone": "Europe/Madrid"},
"publish_at": "2035-01-03T19:30:00+01:00"
```

Use the actual customer-selected IANA timezone and local UTC offset. Exact requests
never overflow to another time/date; conflicting instants, past times, subsecond
values and invalid DST offsets are rejected. Multiple distinct times per account/day
are supported. Calendar coverage must span the full local day in the selected zone.
This is intent and reservation support; real provider/account pilots remain required.

Omitting `schedule` preserves legacy 09:00 America/New_York, one item/account/day.
Old reservations retain their whole New York date and conflict with exact jobs on
that date. Existing approval digests remain valid. New exact jobs bind their mode,
timezone and instant into the preview digest. Rescheduling stays an explicitly
approved official-UI operation; it retains both reservations until verified. Exact
reschedule preview, approval and submission require fresh destination-account
calendar inventory covering the entire new local day.

The first persisted exact job upgrades that local ledger to schema 3 atomically.
Old clients reject schema 3; keep all writers on the reviewed candidate version.
Read-only commands never upgrade the ledger. No existing jobs, evidence or media
are rewritten, and subsequent writes never downgrade schema 3.

Use `socialcli delivery-options --brand BRAND --json` to inspect product limits and
`socialcli schedule-doctor --brand BRAND --json` to inspect local executor evidence.
The latter exits 1 while supervision is unverified. On persistent customer-owned
Linux systems, `schedule-install` installs a systemd user timer and verifies its
loaded configuration and activation. `schedule-doctor` also requires logout
persistence and a recent executor heartbeat before reporting ready. The installer
does not enable linger automatically; follow its operator instruction if necessary.
`schedule-uninstall --brand BRAND --root WORKSPACE` disables future timer runs and
preserves queue/media. None of this makes an ephemeral AI conversation persistent.

Complete offline input examples (manifest, required calendar inventory, eligibility
and reconciliation observation) ship in `socialcli agent-guide`; the canonical source
is [the packaged guide](socialctl/ui/assets/agents.md#native-scheduling-input-examples-offline).
