# SocialCli for AI agents — 0.3.0

Requires a persistent shell, Python 3.11+, macOS/Linux and protected storage.
Use https://pypi.org/pypi/socialcli/0.3.0/json. Require HTTP 200, name
`socialcli`, version `0.3.0`; select only `socialcli-0.3.0-py3-none-any.whl`.
Download its HTTPS URL and verify `digests.sha256` before installing that local
wheel with `[headless-credentials]` in an isolated uv tool/venv. Dependencies come
from normal PyPI. If unavailable, stop; never substitute a package or migrate an
existing installation silently. Verify `socialcli --version`.

Read `socialcli agent-guide setup` offline before login. Ask for the brand; never
infer it. Setup order: `brand new NAME`, `credentials generate-key`, `credentials
configure` with encrypted-file storage and an external protected key, then:

    socialcli account login --brand NAME --service https://pilot.socialcli.app --no-browser
    socialcli connect youtube --brand NAME --management --analytics --no-browser

Give links directly to the user. Do not open, fetch or preview them. Keep the
command running. Users enter codes, accept privacy and review assistant data access in their browser only. Never accept for them. Read the setup guide’s Google data-use conditions and daily cleanup instructions.

Before work read the relevant local guide:
`socialcli agent-guide publish|schedule|inbox|errors` (choose one topic).
Use only supplied media. Show the FULL `--dry-run` preview and obtain explicit
approval before writes. Never retry an uncertain write. Preserve credentials and
workspace across sessions. JSON stdout is separate from progress stderr; exit 3
means partial reads, not an empty inbox. Trial: 14 days, then USD 10/month.
Stripe is in test mode. Google is In production with verified branding, but sensitive YouTube scopes remain unverified with a warning and user cap. Meta development and TikTok sandbox restrictions remain. No provider-wide approval is implied.
