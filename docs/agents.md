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
