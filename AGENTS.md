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
