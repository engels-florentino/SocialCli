# SocialCli for AI agents — Candidate 0.3.0rc16

Candidate setup instructions. Require published metadata before installing;
do not replace an existing 0.2.6 installation or migrate its credentials silently.
Stripe remains in test mode. Google is External / In production with verified branding, but sensitive YouTube scopes remain unverified, with an unverified-app warning and user cap.
Real-agent acceptance is performed by the owner; do not claim it from installation.

SocialCli gives terminal-enabled agents tools for creator-owned social accounts.
You need a persistent shell workspace, Python 3.11+, macOS or Linux, and protected
credential storage. SocialCli does not include a model or MCP server. Provider
availability depends on account eligibility; Google sensitive-scope verification is still pending.

## Install the reviewed candidate

Use the official TestPyPI release metadata:
https://test.pypi.org/pypi/socialcli/0.3.0rc16/json

1. Require HTTP 200 and package name `socialcli`, version `0.3.0rc16`. If this
   release is unavailable, stop and report it; do not install a similarly named package.
2. Select exactly `socialcli-0.3.0rc16-py3-none-any.whl` from `urls`. Download its
   HTTPS `url` and verify the file against that entry's `digests.sha256`.
3. Install that verified local wheel into an isolated environment. Resolve
   dependencies from normal PyPI, not TestPyPI. For example:

       uv tool install --python 3.12 '/absolute/path/socialcli-0.3.0rc16-py3-none-any.whl[headless-credentials]'
       socialcli --version
       socialcli agent-guide

   Without uv, use a new Python virtual environment and its `python -m pip install`
   with the same local wheel and `headless-credentials` extra. Do not use system pip.
   If an existing SocialCli tool is installed, use a separate virtual environment
   for this pilot and its absolute executable; do not upgrade it automatically.

Verify the installed version is exactly 0.3.0rc16 before continuing. The stable
`uv tool install socialcli` command is not the installation path for this pilot.

For headless agents, configure encrypted-file storage with an external0600 key in
a0700 directory before login. Keep the key outside the workspace and preserve both
across conversations. Never fall back to plaintext storage. Follow the protected
storage procedure at:
https://socialcli.app/connections.html#headless-encrypted-capability-storage.

If uv is missing, use the virtual-environment route or its official installation
instructions at https://docs.astral.sh/uv/getting-started/installation/.
If Python is older than 3.11, select a supported interpreter first. If the installed
tool is not on PATH, use its absolute executable or `uv tool update-shell` and
start a new shell. Video validation needs ffprobe (from FFmpeg); missing ffprobe
must be resolved before a video submission. Never generate or reencode media.

## Authorize the installation, then the social account

The owner must accept the current privacy policy in the browser and separately acknowledge assistant data access. Check the actual assistant plan/settings: Google data must not be used for advertising or general-purpose model training or unrelated human access/transfer. If these controls cannot be established, do not connect that configuration. An agent compatibility label is not provider-wide approval. Existing installations without the new receipt must be revoked and linked again by the owner; never erase local credentials as a shortcut.

Use `https://pilot.socialcli.app` for this pilot. The main site is the human landing.
Ask for the brand name; never infer it. Replace MyBrand below with that exact name.
Keep the workspace outside the application repository:

    socialcli brand new MyBrand
    mkdir -m 700 -p "$HOME/.local/share/socialcli-credentials"
    socialcli credentials generate-key --key-file "$HOME/.local/share/socialcli-credentials/master.key"
    socialcli credentials configure --brand MyBrand --backend encrypted-file --store-file "$HOME/.local/share/socialcli-credentials/store.json" --key-file "$HOME/.local/share/socialcli-credentials/master.key" --yes
    socialcli account login --brand MyBrand --service https://pilot.socialcli.app --no-browser --label "My AI assistant"

Give the printed URL directly to the user. Do not open, fetch, preview or follow
it yourself. Keep the process alive with a runner timeout longer than 30 minutes.
The user signs in by email and explicitly approves this installation. Do not ask
for their OTP, password, session cookie or provider token in chat.

    socialcli connect youtube --brand MyBrand --management --analytics --no-browser

Use the same browser handoff for TikTok:

    socialcli connect tiktok --brand MyBrand --no-browser

Facebook and Instagram also use `connect PLATFORM --no-browser`; add the
documented management/analytics options only when needed and owner-authorized.
Never ask SaaS users for a developer `client_key`, `client_secret` or localhost
OAuth setup. After account login, `auth PLATFORM` also routes to the SaaS
connection flow. Without a signed-in installation it fails with login guidance.
Legacy developer-owned app prompts require explicit `auth --independent` in an
interactive terminal. Do not use that mode for SaaS users.

The provider step has its own 10-minute window. The user grants access and chooses
the exact account in their browser; the SaaS flow does not require guessing an
account ID or interactive terminal confirmation. Connecting does not authorize
publication. Do not use --yes as blanket authorization.

The trial lasts 14 days from the first successful connection, without a card.
Then hosted access costs USD 10 per month. You can subscribe during the trial without losing free days; the first monthly charge is due at its original end.
Only the user's browser completes payment. The current candidate uses Stripe
test mode; it is not collecting live subscriptions.

## Persist and recover

Managed YouTube API observations expire after six days. User-supplied originals and independent Studio imports are preserved. Re-read the platform when needed; do not report absent old snapshots as zero or invent weekly growth. Unknown ages and failed cleanup block affected data. Disconnect, terminal authorization failure and account deletion trigger a managed-data purge when observed by this runner. Exported reports, chats and independent backups require separate deletion.

Concurrent commands can briefly contend for a managed-store lock. Cleanup waits
up to two seconds per lock. `local_data_busy` means wait for the other command to
finish and retry; it does not mean corruption and never calls for deleting files.
Retention remains enforced while cleanup is blocked.

Preview the daily cleanup service for this exact brand and root, show it to the owner, then install only with their authorization:

```sh
socialcli data-cleanup-install --brand NAME --root /absolute/workspace --dry-run
socialcli data-cleanup-install --brand NAME --root /absolute/workspace --yes
socialcli data-cleanup-status --brand NAME --root /absolute/workspace
socialcli data-cleanup --brand NAME --root /absolute/workspace
# Only for an authorized deletion request:
socialcli data-cleanup --brand NAME --root /absolute/workspace --purge-youtube
```

The daily macOS LaunchAgent needs the user session; the Linux user timer needs its user manager (check Linger in status). No cleanup can execute on a stopped runner. Status must confirm the supervisor; a preview is not installation. Do not install on another brand or infer the workspace. Keep the original interpreter/venv available.

Keep the brand folder, its .secrets metadata and the configured credential backend
between conversations. Never upload credentials or keys to Git. A changed path
can change OS-keyring lookup; use the same absolute SOCIALCLI_ROOT or --root.
Ephemeral agents must arrange protected persistent storage before authorizing.
A fresh conversation alone does not require reconnecting YouTube.

    socialcli account status --brand MyBrand --json
    socialcli connections --brand MyBrand --json

`connections` retains successful platforms and identifies each failed platform in
`errors` with a fixed code, next action and HTTP status when available. Exit 3 is
partial, exit 1 means the read failed, and exit 0 is complete. Do not discard a
healthy YouTube result because another platform returns `connection_unavailable`.
After the owner revokes and relinks an assistant, explicit `account attach` can
restore an existing approved connection even when its old capability was deleted.
The current installation must still have owner-approved access, and an existing
local account must match exactly. This does not repeat OAuth or approve publication.

Use `account attach PLATFORM --brand MyBrand` to attach an existing owner-approved
connection after signing in on a second installation. Use `account recover
PLATFORM --brand MyBrand` after interrupted local installation; do not blindly
repeat OAuth. `account rotate --brand MyBrand` renews this installation's local
credentials. A network failure is not proof the social account was revoked.
Check --help on the installed version before using a proposed flag.

Preserve stdout JSON separately from stderr progress. Give read operations their
own --timeout and a longer runner limit. For stats, inbox and report, exit 3 means partial observations; exit 1 means error. Older clients and other read commands may return partial data with exit 1;
never treat an incomplete empty inbox as proof there are no comments.

## Publication gate

For every YouTube upload, show the Community Guidelines certification in the complete preview. The owner’s approval must include that statement; account connection is not upload consent. Old upload approvals without the certification require a fresh preview and approval. Never accept privacy or assistant terms on the owner’s behalf.

Use only media supplied by the user. Show the complete --dry-run preview and wait
for explicit approval of that exact account and content before any publication.
Keep confirmed, pending and uncertain results distinct. Never repeat an uncertain
write. Sign-in, subscription and connection approval do not approve publication.

## Temporary Instagram media

For SaaS Instagram connections, `publish` accepts local JPEG, MP4 and MOV files
without `media_url_base`. Approved original bytes are uploaded to the SocialCli
server on florentino.pro (up to 512 MiB), then fetched by Instagram through an
opaque HTTPS URL. The URL expires 15 minutes after reservation, including upload
time. The CLI requests deletion when the operation ends; automatic cleanup also
handles crashes and interrupted uploads. Media is excluded from backups.
This is temporary staging, not a file library. Keep the original on your runner.
A scheduled local job uploads only at dispatch; its runner must remain available.
Native Instagram video delivery already supports direct upload to Meta. Native
image/carousel helpers with explicit URLs retain their existing hosting contract.

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

SaaS Instagram publishing uses temporary hosting automatically when no
`media_url_base` is configured. Independent credentials still need their own
HTTPS media host. Video/image preparation
and validation never authorize generating, cropping or transcoding the originals.

## Scheduling and temporary media

YouTube remote scheduling receipts need a verified platform reconciliation at least every six days. Without it, receipt expiry blocks local management while preserving the occupied slot; a video already scheduled on YouTube can still publish there. Use YouTube Studio to inspect or cancel it if the local receipt expired. Do not dispatch a replacement or assume the remote schedule was canceled. Supplied future plans without provider receipts are preserved.

The SaaS stores account and connection metadata. Instagram may temporarily stage
original media for at most 15 minutes after reservation; it is excluded from backups.
Do not use the server for documents, archival media or long-lived scheduling files.
Other uploads go from the customer’s runner directly to the destination platform.

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

## Check supplied media locally

    socialcli media check POST_SLUG --brand MyBrand --json
    socialcli media check POST_SLUG --brand MyBrand --only instagram --json

This checks ordinary `post.yml` routes without credentials, network requests,
uploads, copying or conversion. Shared files are inspected once. Read every
platform's `assets`, `issues` and `coverage`; a missing or unreadable file does
not discard other platform results. `passed` means the implemented local checks
passed, not that the provider will accept or publish the file. `partial` retains
passed platforms while others are blocked. Neither result approves publication.

Issue actions distinguish installing `ffprobe`, inspecting a timed-out file,
supplying compatible original media, configuring an account and reviewing post
text. Each ffprobe run is capped at30seconds and can open local files only.
The command does not download URLs or resolve remote hosting. Provider account
eligibility, TikTok creator-specific duration/privacy, public media URL availability
and full-file decoder integrity remain unchecked. Actual provider processing and
visibility still require readback after an explicitly approved operation.

Instagram image and Reel constraints are separate: the generic ordinary route
checks observed codec/container/frame rate plus its applicable dimensions and
size limits. TikTok's route also checks observed video codec/container/frame rate
and dimensions. For Facebook/YouTube the existing configured route policy is not
claimed to be a complete provider codec specification. Inspection metadata stays
outside durable approval payloads; supplied bytes are never modified. Native
manifests, Stories and group assets have their own validators and are outside
this command's scope. Continue to show the full publication dry-run for approval.

## Read metrics and prepare engagement

    socialcli stats --brand MyBrand --only youtube --timeout 120 --json
    socialcli inbox --brand MyBrand --since yesterday --timezone America/New_York --timeout 120 --json

Set the external runner timeout longer than the command timeout. Keep stdout as one
JSON document and progress stderr separate. Aggregate partial results use exit 3
in rc2 (exit 1 in rc1). Inspect coverage and errors; a partial empty inbox never means no
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

`inbox` reads owner replies from the platform and saves account/media/parent-bound
evidence, including after reinstalling or attaching an existing connection.
`inbox-review` is offline: it joins those observations to the actual reply journals.
`reply_state` is `observed`, `draft`, `blocked`, `uncertain` or `replied`.
`replied` can come from a verified local write or an observed platform reply by
the connected account. It does not recheck present visibility. Local uncertain
writes remain uncertain even if other owner replies are found.
`platform_reply_state` distinguishes `owner_reply_found`, `no_owner_reply_found`
and `unknown`. Unknown means incomplete or unverifiable evidence, never proof
that a comment is unanswered. A partial later scan retains positive evidence.
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
schema with `platform: instagram`. Select 1–100 comments, each once. TikTok
comments remain unsupported. For a nested observation, explicitly provide the
verified top-level `reply_parent_id` as described under Resumable batches and
reply context; the preview shows the actual target without inventing deeper nesting.

Drafting checks every local selection before provider reads, rechecks author/text
and owner replies against the provider, and creates immutable child proposals plus one
community batch. It does not send replies. The complete batch preview is on stderr;
JSON stdout includes `batch.id`, `batch.fingerprint` and `prepared` child references.
If a later preparation fails, `status: partial`, `batch: null` and `prepared` retain
finished local drafts. Review them; no batch was approved or applied. Do not claim
that the unprepared comments were handled. An existing uncertain or verified reply
blocks a fresh unified draft. Resolve it before considering another response.
Incomplete owner-reply coverage also blocks preparation. Follow-ups in an already
answered thread require separate, deliberate review through the lower-level
proposal commands; never bypass the guard automatically. Two independent agents
can still race after preparation: this is read-backed prevention, not a provider
transaction or a cross-installation lock.

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

Typed failure codes now survive sanitized authentication, YouTube metadata/resource
and Meta management/comment wrappers. A provider GET timeout or rate limit can
request a bounded read retry; a lost token-refresh or SaaS token POST requires
reconciliation even when the outer command only requested a read. A generic
401/403 does not prove expired authorization. Only a recognized service expiry
code requests reconnection, which still requires the owner. Partial comment and
community pagination, comments sync and the unified inbox retain verified rows
and the specific recovery action.

Other management commands are not all converted to this taxonomy; inspect
their exit code, stderr and durable result as well. No action hint grants approval
to reconnect, publish, delete or replay a write.

Meta management journals preserve sanitized errors, typed guidance, and request
stage/HTTP status when available. `write_intent` does not prove a request reached
Meta or a response arrived. Older journals cannot recover a discarded response.
Report `uncertain` as attempted and unconfirmed, never as applied. Reconcile the
existing ChangeSet with reads only; a fresh UUID cannot bypass an unresolved
operation on the same target.

Before a Meta Page profile update, every patch field must appear in the profile
read. Missing does not mean "currently not set". If Meta omits the field, the
change cannot be previewed or verified and is blocked before writing. Do not
silently map `bio` to `about` or another field; a different field needs its own
explicit preview and approval. A field missing during readback is unavailable
evidence, not proof of failure or success.

Revoking an installation is supported. Fine-grained, server-enforced permissions
such as “publish but never delete” are not implemented: the current client receives
provider tokens. Do not advertise local agent instructions as an access-control boundary.

Container checks require verified MOV/MP4 brand metadata. Ambiguous Matroska/WebM
identification is currently blocked even though TikTok supports WebM; supply an
existing MOV/MP4 or use a separately validated route. No automatic conversion.

## Installation connection access

When the owner links an assistant, the browser lets them select its SocialCli
connections by platform. The service checks this selection before OAuth, attaching
an existing grant, migration, activation and token delivery. `account status --json`
can include `platform_access` from installation status. Existing installations
keep their previously approved access after the schema upgrade.

`platform_not_allowed` and `installation_permissions_invalid` require owner review;
never retry by silently reconnecting, choosing another brand or another installation.
The account page shows the selection. To change it, the owner revokes the old
installation and explicitly links a new one.

This is a connection-broker boundary. It does not narrow provider tokens, which may
carry permissions for linked accounts and additional actions. Per-action restrictions
remain unavailable. Previously delivered provider tokens can remain valid after
SocialCli revocation until they expire or are separately revoked at the provider.


## Read diagnostics in Candidate 0.3.0rc3

- Aggregate `stats`, `inbox` and `report` JSON envelopes: exit 0 = ok,
  exit 3 = partial, exit 1 = error, exit 2 = invalid command arguments.
  Always inspect coverage and retain verified observations, including after errors.
  A partial empty inbox never proves that no comments exist.
- `stats` and `inbox` default to a 300-second total budget. Give the process
  additional time for persistence. Use `--only youtube` (repeat `--only` for more
  platforms) to devote the read budget to selected providers. Increase `--timeout`
  for large catalogs; the budget remains finite. `--since` on stats filters video
  publication dates, not the analytics period. Inbox still examines older videos
  because they can have new comments.
- Progress is summarized at most once every five seconds. YouTube audience is
  requested before per-video retention and traffic enrichment. Unverifiable Meta
  comments remain excluded and counted; more time cannot repair missing fields.
- YouTube inbox's authenticated moderation filter requires management consent.
  `connect --management` and `--analytics` exist in rc1 too. Inspect the installed
  `connect --help`. Only with owner authorization, use the connection example above
  to add missing consent; reads never reconnect automatically. A single 403 can
  also mean disabled comments or resource eligibility, not missing OAuth scopes.
- `local_configuration` indicates a local URL/proxy/CA setup issue, not server
  rejection. `transport_failure` means connectivity failed. Preserve environment
  proxies and credentials; correct configuration rather than bypassing it.
- In `delivery-options`, `native_schedule_authorization_verified` concerns native
  scheduling only. The legacy `authorization_verified` alias has the same meaning,
  identified by `authorization_verified_scope`. Neither verifies general read access.
- `credentials generate-key` writes a new external Fernet key privately, never to
  stdout and never over an existing file. The parent must already be owned by you
  with mode 0700; the file is created with mode 0600. Use a separate protected
  directory per independent brand. The key is URL-safe base64 for 32 random bytes,
  not a password or hexadecimal string.

## Comment API corrections in Candidate 0.3.0rc3

YouTube comments with disabled discussion return `comments_disabled`, not a
reconnection request. Valid reply-list cursor traversal tolerates an omitted
total count while retaining supplied-data consistency checks. Meta may withhold
a comment author; read observations retain `author_id: null`. Do not invent the
author or draft replies from that unverified identity. Facebook verification can
resolve a receipt/list ID difference using exact provider comment permalinks and
owned-target membership; unknown outcomes must still never be resent.


## Explicit approval and preflight

The approval path is: display the complete preview, obtain the creator's explicit
approval, then pass its exact fingerprint to the execution command. Supplying a
flag is the transport for that approval; it does not grant an agent permission.

```sh
socialcli publish clip --brand Example --only facebook --dry-run
# After the creator approves that complete preview:
socialcli publish clip --brand Example --only facebook --approval-fingerprint APPROVED_DIGEST

socialcli schedule clip --at 2030-01-01T12:00:00Z --brand Example --only facebook --dry-run
# After the creator approves the content and exact scheduled time:
socialcli schedule clip --at 2030-01-01T12:00:00Z --brand Example --only facebook --approval-fingerprint APPROVED_DIGEST

socialcli content youtube-owned prepare --file change.yml --brand Example --dry-run
# Keep the returned ChangeSet ID and its fingerprint together.
socialcli content youtube-owned status CHANGE_ID --brand Example
socialcli content youtube-owned apply CHANGE_ID --brand Example --approval-fingerprint APPROVED_FINGERPRINT
```

`--approval` and `--approval-digest` are aliases for `--approval-fingerprint` on
these commands and on the management apply commands. The same options are
available on `changes apply`, `meta apply`, `comments apply`, `comments apply-draft`,
`content youtube-assets apply`, `content youtube-community apply`,
`content youtube-analytics apply`, `story apply` and `group handoff`.
Check each installed command's help; native scheduling has its existing explicit
`--approval-digest` contract.

Management proposals are durable and include a proposal identity and observations.
Preparing again can produce a new ID and fingerprint even for the same input
file. Preserve the exact returned pair, inspect that stored proposal, and approve
that fingerprint. Do not substitute a digest from a different preparation.
Publication digests bind the selected account, content and media bytes; queue
digests additionally bind the scheduled instant. Changed inputs require a fresh
complete preview and approval. Execution rechecks before writing.

Without a terminal or explicit approval, these commands fail promptly without
reading an open input pipe. To deliberately provide approval through stdin:

```sh
printf '%s\n' "$APPROVED_FINGERPRINT" | socialcli content youtube-owned apply CHANGE_ID --brand Example --approval-stdin
```

`--approval-stdin` reads one line and can wait for that line by explicit choice.
It cannot be combined with a fingerprint argument. Do not blindly pipe a newly
generated digest into an apply command: the creator must review and approve the
complete preview first. On an interactive terminal, the existing prompts remain.

For `publish` and `schedule`, the previously authorized `--yes` workflow remains
supported, including displaying the complete preview and checking for changes.
For management commands that historically required an exact fingerprint,
`--yes` still cannot replace it. A supplied wrong fingerprint is never overridden
by `--yes`. Local schedule output is text; `publish --json` keeps its complete
preview and approval UI on stderr and returns one JSON document on stdout.

### Combined read-only preflight

`preflight` is an alias of `doctor`. Both accept explicit supplied files and/or a
post slug, and collect local validation alongside account identity checks:

```sh
socialcli doctor --brand Example --platform youtube --post clip --media /path/to/supplied.jpeg --json
socialcli preflight --brand Example --platform youtube --require-scope https://www.googleapis.com/auth/youtube.force-ssl --json
```

`--media` is repeatable and inspects existing files without modifying them.
`--post` reuses `media check` and preserves each platform's diagnostics when
another supplied file is invalid. Account and credential-store diagnostics remain
in the report alongside media failures. Encrypted-store checks validate the
protected key and existing store without creating lock files or repairing state.
A directory with owner permissions 0700 and no group/other access is accepted,
including mode 2700; credential files still require mode 0600.

Scope checks compare exact scopes recorded as granted in the token response;
they never substitute requested OAuth scopes or the capability matrix. Unknown
scope grants remain unknown and cannot satisfy `--require-scope`, which requires
an explicit `--platform`. Identity verification and recorded scopes do not prove
write permissions or provider approval. No token refresh, reconnection, remote
mutation, publication, media generation or reencoding is performed. TikTok
identity checks remain unsupported by this existing doctor integration and are
reported as unavailable. Without `--media` or `--post`, file integrity is not
checked. A supplied image passing inspection alone does not prove eligibility
for a particular publishing route; use `--post` for route validation.


## Resumable batches and reply context

Community batches remain sequential, resumable operations, not atomic cross-provider transactions. Each child must verify before the next starts. A received comment receipt now permits one additional bounded, read-only reconciliation during apply, covering delayed provider readback without another POST. A lost receipt, incomplete evidence or contradictory identity still stops the batch. Reconciliation never starts pending children.

Batch JSON preserves existing keys and adds all children to `outcomes`, including `not_attempted`, with child index, provider, platform and change ID. `summary` includes total and verified/pending/uncertain/blocked counts and ID lists. A partial batch with only untouched children reports `batch_pending` and `action: apply_remaining`; an uncertain child reports `operation_uncertain` and `action: reconcile`. Continue the same approved batch with its exact fingerprint, retaining all verified outcomes. Progress is on stderr; stdout contains one JSON document.

`inbox-draft` errors include `failed.child_index` (one-based), platform, account/comment IDs, stage and a safe reason. Completed immutable children remain in `prepared` after a later failure. Diagnostics do not reproduce arbitrary provider or storage exception text.

Facebook reply preparation accepts an unavailable external comment author when exact comment membership and media ownership can be verified. The author remains null in the approved baseline. Changes to text, identity or membership block the write. Own-comment edit/delete and created-reply verification still require the authenticated account's exact author ID.

A nested observation can explicitly map a new thread reply to its observed top-level parent:

```yaml
version: 1
replies:
  - platform: youtube
    account_id: fictional-channel
    comment_id: observed-reply-id
    reply_parent_id: observed-top-level-comment-id
    text: Exact supplied reply text
```

The source and mapped parent must both be observed for the same account/media/thread. The parent must be top-level and satisfy the existing eligibility rules. Source membership/text/author are read again during preparation and immediately before the write. The full preview binds `before.reply_context` and the actual write parent. This is supported through the existing YouTube, Facebook and Instagram reply edges; it does not invent a deeper provider nesting relationship or imply a mention. Multiple selections mapping to the same parent are rejected. Existing replied/uncertain parent protections remain in effect. Inbox review links the resulting journal to both the parent and selected source observation.
## Add another social platform without signing out

If `connect` reports `platform_not_allowed`, run
`socialcli account permissions --brand MyBrand --no-browser --json`.
Give its `browser_url` directly to the owner; never open or approve it for them.
The owner adds platforms to this installation in their SocialCli browser account.
The command only reads status and provides the link; it does not grant access.
After confirmation, check `socialcli account status --brand MyBrand --json`
and run `socialcli connect PLATFORM --brand MyBrand --no-browser`.
Existing YouTube and other connections remain active. Do not log out, revoke the
assistant or disconnect existing accounts just to add a network. If the service
does not support expansion yet, keep the connections and report the required
service update. Platform access does not replace provider consent or publication
approval.
