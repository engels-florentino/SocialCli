# Instagram Story deletion snapshot follow-up

Implemented in canonical main; no network/provider calls, credentials, deployment or live cleanup.

Only media-delete snapshots use a safe classification read containing id, owner, media_type, media_product_type, timestamp and permalink. Exact ID and owner are mandatory. Explicit supported product/type pairs are mandatory; missing classification is rejected. Stories stop there, avoiding comment controls and unrelated edit fields. Feed/Reel deletions subsequently use the existing full content read and reject classification drift between reads. This preserves full stale-baseline comparisons at the cost of one additional read per feed/Reel deletion snapshot. General content reads, edits and moderation remain unchanged; absent comment state is never defaulted.

Fictional regressions cover local Story preparation and approved deletion, hosted User-token inspect followed by approved mutation, wrong/missing owner and ID, missing/unsupported classification, feed/Reel caption drift after preview and immediate permission failure without retry. Existing hosted exact approvals, grants and uncertainty/no-replay tests pass unchanged. Fixture classifications now explicitly describe FEED media instead of relying on omitted fields.

RED: `uv run pytest -q tests/test_meta_management.py tests/test_saas_meta_user.py -k 'story_delete or hosted_story'` before implementation: **6 failed, 3 passed, 127 deselected**. Both positive Story regressions reproduced code-10 rejection; four invalid classification cases reproduced missing validation. Full pre-implementation affected files also showed **6 failed, 130 passed**. An initial edit attempt used unavailable `python`; no files changed by that attempt and it was rerun with python3.

GREEN: `uv run pytest -q tests/test_meta_management.py tests/test_saas_meta_user.py`: **139 passed**, one existing Starlette/httpx deprecation warning. No broad suite run; controller owns final review and broader verification.

Remaining limitation: deployment and real cleanup remain controller-owned. Deletion acceptance remains accepted_unverifiable when absence cannot be independently proven, retaining existing semantics.
