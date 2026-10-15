# Agent approval and preflight notes

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

## Combined read-only preflight

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
