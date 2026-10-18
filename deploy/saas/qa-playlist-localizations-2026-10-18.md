# Localized playlist verification

An approved private playlist creation persisted its original Spanish metadata
and English translation. YouTube also returned the original-language text in
`localizations`, causing exact dictionary comparison to leave the operation
uncertain despite the matching approved values.

The client now accepts only an additional default-language entry that exactly
mirrors the approved title and description. All other fields and translations
still require exact equality. Explicit original-language entries are not
ignored. Approved values, fingerprints, ownership checks and raw journal
observations remain unchanged.

Read-only reconciliation verified the existing creation without another POST.
The disposable playlist was subsequently deleted: HTTP 204 receipt followed
by exact-ID absence. A pre-write cleanup conflict and delayed deletion readback
were retained in the private operation record; no ambiguous write was replayed.

Validation: the two positive regressions failed before the fix; 16 new boundary
cases and all 150 owned-resource tests passed afterward. Full suite: 3,246
passed, with the existing Starlette/httpx deprecation warning. Independent review
found no critical or important issues. A dedicated synthetic persisted recovery
case remains a minor test-coverage follow-up; live recovery was verified.

This establishes localized private creation only. Updates, playlist images,
item reordering and podcast variants retain their separate QA status. The fix
is currently in source; published rc15 and Muse still require the next client
release. No server or schema change is required for this client comparison.
