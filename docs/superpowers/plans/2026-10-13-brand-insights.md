# Brand insights implementation plan

> Use superpowers:executing-plans inline with TDD and one final independent review.

**Goal:** Turn saved snapshots into trustworthy per-brand growth and top content.
**Architecture:** A pure offline metrics report module consumes daily snapshots;
small CLI registration emits the existing ReadReport envelope or plain text.
Optional provenance captured by readers makes account comparisons safe.
**Tech stack:** Python stdlib, existing Pydantic models and Typer; no new dependency.
**Spec:** ../specs/2026-10-13-brand-insights.md

## Global constraints

No provider writes, media processing/storage, new LLM dependency or implicit brand.
Unknown is never zero; no fabricated best posting time or cross-network totals.
Preserve legacy snapshots, record provenance prospectively, sanitize diagnostics.

## Task 1: Offline report and integration

Files: socialctl/metricas/insights.py (report and renderer), metricas/modelos.py
(optional account_id), youtube.py/facebook.py/instagram.py/tiktok.py (returned
provenance), cli.py (registration), tests/test_brand_insights.py, existing reader
fixture tests, packaged guide and generated docs.

Consumes Brand and Snapshot. Produces build_report(brand, days=7, as_of=None,
top=5, only=None)->ReadReport; render_report(report)->str. account_id remains
optional and defaults None. Report never invokes readers or credential stores.

- [x] Write failing tests for known growth, actual interval, missing/zero counters,
  account replacement, legacy provenance, partial/stale/corrupt/mismatched files,
  duplicated IDs, corrected counters and deterministic rankings.
- [x] Run tests: fail on missing module, then implement report and optional model
  field; run tests to green.
- [x] Add failing provenance assertions in existing provider fixture tests; capture
  only actual response/target identity without additional API calls; run to green.
- [x] Add CLI JSON/human offline tests, implement options and fixed error output.
- [x] Update canonical guide/generated web copy and critique tracker.
- [x] Run full suite, build/artifact and installed offline report checks; request
  one independent review, reproduce/fix Important findings, commit locally.

## Review focus

- Same brand reconnecting a different account must not yield a fake growth delta.
- Reused daily reads and missing baselines must not impersonate fresh comparisons.
- Missing/negative metrics and duplicate IDs must not yield invented rankings.
- Unreadable files and embedded provider errors must not leak diagnostics/secrets.
- Partial reads and stale observations must remain usable with explicit limits.

## Verification and review result

Full suite: **2,540 passed**, one upstream Starlette warning,50.20s. Artifact
checks, installed macOS smoke and offline report in an external fictional
workspace passed; no provider calls, deployment or release.

Independent review found3 Important issues, all reproduced RED and fixed GREEN:
reused previous-day observations, cumulative account view corrections and unknown
content-counter coverage. Matching identity, exact daily baselines and unknown
values are preserved. Dates crossing the collector's undocumented timezone remain
unverified; legitimate midnight-boundary comparisons can be unavailable, with
known observations retained. TikTok identity remains unknown when absent from the
existing response; it is never inferred from a current configuration.

Deferred minor: an OK read with every metric null and no pieces returns partial
instead of error; it cannot return complete coverage or invented counts. Live
identity/provider guarantees, platform approval, deployment and per-agent
permissions remain separate acceptance work.
