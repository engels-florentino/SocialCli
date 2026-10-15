# SocialCli for AI agents — Candidate 0.3.0rc4 pilot

Requires a persistent shell, Python 3.11+, macOS/Linux and protected storage.
Use https://test.pypi.org/pypi/socialcli/0.3.0rc4/json. Require HTTP 200, name
`socialcli`, version `0.3.0rc4`; select only `socialcli-0.3.0rc4-py3-none-any.whl`.
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
command running. Users enter codes and approve accounts in their browser only.

Before work read the relevant local guide:
`socialcli agent-guide publish|schedule|inbox|errors` (choose one topic).
Use only supplied media. Show the FULL `--dry-run` preview and obtain explicit
approval before writes. Never retry an uncertain write. Preserve credentials and
workspace across sessions. JSON stdout is separate from progress stderr; exit 3
means partial reads, not an empty inbox. Trial: 14 days, then USD 10/month.
This pilot uses Stripe test mode and provider test accounts; no approval implied.
