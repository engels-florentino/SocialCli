# SocialCli mascot landing — 15 October 2026

Static customer landing redesign. The application remains 0.3.0rc8; this does not
publish a new CLI distribution or change provider permissions or billing mode.

## Design and behavior

- Original user-provided robot recolored pearl white with ice-blue reflections,
  typing at a laptop. Google Flow preserves the supplied character reference.
- Portrait handset uses a fixed 278 × 600 layout and scales uniformly; it does
  not become wider when desktop height decreases.
- Interactive English conversation follows the supplied Muse-style references:
  per-platform observations, data limitations, a proposed reply, and explicit
  approval. All account numbers and comments are fictional and labeled sample
  data. No requests to social APIs and no actual publications occur in the demo.
- Installation prompt remains `Install SocialCli: https://socialcli.app/agents`.
  Muse, Grok bots, Dots, Claude, ChatGPT and Gemini are named beside it, with the
  requirement for terminal access. This is not a claim that every chat mode or
  every named agent was integration-tested during this visual update.
- Price stays $10 USD/month, 14 free days, no card required. Existing early-access
  restrictions and TikTok draft delivery remain visible.
- Pause control and reduced-motion preference stop automatic movement. Reduced
  motion initially uses the poster without downloading the video. The phone
  shows a question, a brief typing indicator and a concise answer, then begins
  a scheduling example after about eight seconds. The complete fictional
  conversation then continues through a reply preview and simulated user
  approval before looping back to the start.
  Without JavaScript, the poster, example metrics and selectable install text
  remain available. Mobile places installation before the larger phone demo.

## Media provenance

[Google Flow project](https://flow.google.com/project/d26d3f0d-fa48-4b2c-abfa-a4974af96274)
uses the original robot reference IMG_2298.WEBP supplied by the operator.
The base image was made with Nano Banana Pro, 16:9. Animation uses Omni 1.1 Flash,
720p, 8 seconds, with matching start/end reference frames and a locked camera.
The final scene integrates YouTube, Facebook, Instagram and TikTok icons directly
in Flow, replacing the first revision’s separate HTML badges. The matching
first/end frame includes all four logos. The files are original Flow downloads,
not locally reencoded. Video is muted.

## Verification and deployment

- `node --check docs/assets/landing-demo.js` passed.
- `python3 scripts/build_ui_assets.py --check` passed.
- `uv run pytest tests/test_ui_build.py -q`: 2 passed.
- `git diff --check` passed.
- Safari local review: 1366 × 680 viewport has 1366 × 680 document bounds,
  including the footer. At 390 × 844 there is no horizontal overflow and the
  install control ends at y=690; the phone demo follows below.
- Local copy button, demo navigation and explicit simulated approval checked.
- Initial static rollout: nine explicitly selected files, assets first and index
  last, atomic per-file replacement. Previous affected files retained privately
  at `landing-mascot-20261015T185139Z` on the server.
- All nine initial public files returned HTTP200 and matched their local SHA256.
  MP4 range requests returned HTTP206 with the expected video/mp4 content type.
  Public /agents still advertises rc8; pilot /healthz returns HTTP200.

The local temporary responsive-check page was removed and was not deployed.

## Integrated network scene

The final [Flow animation](https://flow.google.com/project/d26d3f0d-fa48-4b2c-abfa-a4974af96274/edit/1118cc05-6aed-4d14-8f6e-76f70c04efb7)
contains the four network icons in the generated scene. The image was edited with
Nano Banana Pro; the 8-second animation used Omni 1.1 Flash, 720p, x1 (12 credits
shown). Original download: H.264 1280 × 720, 1,439,691 bytes. The poster is the
matching Flow frame. Local Safari playback and frames near the beginning/end
were inspected; all four logos remained recognizable. The final webpage has no
separate floating logo overlays.

## Final hero and background blend

The mascot is a large backdrop in the left hero, with the copy above the face,
laptop and network icons. The video element follows its actual 16:9 picture
bounds, so the horizontal and vertical alpha fades reach the picture edges
instead of fading an invisible object-fit letterbox. Normal compositing avoids
the darker rectangle previously introduced by multiply blending. Safari visual
review confirmed the soft edge transition; the operator accepted this revision.

Final static rollout: six allowlisted files, assets first and index last, with
atomic per-file replacement. Backup: `landing-hero-20261015T190631Z` in the
server's private deployment directory. All six public downloads returned HTTP200
and exactly matched local SHA256 hashes. The new video range request returned
HTTP206 and video/mp4. `/agents` still serves rc8 and `/healthz` returned HTTP200.
The JS syntax check, UI asset consistency check, two UI build tests and diff
whitespace check passed again after the final CSS change. No runtime restart,
CLI release, provider configuration or billing change was needed.

## Conversation pacing refinement

The phone now shows each question first, a three-dot typing bubble after 650 ms,
and the assistant's answer after 1.25–1.5 seconds of typing. The account summary
has six seconds of reading time before the scheduling exchange begins. Short
message entrance transitions replace the static 18-second wait. Pause and tab
visibility preserve the remaining delay; reduced motion shows complete messages
without the animated sequence. Manual navigation remains available. The later looping refinement also shows
a fictional user approving the reply automatically, as part of the simulation.

Safari verified the question-only start, summary response, automatic progression
into the reply draft, deliberate simulated approval and replay control. JS syntax,
UI asset consistency and both UI build tests passed. Three static files were
published with backup `landing-chat-20261015T191107Z`; all three public downloads
matched the local SHA256 hashes. Application version remains rc8.

## Scheduling example and continuous loop

The demonstration now cycles through account insights, scheduling an already
approved YouTube Short for tomorrow at 8:00 AM, a comment reply draft, and a
simulated user approval with confirmation. Every exchange starts with the user
message and a typing indicator. After the last response, the conversation returns
to insights automatically. A full cycle is approximately 29 seconds. Manual
navigation or the demo approval button pauses autoplay; Play restarts the loop.
Reduced motion keeps a static, manually selectable example.

The scheduling confirmation states the example timezone (America/New_York),
explains that YouTube holds the video privately until its scheduled publication,
and explicitly says no real video was scheduled. The entire phone is labeled
as a simulated conversation with sample data and no real actions. No platform
requests, uploads, scheduling operations or comments are performed by the demo.


The loop refinement passed the JS syntax, UI consistency and two UI build checks.
Safari displayed the scheduling card, advanced automatically through simulated
approval, and continued into the next conversation cycle. The three deployed
files matched their public SHA256 hashes; private backup:
`landing-loop-20261015T191641Z`. Agent names beside the installation command were
subsequently emphasized in blue bold text without implying support for chat
modes that lack terminal access.

The emphasis update was deployed with backup
`landing-emphasis-20261015T191729Z`; public HTML, CSS and demo JavaScript matched
local SHA256 hashes after deployment. The application runtime was unchanged.

## Free trial emphasis

The offer now presents “14 days free.” in larger bold text with “No card
required.” below it, in a pale-blue badge beside the installation area. Mobile
uses a compact badge next to the monthly price. Public Safari review confirmed
the visual treatment. HTML and CSS SHA256 hashes match the local files and the
published downloads. Private server backup: `landing-trial-20261015T225352Z`.
No pricing, trial eligibility or billing behavior changed.
