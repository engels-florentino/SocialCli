# English observed QA followup report

## Scope and files

Applied only the five product-facing wording changes specified by the brief:

- `socialctl/management/stories_cli.py`: story preview header is `FULL PREVIEW — standalone Story`.
- `socialctl/management/groups_cli.py`: group URL help is `Exact HTTPS URL /groups/GROUP.` and export confirmation begins `Local export written:`.
- `socialctl/cli.py`: schedule confirmation begins `Scheduled:` and schedule status uses `attempts=`.

No command names, option names, values, identifiers, records, exit codes, approval behavior, comments, or internal names changed. Existing tests contained no brittle expectations for these exact presentation strings, so no tests were edited or added. No additional strings were observed during this scoped review; the other Spanish-looking strings found elsewhere by text search were outside this task's supplied QA observations and remain for separate triage.

## Verification evidence

- `uv run pytest tests/test_stories.py tests/test_facebook_groups.py tests/test_scheduler_cli.py` — **100 passed in 4.12s**.
- `git diff --check` — passed.
- Self-review confirmed the diff contains only the five requested string replacements plus this report.

## Commit

Pending scoped commit.

## Concerns

None identified within the requested scope.
