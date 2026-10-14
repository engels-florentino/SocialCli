# Linux scheduler implementation plan

> Use superpowers:executing-plans inline, test-driven development and one final review.

**Goal:** Install and diagnose a customer-owned persistent Linux queue executor.
**Architecture:** Dedicated systemd user service/timer module, invoked by existing
schedule-install/uninstall/doctor commands on Linux. Preserve the macOS launchd path.
**Tech stack:** Python stdlib, Typer, systemd user units; no dependencies or server media.
**Spec:** This bounded follow-up implements critique 3 in the agent-readiness tracker.

## Contract

- Explicit brand/root; unit identity includes both, so workspaces cannot overwrite
  one another. Use installed interpreter, module invocation and absolute root.
- systemd user oneshot plus a timer 60 seconds after completion, no parallel copies.
  Original media stays on customer executor. No native publication/pilot bypass.
- Correct escaping for spaces, quotes, backslashes, percent and dollar; no shell.
- Verify user manager availability before writing. Fail on reload/enable/active
  verification errors, never report success from files alone. Leave actionable diagnostics.
- Doctor combines actual loaded timer/service configuration, enabled/active state,
  linger and recent heartbeat. Healthy means observed now, never future guarantee.
- Do not enable linger or alter global host policy automatically. Explain its need
  for surviving logout; no app can keep an ephemeral AI runtime alive.
- Uninstall disables future timer runs before removing owned files, preserves the
  queue/media/credentials, and allows an already running approved operation to finish.
- Retain macOS compatibility and remote-workspace guard. No real service installed
  on the user's Mac/server during development.

## Steps

- [x] Add failing tests for Linux installation, inactive manager/failed enable, correct
  workspace identity, missing linger, doctor failure/healthy evidence, and uninstall.
- [x] Implement `socialctl/scheduler_systemd.py` and Linux CLI branches; run focused tests.
- [x] Verify a rendered unit with the systemd parser where available; run installed
  original run-due command against an empty fictional workspace, without network.
- [x] Update canonical guide/generated docs and critique tracker; keep UI wording consistent.
- [x] Run full suite, artifact/installed checks, one review and fixes; commit locally.

## Review focus

No silent workspace replacement, no command/specifier interpolation, no success on
unavailable user managers, no readiness from heartbeat alone, no stopped in-flight
publication or removed user data during uninstall. Platform pilots remain separate.

## Verified result

Linux user timers, combined doctor evidence and safe uninstall implemented.
Final review found and corrected a cached-timer uninstall gap: absence of unit
files no longer bypasses manager checks; active timers stop before disable while
in-flight services may finish. Four regression tests observed RED then GREEN.
Full suite: 2,512 passed, one upstream Starlette deprecation warning. Wheel/sdist
checks and installed macOS smoke passed. Rendered units passed the real systemd
259 parser on Linux without starting or installing a service.

Remaining minor review coverage: injected reload, drop-in and fragment mismatch
failures have not each been exercised; disable failure is now covered. Real Linux
logout/reboot lifecycle and platform delivery remain pilot acceptance work.
No deployment, social publication, GitHub push or package release occurred.
