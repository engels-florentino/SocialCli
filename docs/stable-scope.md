# Stable release scope

Owner instruction, October 18: release a stable version whose offered functions
pass their required tests; remove operations without a supported API or a viable
implementation. A blocked or disabled operation is not a passed function.

## Excluded automatic operations

The following are outside the stable API product. Existing disabled catalogue
records document the boundary; they are not executable features or a roadmap
promise. Do not replace them with guessed endpoints or browser automation.

| Platform | Excluded operation | Boundary |
| --- | --- | --- |
| YouTube | Pin comments, give creator hearts, create community polls, add cards/end screens, select a Short's related video | No supported public Data API write contract in this product. |
| YouTube | `comments.markAsSpam` | Removed provider operation; never substitute another moderation action. |
| Instagram | Edit the text of an existing comment | No verified supported text-edit endpoint; delete/recreate is a different action. |
| Instagram | Arbitrary profile/bio editing | No supported public profile-edit route in this product. |
| Instagram | Arbitrary editing of a published caption | No supported public caption-edit route in this product. Changing comment settings is a distinct action. |
| Instagram/TikTok | Automatically like or heart comments | No supported endpoint implemented; replies are not substitutes. |
| TikTok | Comment inbox, comment posting or deletion | Outside the supported connector; draft upload is a separate operation. |

Manual Studio/Business Suite instructions and local handoff files do not turn
these operations into API features. Do not count an operator's manual action as
an automatic API test pass. Preserve historical journals and useful local
evidence when removing a public claim.

## Real APIs still require evidence

An implemented API method, requested scope or sandbox connection does not establish
general availability. Native scheduling, hosted Meta webhooks, Instagram moderation
and every media format require their exact account/permission/public-route tests.
Keep unverified candidate routes explicitly gated; retain their outstanding QA
cases until verified or explicitly removed from the offered product scope.

TikTok Direct Post is disabled and excluded from the current offer. The supported
posting mode is creator-reviewed draft upload with `video.upload`. Direct Post is
a separate implementation and provider-review project, not an implicit condition
for the draft-upload connector.

## Release evidence

- Reuse passed live variants; rerun automated regressions after code changes.
- Require a clean complete automated suite, package allowlist and clean-install
  checks on Linux/macOS for the exact immutable release artifact.
- Match help, schema, catalogue, public guides and actual supported routes.
- Record publication/readback/cleanup separately; an accepted receipt alone does
  not prove the requested change or its restoration.
- Preserve unresolved provider uncertainty; never replay an uncertain mutation
  to manufacture a pass.
- Stable package publication and commercial hosted launch have separate gates.
  Provider approval, live billing and email delivery cannot be inferred from
  local tests or the stable version number.

The private QA ledger holds exact successful exclusions and remaining variants.
This scope document does not certify that the stable release is ready.
