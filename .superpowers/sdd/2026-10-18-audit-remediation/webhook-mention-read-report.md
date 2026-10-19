# Task4 scoped mention-read correction

Implemented on canonical main. No remote calls, credentials, deployment,
publication, receiver/enrollment rebuild or write-action expansion.

Changed source: `socialctl/management/meta_webhooks.py`,
`socialctl/management/meta_client.py`, `socialctl/connections/meta_user.py`,
`socialctl/connections/saas_client.py`, `socialctl/saas/meta_user.py`.
Tests: `tests/test_meta_webhooks.py`, `tests/test_saas_meta_user.py`.
Documentation: `docs/meta-webhook-receiver.md` and this report.

Offline reconciliation dispatches comment versus media inspection by hint shape;
malformed explicit comment IDs fail closed. Dedicated strictly typed mention
inspection accepts exact IDs without fabricating a reply proposal. Managed reads
reuse server-retained User grant, current scopes, installation/owner and selected
Page/Instagram authorization, with no token export or refresh. Media mention
observations are not ownership evidence. Existing comment mention validation and
write operations remain unchanged. Unrelated pre-existing dirty files preserved.

Red: both new endpoint tests failed with 404 before implementation.
Final focused command:
`uv run pytest tests/test_saas_meta_user.py tests/test_meta_webhooks.py tests/test_saas_instagram_moderation.py tests/test_meta_management.py tests/test_meta_cli.py -q`
Result: 192 passed; one existing Starlette/httpx deprecation warning.
Fictional coverage includes shape routing, exact account/media/comment mismatch,
malformed inputs, missing/expired User grants, current missing scopes, inaccessible
mentions, retained credentials never exported, actual managed client bridge,
independent User media reads and zero provider writes.

Limits: source-only fictional validation; genuine event capture and live provider
shapes remain operational QA. No public hosted queue CLI added; existing offline
commands retain local event semantics. Commit: see the commit containing this
report (the report is included in that commit).
