# Deterministic brand insights

Implements critique 5 under the approved autonomous SaaS follow-up. Customers and
agents need growth and top-content decisions from their existing metrics without
another LLM service, extra provider traffic or media storage.

Use the saved daily snapshots and the existing ReadReport envelope. A new offline
`report --brand NAME --days 7 --as-of YYYY-MM-DD --top 5 --json` reads only the
explicit workspace. Defaults: today, seven days, five items; days1..365, top1..50.
An optional --only selects one platform. Human output and JSON describe the same
observations. No snapshot changes, credentials reads, writes or network requests.

Use latest snapshot on/before as-of as the endpoint, explicitly mark an older date
stale. Baselines must be on endpoint minus days and endpoint minus2*days: never
silently compare uneven daily windows or interpolate missing snapshots. Report the
actual observed timestamps and elapsed seconds as well as requested dates. Previous
versus current daily-window growth is descriptive; never label it normalized when
actual read times differ. No cross-platform totals of people or views.

Account deltas require matching nonempty account IDs at both endpoints and valid
aware observation times in increasing order and dates matching their daily files.
Without collector timezone provenance, different dates remain unverified; never
assume a blanket timezone tolerance makes a reused read fresh. Add optional account_id provenance to
LecturaRed, captured from the authenticated YouTube channel and requested Meta
account node. TikTok identity stays unknown unless returned by its current response;
do not guess from current configuration or retroactively stamp old snapshots.
Legacy snapshots remain readable but unknown identity prevents account comparison.

Return known account values and top lifetime views from latest successful reads.
Return top observed view changes only for identical piece IDs present at both
endpoints with known nonnegative counters and matching account provenance. Missing
items and None are never zero; counter decreases remain flagged corrections, not
engagement or negative growth recommendations. Rank per platform deterministically
by metric descending then ID; keep latest content titles/URLs as data, terminal
output must not interpret embedded controls or rich markup. Duplicate piece IDs
exclude ambiguous comparisons and mark partial coverage.

Snapshot brand/date must match path and request. Corrupt, mismatched and unavailable
sources produce fixed diagnostics without raw exception/provider error payloads.
Partial source warnings and failed enrichments remain explicit. Unknown metrics do
not become numbers. Return partial (exit1) when observations exist but coverage or
comparison is incomplete; error (exit1) if no usable observations; ok(exit0) when
requested current comparisons are covered. Previous-period availability is explicit
but optional, not a reason to discard current comparisons.

Current audience snapshots contain geography/age, not audience online hours. Return
best_posting_time unavailable with an explicit reason; do not infer causality or an
optimal hour from top content. Explain this in the packaged guide. Preserve shared
UI conventions; this increment adds CLI/report output, not a new web design.

Validation: synthetic snapshots, controlled HTTP fixtures for provenance, offline
CLI, missing/corrupt/stale/error/partial data, account replacement, zero and unknown
counters, timestamps, counter corrections, duplicate IDs, deterministic ordering,
JSON parseability and legacy compatibility. One final independent review, full
suite and installed artifact smoke. No live accounts, rollout or provider pilot.
