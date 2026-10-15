# SocialCli

Public Python CLI for creator-owned social accounts. Run `uv run pytest` and
`uv run socialcli --help`. Internal Python imports remain `socialctl`.

## One product, one canonical workspace

SocialCli SaaS is the current version of this product, not a separate project.
Work from this repository root on `main`; the current source version is 0.3.0rc7.
The former `.worktrees/saas` and `.worktrees/shared-connect` checkouts have been
consolidated here. Do not maintain a second permanent application checkout.
Historical versions and tags remain migration/release evidence, not active copies.
Package publication and server deployment are separate from updating `main`.

- All product-facing UI, CLI messages, errors, emails and product documentation
  must be in English. The operator conversation may be in Spanish.

- Never infer a user's brand or connect somebody else's accounts.
- Never publish without a complete `--dry-run` preview and explicit approval.
- Never generate, crop or reencode media. Users supply produced files.
- Never commit `.env`, `.secrets/`, credentials or private creator workspaces.
- Keep user data outside the installed package. Use `--root` or `SOCIALCLI_ROOT`.
- Do not claim platform approval based on sandbox success or this public repo.
- New tests must use fictional accounts and avoid live publication.

## Metrics and inbox operating rules

- Verify the installed version with the active workspace interpreter. An updated
  checkout does not update another workspace's virtual environment.
- Use supported `--json` output and SocialCli's own `--timeout`. Keep stdout as
  one JSON document and progress on stderr; do not merge streams or use `tail`
  to capture the result. Any runner timeout must exceed SocialCli's limit with
  enough grace for local persistence and final output.
- Aggregate stats/inbox/report use exit 3 for partial observations in rc2.
  Older clients and other reads may use exit 1 for useful verified observations. Read `status`, `coverage`
  and `errors`; retain those observations and never interpret a partial empty
  inbox as proof that no new comments exist. Inspect Meta `skipped_comments`
  counts and respect unknown creation times and incomplete pagination.
- Full YouTube stats requests retention and traffic per selected video, plus
  audience data. Advancing request progress does not prove a hang. `--since`
  filters publication dates, not the metrics period. Check installed `--help`
  before using options; do not assume a proposed `--basic` flag exists.
- Distinguish deadline expiry from connection/proxy/read failures. Bounded reads
  honor environment proxies, `NO_PROXY` and custom CA settings. Preserve that
  configuration and report only sanitized diagnostics; never reveal secrets.
- Handle routine read budgets and partial-result bookkeeping yourself. Give the
  user concise findings and meaningful coverage limits. Keep technical warnings
  in the operation record unless they block the task or require user input.
  Read failures do not authorize reconnecting accounts or repeating writes.

See [the AI agent guide](docs/ai-agents.md) for read examples and limitations.

## Account connection handoff

AI agents must use `socialcli connect PLATFORM --brand MyBrand --no-browser` and give the printed link directly to the user. Never open, fetch, validate, preview or follow that link with a browser, HTTP client or link-unfurl tool: the user must open it and press Connect on the landing page to start a single-use authorization bound to their browser. Keep the CLI process running while the user signs in and confirms the account. The link expires after ten minutes. If it was already visited, expired or cancelled, restart `connect --no-browser` to generate a fresh link; do not reuse it. Account confirmation still requires the user’s explicit approval.


Legacy 0.2.6 connection flow only: for an agent without interactive stdin, obtain the user’s explicit authorization for the exact account ID and brand first, then use `socialcli connect PLATFORM --brand MyBrand --no-browser --account-id ACCOUNT_ID --yes`. `--yes` confirms this account connection only; it does not authorize publication. Never guess an ID or use its position in a returned list. Replacing independent credentials additionally requires `--replace-independent`. Without these explicit arguments, noninteractive execution stops before creating an authorization link.

For the SaaS candidate, read the installed `socialcli agent-guide` before connecting.
Account selection and approval occur in the user-owned browser pairing flow; do not
apply the legacy account-ID flags to that flow or infer supported flags from another version.
