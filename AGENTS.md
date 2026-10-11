# SocialCli

Public Python CLI for creator-owned social accounts. Run `uv run pytest` and
`uv run socialcli --help`. Internal Python imports remain `socialctl`.

- Never infer a user's brand or connect somebody else's accounts.
- Never publish without a complete `--dry-run` preview and explicit approval.
- Never generate, crop or reencode media. Users supply produced files.
- Never commit `.env`, `.secrets/`, credentials or private creator workspaces.
- Keep user data outside the installed package. Use `--root` or `SOCIALCLI_ROOT`.
- Do not claim platform approval based on sandbox success or this public repo.
- New tests must use fictional accounts and avoid live publication.

## Account connection handoff

AI agents must use `socialcli connect PLATFORM --brand MyBrand --no-browser` and give the printed link directly to the user. Never open, fetch, validate, preview or follow that link with a browser, HTTP client or link-unfurl tool: its first visit starts a single-use authorization bound to that browser. Keep the CLI process running while the user signs in and confirms the account. The link expires after ten minutes. If it was already visited, expired or cancelled, restart `connect --no-browser` to generate a fresh link; do not reuse it. Account confirmation still requires the user’s explicit approval.
