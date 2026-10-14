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

The existing native ledger uses 09:00 America/New_York, one item per account/day.
Do not silently substitute that for another requested hour; general scheduling is
not yet a launch-ready feature. Complete input examples are included below.
Facebook API delivery currently covers
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
Muse/Grok will keep an executor alive between conversations. `schedule-install`
loads a macOS launchd agent and fails if it cannot. Linux operators must provide
their own persistent supervisor for `run-due`; automatic systemd installation is
not yet included. This is not a reason to move media onto the SaaS server.

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
submission also obeys existing account cooldowns. The current 09:00 policy may move
an occupied date; inspect the **final** preview before approving. A customer who
requested another hour needs the general scheduling feature, still pending.

```json
{
  "id": "example-reel",
  "platform": "facebook",
  "account_id": "page-example",
  "publish_at": "2035-01-03T09:00:00-05:00",
  "dispatch_after": "2035-01-01T12:00:00Z",
  "route": "api",
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
  "publish_at": "2035-01-03T09:00:00-05:00",
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
The local metrics summary already includes snapshot differences; do not invent
growth, audience peak hours or causal recommendations from insufficient samples.

For a Facebook or Instagram reply, choose the exact verified IDs and draft locally:

    socialcli comments draft MEDIA_ID COMMENT_ID --platform facebook --text "Approved proposed reply" --brand MyBrand --dry-run

Show the complete draft and fingerprint. `comments apply-draft` asks for that exact
fingerprint after approval; never infer approval from the draft or inbox read.
For headless batch application, prepare exact provider proposals, then:

    socialcli changes prepare-batch --file replies.yml --brand MyBrand --dry-run
    socialcli changes apply-batch BATCH_ID --approval APPROVED_DIGEST --brand MyBrand
    socialcli changes reconcile-batch BATCH_ID --brand MyBrand

The last command only reads prior attempts. The batch references explicit
`meta-comments` or `youtube-community` proposal IDs; preparation and application
remain separate. Use `socialcli content youtube-community --help` for YouTube reply
preparation. Never invent a supported flag or automatically respond to new comments.

## Recovery and error actions

`account login --json` and `account status --json` return structured failure
documents with `error.code`, `error.action`, `error.retryable` and a fixed diagnostic.
`retry_later` applies to a safe read; `recover` resumes installation delivery;
`reconcile` checks an uncertain operation; `sign_in` concerns the installation;
`reconnect` requires explicit user authorization for that social account. A 409 or
network failure alone is never proof of revoked provider access. Unknown failures
require inspection, not automatic retries. Legacy commands are not all converted
to this taxonomy; inspect their exit code, stderr and durable result as well.

Revoking an installation is supported. Fine-grained, server-enforced permissions
such as “publish but never delete” are not implemented: the current client receives
provider tokens. Do not advertise local agent instructions as an access-control boundary.
