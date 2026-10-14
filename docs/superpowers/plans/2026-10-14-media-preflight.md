# Local media preflight and accurate metadata

Approved critique8: report actionable media diagnostics before upload, preserve
original files and approval gates, no automatic processing or SaaS media hosting.
Use executing-plans inline with TDD and one final independent review.

## Implementation contract

1. Read actual visual stream instead of blindly using first stream; ignore attached
   cover art for videos. Reject audio-only, empty/nonregular and unreadable inputs.
   Bound ffprobe time and disable network protocols. Keep rotation handling.
   Probe metadata remains internal/nonserialized so approvals do not change merely
   because more diagnostics were collected.
2. Add provider-specific checks for inspected Instagram images/Reels and TikTok
   video formats/codecs/frame rate/dimensions. Preserve local policy limits with
   honest provenance; image and Reel aspect ratios are different. Unknown essential
   probe data is a diagnostic, never a false media-valid claim. Images unsupported
   by YouTube/TikTok video routes are rejected. Other platform codecs and decoder
   integrity remain explicitly unchecked rather than claiming universal acceptance.
3. `media check SLUG --brand --only PLATFORM --json`: local post preflight reports
   every selected platform and asset, reuses probes for shared files, collects
   partial failures instead of aborting at the first file. Reports fixed actions,
   metadata/read coverage and provider eligibility checks still required. No HTTP,
   credential access, copying, hashing of full videos, reencoding or writes.
4. Normal publication validation uses inspected technical metadata from loader;
   preflight itself never approves or publishes. Native manifests/Stories/groups
   remain distinct formats and their own approval/validation paths; report scope
   explicitly describes ordinary post.yml routes.

## Evidence checked through Context7

- Instagram official IG User Media: Reel MOV/MP4,HEVC/H264,AAC,23–60fps,
  3–900s,300MB; image JPEG,8MB,4:5–1.91:1. Recommend9:16 is not a hard Reel ratio.
- TikTok official media transfer guide:MP4/WebM/MOV,H264/H265/VP8/VP9,
  23–60fps,360–4096 dimensions,4GB. Creator-specific duration is checked online,
  not inferred by offline preflight.
- FFmpeg official stream specifiers distinguish visual streams/attached pictures.

## Tests and review focus

RED→GREEN audio-first/cover art/audio-only/nonfinite metadata; separate image and
video limits; shared-file probes, one broken file plus usable destinations; no
HTTP or data modification; malformed/missing ffprobe bounded errors; installed
JSON output. Run full suite/build/installed smoke and one review before commit.
Review: invalid media cannot claim pass; read-only path never transfers or rewrites
files; technical observations don't invalidate legacy approval hashes silently;
unknown runtime eligibility stays unknown; partial failures preserve known checks.

## Review decisions and verification

Independent review found two Important defects. Eight failing regressions reproduced
ambiguous container aliases incorrectly passing and invalid media paths/body hiding
later asset diagnostics. Both fixed in one pass.100 focused tests pass, including
Story round-trip and migration workflows. Shared migration fixture now supplies
realistic visual-stream metadata; ephemeral probe observations do not alter model
identity or durable approval serialization.

Ruling: ordinary post.yml routes only; Stories/native/groups retain separate
validators. Cost: their operators still need those validators and provider pilots.
Ruling: require known MOV/MP4 major-brand metadata and matching extension; ambiguous
Matroska/WebM and unknown major brands block rather than falsely pass. Cost: some
valid files, including WebM supported by TikTok, need a separately validated route
until richer identification is implemented. No conversion is performed.

Deferred minor: this command does not validate top-level campaign; the ordinary
publication loader still does. Passing local checks never approves publication.

Installed wheel:37 media preflight tests pass with all product imports asserted to
come from the installed package. Installed smoke passes version/help/workspace,
encrypted credentials and packaged instructions, with zero external calls.

Final full suite:2687 passed in54.63s; one pre-existing Starlette deprecation warning. Build/artifact validation and installed smoke pass. No real provider calls, customer media processing, public release or candidate deployment performed.
