# Metadata keyword readback remediation

Diagnosis: V2 compared full editable parts using list equality, so an exact keyword permutation incorrectly failed verification. Unexpected defaultAudioLanguage remains a real mismatch requiring review; this work does not classify it as benign.

Design: only V2 postwrite/read-only reconciliation compares valid string tag lists as exact multisets, preserving duplicate counts, case and whitespace. Neither approved payloads, durable before/after, fingerprints nor outbound PUTs are reordered. Existing timestamp and empty-value equivalence remains. All other fields/lists remain strict. V1's comparator also gates prewrite conflict detection, so it remains untouched; same_metadata_snapshot is unchanged.

Diagnostics: at most 16 mismatching editable field paths, stored as a journal string without values/raw responses. Audio introduction records snippet.defaultAudioLanguage. Uncertain retry reconciles read-only into manual_review; another apply does not issue a PUT.

RED: first targeted run reported the intended two failures (tag permutation uncertain, absent mismatch_paths), with 41 passing. GREEN: `uv run pytest tests/test_youtube_batches.py tests/test_youtube_metadata_parts.py -q` reported 80 passed. `git diff --check` passed. Fictional tests include exact duplicate cardinality, added/removed tags, case/whitespace, invalid/unhashable types, strict other fields/list order, unchanged preflight, one PUT, unchanged approval/payload, audio drift and fresh proposal preserving observed audio.

Limits: no full suite, provider/network operations, brand/secret reads, or historical journal modifications. The historical live uncertain record must not be changed or replayed by this worker. Parent owns live restoration/cleanup, full-suite verification, and real comparisons. The implementation commit is the scoped commit containing this report.
