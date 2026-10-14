# SocialCli for AI agents — Candidate 0.3.0rc1

Status: this is the private SaaS candidate guide. Public PyPI availability,
hosted SaaS deployment and Muse/Grok pilot completion are not yet confirmed.
Do not claim that an installation command succeeded without checking its result.
The public 0.2.6 service remains separate until the operator completes rollout.

SocialCli gives terminal-enabled agents tools for creator-owned social accounts.
You need a persistent shell workspace, Python 3.11+, macOS or Linux, and protected
credential storage. SocialCli does not include a model or MCP server. Provider
availability depends on account eligibility; Google production is still pending.

## Install the reviewed candidate

For an operator-supplied wheel whose SHA256 you have verified:

    uv tool install --python 3.12 /absolute/path/socialcli-0.3.0rc1-py3-none-any.whl
    socialcli --version
    socialcli --help

Without uv, create a virtual environment and install the same reviewed wheel:

    python3 -m venv .venv
    .venv/bin/python -m pip install /absolute/path/socialcli-0.3.0rc1-py3-none-any.whl
    .venv/bin/socialcli --version

After a stable release is actually available on PyPI, the normal command will be
`uv tool install socialcli`; do not use it as proof this candidate is published.
For headless agents install the `headless-credentials` extra on the reviewed wheel,
then explicitly configure encrypted-file storage with an external 0600 key in a
0700 directory. Never silently fall back to plaintext storage. The operator keeps
key backups separate. The protected storage procedure is documented at
https://socialcli.app/connections.html#headless-encrypted-capability-storage.

If uv is missing, use the virtual-environment route or its official installation
instructions at https://docs.astral.sh/uv/getting-started/installation/.
If Python is older than 3.11, select a supported interpreter first. If the installed
tool is not on PATH, use its absolute executable or `uv tool update-shell` and
start a new shell. Video validation needs ffprobe (from FFmpeg); missing ffprobe
must be resolved before a video submission. Never generate or reencode media.

## Authorize the installation, then the social account

Ask for the brand name; never infer it. Replace MyBrand below with that exact name.
Keep the workspace outside the application repository:

    socialcli brand new MyBrand
    socialcli account login --brand MyBrand --no-browser --label "My AI assistant"

Give the printed URL directly to the user. Do not open, fetch, preview or follow
it yourself. Keep the process alive with a runner timeout longer than 30 minutes.
The user signs in by email and explicitly approves this installation. Do not ask
for their OTP, password, session cookie or provider token in chat.

    socialcli connect youtube --brand MyBrand --no-browser

The provider step has its own 10-minute window. The user grants access and chooses
the exact account in their browser; the SaaS flow does not require guessing an
account ID or interactive terminal confirmation. Connecting does not authorize
publication. Do not use --yes as blanket authorization.

The trial lasts 14 days from the first successful connection, without a card.
Then hosted access costs USD 10 per month. Checkout opens after the trial ends.
Only the user's browser completes payment. The current candidate uses Stripe
test mode; it is not collecting live subscriptions.

## Persist and recover

Keep the brand folder, its .secrets metadata and the configured credential backend
between conversations. Never upload credentials or keys to Git. A changed path
can change OS-keyring lookup; use the same absolute SOCIALCLI_ROOT or --root.
Ephemeral agents must arrange protected persistent storage before authorizing.
A fresh conversation alone does not require reconnecting YouTube.

    socialcli account status --brand MyBrand --json
    socialcli connections --brand MyBrand --json

Use `account attach PLATFORM --brand MyBrand` to attach an existing owner-approved
connection after signing in on a second installation. Use `account recover
PLATFORM --brand MyBrand` after interrupted local installation; do not blindly
repeat OAuth. `account rotate --brand MyBrand` renews this installation's local
credentials. A network failure is not proof the social account was revoked.
Check --help on the installed version before using a proposed flag.

Preserve stdout JSON separately from stderr progress. Give read operations their
own --timeout and a longer runner limit. Exit 1 may include verified partial data;
never treat an incomplete empty inbox as proof there are no comments.

## Publication gate

Use only media supplied by the user. Show the complete --dry-run preview and wait
for explicit approval of that exact account and content before any publication.
Keep confirmed, pending and uncertain results distinct. Never repeat an uncertain
write. Sign-in, subscription and connection approval do not approve publication.

## Canonical publishing workflow

Read this installed guide with `socialcli agent-guide`. It works offline. Keep the
same explicit workspace for every command (`SOCIALCLI_ROOT` or `--root`). Inspect
`socialcli delivery-options --brand MyBrand --json` before promising a delivery mode.
That report describes configuration, not verified provider authorization.

For an existing user-supplied post and original media:

    socialcli publish my-post --brand MyBrand --only youtube --dry-run

Show the complete preview, including account, text, media, privacy and follow-up
comments. Wait for the user's explicit approval of that exact preview. Then:

    socialcli publish my-post --brand MyBrand --only youtube --yes

`--yes` only bypasses a terminal prompt after approval. Inspect the persisted
result and publication journal before repeating any command. Upload accepted,
scheduled, public and awaiting creator confirmation are different outcomes.
Use `comments publication-status PUBLICATION_ID --brand MyBrand` to inspect a
recorded follow-up; never upload the video again to retry a comment.

TikTok defaults to `mode: inbox` in the brand's accounts.yml: the file goes directly
to TikTok and the creator finishes from its inbox notification. Report **awaiting
creator confirmation**, not published or scheduled. `mode: direct` requires an
actually audited app, the required creator UX and permission checks. Never set
`auditada: true` to bypass a failed check; configuration is not audit evidence.

Instagram's current publish adapter requires a creator-owned HTTPS media source
(`media_url_base`). SocialCli does not supply that hosting. Video/image preparation
and validation never authorize generating, cropping or transcoding the originals.

## Scheduling without SocialCli media hosting

The SaaS server stores account/billing/connection information and small operation
metadata. Do not transfer customer videos or large documents to it. Upload original
media directly from the customer's environment to the destination platform. If a
platform needs a public URL, it must be explicitly approved, customer-owned storage.

YouTube and Facebook can hold future publications on the platform. SocialCli's
native API route is a gated candidate, not a completed public pilot. Before using it,
the operator must record real account/project eligibility and calendar pilot evidence;
agents must never fabricate that evidence or select their own account.

    socialcli native-schedule prepare native.json --brand MyBrand --dry-run

Show the complete native preview and its final time; after the creator approves the
exact digest, persist and approve that same intent:

    socialcli native-schedule prepare native.json --brand MyBrand --persist --approval-digest APPROVED_DIGEST
    socialcli native-schedule approve JOB_ID --brand MyBrand --approval-digest APPROVED_DIGEST
    socialcli native-schedule submit-api JOB_ID --brand MyBrand --eligibility operator-pilot.json
    socialcli native-schedule status --brand MyBrand

For new customer schedules, use `schedule: {"mode":"exact","timezone":"Europe/Madrid"}`
with the customer's actual IANA timezone and a matching local offset in `publish_at`.
Each publication can have its own date, time and timezone, including multiple posts
per day. SocialCli preserves the exact instant or rejects a conflict; it never moves
an exact request to 09:00 or another day. Explicit offsets resolve repeated DST hours;
nonexistent local times are rejected. Complete input examples are included below.
Manifests without `schedule` retain the legacy 09:00 America/New_York daily policy.
Existing legacy reservations still protect their whole New York date; they must not
be silently rewritten or bypassed. Facebook API delivery currently covers
Reels only, with a conservative window greater than ten minutes and at most 28 days.

`pending_verification` means the API response was checked but calendar confirmation
is still pending. `uncertain` means reconcile the saved remote attempt, never start
another upload. Only verified platform scheduling removes the need for a running
agent at publication time. First comments remain separate work after public visibility.
If API eligibility is unavailable, prepare a separately approved official UI handoff;
never change an approved route or publish immediately as a fallback.

Instagram and TikTok's posting APIs do not provide this native future-publication
flow. Offer publication now (or TikTok creator review) in the SaaS launch scope.
The compatibility `schedule` queue for Facebook/Instagram is local: it needs the
original file, credentials, connectivity and a persistent executor at delivery time.

    socialcli schedule-doctor --brand MyBrand --json

Exit 1 requires attention. A recent heartbeat proves only a recent run, not that
Muse/Grok will keep an executor alive between conversations. On a persistent,
customer-owned Linux machine, `schedule-install --brand MyBrand` installs systemd
user service/timer units for that exact workspace and installed interpreter. On
macOS it loads a launchd agent. Both fail instead of reporting success from files alone.

Linux units run `run-due` without a shell, five seconds after timer activation and
60 seconds after each completed run. They cannot keep a disappearing agent runtime
alive. The user manager must be available. The installer does not change host policy:
if logout persistence is unverified, an authorized operator can enable linger with
`loginctl enable-linger USER_ID` and then run `schedule-doctor` again.

On Linux, doctor exits 0 only when the matching loaded configuration, enabled active
timer, service result, logout persistence and recent executor heartbeat are verified
now. Even `ready` does not guarantee future power, credentials, files or connectivity.
macOS persistent supervision still requires operator verification beyond heartbeat.
Use `schedule-uninstall --brand MyBrand --root WORKSPACE` to disable future Linux
runs; an already running approved operation may finish. Queue, media and credentials
remain intact. Different workspace roots have different Linux unit identities.
This is not a reason to move media onto the SaaS server.

## Native scheduling input examples (offline)

These are fictional schemas, not authorization or pilot evidence. Store each file
inside the selected brand directory under `SOCIALCLI_ROOT`. Replace every sample
identity, date, copy and hash with verified values. Keep the user's original
`clip.mp4` there; never generate a replacement. The example destination must match
`facebook.page_id` in that brand's accounts.yml. All timestamps require an offset.

First read the complete official destination calendar for the publication period.
Save `native-calendar.json` with the actual observation time (at most 24 hours old),
coverage spanning full local days, and every existing scheduled item. An empty
`objects` array is valid only after verifying that the covered calendar is empty;
never substitute an API error or partial read for a complete inventory. Each object
has `platform`, `account_id`, `remote_ref`, `publish_at` and optional `occurrence_id`.

Example `native-calendar.json`:

```json
{
  "observed_at": "2035-01-01T12:00:00Z",
  "coverage_start": "2035-01-01T00:00:00-05:00",
  "coverage_end": "2035-01-08T00:00:00-05:00",
  "accounts": [{"platform": "facebook", "account_id": "page-example", "complete": true}],
  "objects": []
}
```

Example `native.json` (standalone Reel, no source-video follow-up). `dispatch_after`
is the earliest upload time, not publication time. It must precede `publish_at`;
submission also obeys existing account cooldowns. The example uses the customer's
chosen 19:30 in Europe/Madrid. Confirm that timezone with the customer; never infer
it from the server, the agent's machine or a brand name. Inspect the local time,
UTC time and **final** preview before approving. Platform eligibility and publication
windows still apply; choosing a time does not prove the platform accepted it.

```json
{
  "id": "example-reel",
  "platform": "facebook",
  "account_id": "page-example",
  "publish_at": "2035-01-03T19:30:00+01:00",
  "dispatch_after": "2035-01-01T12:00:00Z",
  "route": "api",
  "schedule": {"mode": "exact", "timezone": "Europe/Madrid"},
  "payload": {
    "calendar": "Meta Business Suite",
    "visibility": "public",
    "copy": "Creator-approved caption",
    "content_origin": "standalone",
    "source_video_id": null,
    "options": {"format": "reel", "share_to_facebook_story": false},
    "first_comment": null,
    "media": [{"path": "clip.mp4", "origin": "user-provided"}]
  }
}
```

For a derivative of a YouTube long video, set `content_origin: "youtube_long"`,
`source_video_id` to the exact 11-character source ID, and the approved first comment
to `🎥 Video completo en https://www.youtube.com/watch?v=SOURCE_ID`. That comment is
separate work after public visibility, never proof of completion at scheduling time.

The operator must supply a real, nonempty local evidence file for the named account.
Example `operator-pilot.json` below is deliberately disabled. Never change flags
based on these examples; only the operator's verified pilot can enable them.

```json
{
  "version": 1,
  "platform": "facebook",
  "account_id": "page-example",
  "api_enabled": false,
  "page_pilot_verified": false,
  "evidence_ref": "operator-pilot-evidence.txt"
}
```

For YouTube, use `platform: "youtube"`, the verified `youtube.channel_id`, calendar
`YouTube Studio`, and options such as `{"format":"video","title":"Approved title",
"selfDeclaredMadeForKids":false}` (the creator must decide the kids declaration).
The eventual visibility is `public`; the adapter uploads privately with the approved
future time. YouTube eligibility replaces `page_pilot_verified` with
`project_verified` and `studio_pilot_verified`, both false until operator verification.
New upload is the default; scheduling an existing video additionally requires its
exact `video_id` and truthful `never_published: true`. Apply the same preview and
approval commands above. Do not invent provider approval or assume upload acceptance
proves future publication.

After API submission, record fresh official-calendar evidence locally (for example
`calendar-evidence.png`, without tokens or signed links). Verify the exact account,
remote ID, copy, processing state and final time. Use the SHA-256 of that actual file.
`content_hash` comes from `intent.content_hash` in the saved preview; `approval_digest`
is the exact approved digest. `remote_ref` comes from the recorded remote attempt.
Example `calendar-observation.json` below uses placeholder hashes; it is not evidence:

```json
{
  "job_id": "example-reel",
  "platform": "facebook",
  "account_id": "page-example",
  "content_hash": "REPLACE_WITH_PREVIEW_CONTENT_HASH",
  "approval_digest": "REPLACE_WITH_APPROVED_DIGEST",
  "remote_ref": "REPLACE_WITH_CONFIRMED_REMOTE_ID",
  "publish_at": "2035-01-03T19:30:00+01:00",
  "observed_at": "2035-01-01T12:05:00Z",
  "source": "ui",
  "calendar_visible": true,
  "state": "native_scheduled",
  "public_visible": false,
  "processing_complete": false,
  "evidence_path": "calendar-evidence.png",
  "evidence_sha256": "REPLACE_WITH_ACTUAL_FILE_SHA256"
}
```

Set the booleans/state to what was actually observed; never copy success from an
example. The observation must be newer than the job and any reconciliation barrier.
Keep the original media unchanged while reconciling. Then run:

    socialcli native-schedule reconcile JOB_ID --observation calendar-observation.json --brand MyBrand
    socialcli native-schedule status --brand MyBrand

Before rescheduling an exact-time job, refresh `native-calendar.json` with complete
coverage of the new local day. Preview, approval and the submit marker each require
fresh account-bound evidence; an old calendar cannot authorize moving into an
unobserved day. To change a verified schedule, show a new complete action preview with the local
ISO timestamp (including its correct offset for the stored timezone):

    socialcli native-schedule reschedule JOB_ID --publish-at 2035-01-04T20:15:00+01:00 --brand MyBrand --dry-run

Only after approval, use the same command with `--approval-digest ACTION_DIGEST`
instead of `--dry-run`, then follow the approved official-UI handoff and reconciliation.
Both times remain reserved until fresh evidence confirms the change. Cancellation
also requires its own preview and approval; it does not authorize deleting the video.

Reconciliation reads local evidence and changes only the ledger; it does not publish.
Check the resulting `state` and notices: command completion alone does not establish
`native_scheduled`. A missing or conflicting observation stays unresolved. Preserve
the existing remote target; never create a second upload to repair it.

## Read metrics and prepare engagement

    socialcli stats --brand MyBrand --only youtube --timeout 120 --json
    socialcli inbox --brand MyBrand --since yesterday --timezone America/New_York --timeout 120 --json

Set the external runner timeout longer than the command timeout. Keep stdout as one
JSON document and progress stderr separate. Exit 1 can contain verified partial
observations. Inspect coverage and errors; a partial empty inbox never means no
comments. TikTok comments are unsupported. Missing metrics are unknown, not zero.
`--since` on stats filters publication dates, not the metrics reporting window.
For an offline brand report from already saved snapshots:

    socialcli report --brand MyBrand --days 7 --top 5 --json
    socialcli report --brand MyBrand --days 7 --as-of 2026-10-13 --only youtube

This does not refresh metrics or access accounts. It shows per-platform account
changes, an available previous daily window, top observed lifetime views and top
observed view changes. The actual observation timestamps and elapsed intervals
are included; daily files do not imply reads occurred at exactly the same hour.
A missing exact baseline is not replaced by another date. Missing values/items
never count as zero. New content absent from the baseline has no measured change.
Rankings cover observed items only, not a guaranteed complete account inventory.
Counter corrections and duplicate IDs remain explicit.

Account comparisons require saved matching account identity and dated observations.
Observations whose timestamp date differs from the daily file are not silently
assigned a timezone: the comparison stays unavailable. Existing snapshots do not
record the collector timezone, so legitimate midnight-boundary reads may need
explicit timezone provenance in a future format. Known values remain available.
Legacy snapshots lacking identity still provide known current values and lifetime
rankings, with partial status. Do not add identity retroactively from accounts.yml.
TikTok comparisons stay unavailable if its current response does not supply an
open_id; configured identity is not inferred as authenticated evidence. Failed,
stale and partial sources remain explicit, and exit1 may contain useful results.
The existing audience data has no audience-online-hours series: best posting time
is unavailable. Do not invent peak hours or causal recommendations from top posts.

For one supervised workflow across saved YouTube/Facebook/Instagram comments:

    socialcli inbox-review --brand MyBrand --json
    socialcli inbox-draft --file inbox-replies.yml --brand MyBrand --dry-run --timeout 120 --json

`inbox-review` is offline: it joins saved comments to the actual reply journals.
`observed`, `draft`, `blocked`, `uncertain` and `replied` describe local evidence.
`replied` means a previously verified reply; it does not recheck present visibility.
Former-account observations and proposals are excluded with explicit counts;
a foreign-brand journal still blocks review. An empty saved view never proves there
are no comments. Refresh with `inbox` first,
inspect its coverage, and preserve the selected account/comment identities.

The explicit reply file is:

```yaml
version: 1
replies:
  - platform: youtube
    account_id: EXACT_CHANNEL_ID
    comment_id: EXACT_TOP_LEVEL_COMMENT_ID
    text: "Exact proposed reply"
  - platform: facebook
    account_id: EXACT_PAGE_ID
    comment_id: EXACT_TOP_LEVEL_COMMENT_ID
    text: "Exact proposed reply"
```

Replace IDs with observed values; never infer an account. Instagram uses the same
schema with `platform: instagram`. Select 1–100 comments, each once. TikTok and
nested-comment targets are unsupported in this unified drafting flow; select the
actual top-level target explicitly instead of silently redirecting a response.

Drafting checks every local selection before provider reads, rechecks author/text
against the provider, and creates existing immutable child proposals plus one
community batch. It does not send replies. The complete batch preview is on stderr;
JSON stdout includes `batch.id`, `batch.fingerprint` and `prepared` child references.
If a later preparation fails, `status: partial`, `batch: null` and `prepared` retain
finished local drafts. Review them; no batch was approved or applied. Do not claim
that the unprepared comments were handled. An existing uncertain or verified reply
blocks a fresh unified draft. Resolve it before considering another response.

Show the full preview and obtain explicit approval of that exact fingerprint:

    socialcli changes apply-batch BATCH_ID --approval APPROVED_DIGEST --brand MyBrand --json
    socialcli changes status-batch BATCH_ID --brand MyBrand --json
    socialcli changes reconcile-batch BATCH_ID --brand MyBrand --json
    socialcli inbox-review --brand MyBrand --json

Reconciliation only reads existing attempts and never starts pending replies.
An interrupted apply reports the batch ID; inspect its status and child journals.
Meta inbox-bound replies retain their existing frequency limit and durable inbox
status. Partial batches are not cross-provider transactions and have no rollback.

Existing individual `comments draft/apply-draft` and YouTube community proposal
commands remain available. To group already reviewed child proposals, use
`changes prepare-batch --file children.yml --brand MyBrand --dry-run --json` with
`version: 1` and `children: [{provider: meta-comments, change_id: EXACT_UUID}]`.
The `children` reference file is different from the explicit `replies` file above.
Never automatically reply merely because an inbox read found a new comment.

## Recovery and error actions

`account login --json` and `account status --json` return structured failure
documents with `error.code`, `error.action`, `error.retryable` and a fixed diagnostic.
`retry_later` applies to a safe read; `recover` resumes installation delivery;
`reconcile` checks an uncertain operation; `sign_in` concerns the installation;
`reconnect` requires explicit user authorization for that social account. A 409 or
network failure alone is never proof of revoked provider access. Unknown failures
require inspection, not automatic retries.

With explicit `--json`, `stats` and unified `inbox` preserve verified observations
and expose each diagnostic in `errors[]` with `code`, `action`, `retryable`,
`requires_user_action`, sanitized `message` and fixed `guidance`. Legacy comments
and YouTube community partial dictionaries retain items, cursors and flags and add
`error_detail`; do not assume they have the same envelope as `stats`. A missing
workspace also produces a structured error in these explicit JSON read modes.
`adjust_read` means reduce scope or adjust the budget, not repeat unchanged.
`unsupported` cannot be repaired by reconnecting. Generic authentication failure
requests account review; it does not prove revoked access. A token-refresh POST
with uncertain delivery must not become a safe read retry. Some legacy adapters
wrap typed causes, so their conservative fallback is inspection or account review.

Meta `comments apply/reconcile/status/apply-draft/reconcile-draft` and YouTube
`content youtube-community apply/reconcile/status` support explicit `--json`.
Keep stdout and stderr separate: the complete preview and exact fingerprint prompt
are on stderr; stdout is one result/error document. JSON does not approve anything.
Show the complete preview to the owner before supplying their approved fingerprint.
There is no `--yes` shortcut for these operations. EOF or a mismatched fingerprint
stops before the mutation service.

Successful results keep their existing fields. Nonverified durable results add
`error_detail`; inspect their status, IDs and journal even if a legacy reconcile
command exits 0. Failures before returning a durable result use `error`, plus
`change_id` when known. A failure after entering the apply service conservatively
requires reconciliation, including a failed journal save or interruption; it never
means the write was rejected. Use the corresponding reconcile/status command,
not a fresh proposal or another apply to retry a possibly completed write.

`publish --json` and `retry --json` also reserve stdout for one versioned document.
Full preview, progress and confirmation remain on stderr; a dry-run additionally
returns the exact `preview` string. Preserve both streams and show the owner the
complete preview. `--json` never grants approval; existing `--yes` still requires
prior explicit consent to that complete preview.

Publication reports include `status`, `resultados`, `errors` and `persistence`;
retry additionally reports `retry.selected` and `retry.blocked`. A report can be
`preview`, `cancelled`, `noop`, `ok`, `partial` or `error`. Each result retains its
original platform state, confirmed IDs, durable attempt IDs and visibility fields.
`error_detail` describes a failed/uncertain/pending media result;
`first_comment_error_detail` concerns only the comment, never a reason to upload
media again. Pending TikTok confirmation remains pending even with exit 0.
An unfinished first comment may likewise produce `partial` with the legacy exit 0.
Always inspect the document instead of inferring completion from the exit alone.

`persistence.complete` is true only after both resultado.json and history were
saved; false after an attempted publication whose persistence is incomplete, and
null before publication. Keep all returned observations after an interruption or
persistence failure. Confirmed results are not permission to replay an uncertain
platform; use the durable journal and provider observations for reconciliation.

Every `native-schedule` command accepts `--json`. It produces one versioned
`kind: native-schedule` envelope on stdout; preserve stderr for the complete local
preview. `data` contains approval documents or operation observations, `jobs`
contains the latest readable job snapshot, and `jobs_complete` says whether that
snapshot completed. An error can retain earlier jobs and confirmed IDs: preserve
them and check `errors` before acting. Checkpoint IDs are observations of an
existing upload, never proof of a public or scheduled post.

`status: ok` means the command completed, not that its jobs are public. Inspect
job/result `state` and `next_action`: prepared needs approval, waiting needs time,
UI handoff needs an authorized operator, and uncertain/verifying needs readback.
Native scheduling evidence does not establish publication. The original date,
timezone and approval document remain unchanged; JSON grants no new approval.

SSH executors must support the same JSON envelope. The client validates command,
brand, exit and safe output and never replays an invalid/lost remote response.
For an older executor, inspect authoritative state and upgrade or use legacy
read-only status; do not repeat a mutation to obtain a prettier response.
Parser errors (such as missing required CLI flags) remain standard CLI usage errors.

Other management commands are not all converted to this taxonomy; inspect
their exit code, stderr and durable result as well. No action hint grants approval
to reconnect, publish, delete or replay a write.

Revoking an installation is supported. Fine-grained, server-enforced permissions
such as “publish but never delete” are not implemented: the current client receives
provider tokens. Do not advertise local agent instructions as an access-control boundary.
