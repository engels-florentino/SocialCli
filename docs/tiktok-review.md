# TikTok review preparation

SocialCli is a public project that other creators can install to manage their accounts. A new review application must accurately reflect this purpose. The production draft was updated on October 6, 2026 with SocialCli branding and an accurate inbox-only explanation. This does not establish production approval.

## Architecture of this distribution

The shared connection service is implemented at `https://social.florentino.pro`. It owns OAuth callbacks and encrypted refresh credentials; creators connect through the browser and confirm the discovered account in the CLI. The CLI holds a connection capability in its OS keyring and retrieves access tokens only into process memory. Creator media and publication execution remain local. Independent-app authentication is retained as a separate option.

The TikTok connector uses Web Login Kit with `https://social.florentino.pro/oauth/tiktok/callback`. Matching sandbox credentials and the HTTPS callback are configured. On October 6, 2026, the authorized Histopast target completed real Web OAuth, OS keyring installation, exact open_id verification, real token renewal and service disconnection. This live pilot published no content. A current inbox-upload recording and a second independent creator test remain required; production approval is not established.

## Current production draft

- Products: Login Kit and Content Posting API. Data Portability removed.
- Scopes: `user.info.basic` and `video.upload`; Direct Post disabled.
- Web redirect: `https://social.florentino.pro/oauth/tiktok/callback`.
- Legacy desktop redirect: `http://localhost:8723/callback`.
- Public CLI authorization requests exactly these two scopes. Existing tokens may retain previously granted permissions.
- Review has not been submitted. The historical `histopast.mov` attachment still needs replacement with verified current evidence.

## Status to declare

- The code is public and supports brands configured by each operator.
- Inbox is the default mode; users complete posts in TikTok.
- Production approval and a Direct Post audit are not accredited.
- A historical recording is attached to the previous application; it has not been verified as demonstrating the updated public product.
- The public website documents the product and its limits; TikTok makes the final assessment.

## Before requesting Direct Post

The existing adapter queries creator info and checks duration and audit status, but fixes privacy to public. It does not yet offer all required UX controls: explicit privacy selection without a default, comments/Duet/Stitch controls consistent with creator info, visible creator identification, commercial disclosure and corresponding declarations. The current terminal preview alone does not establish compliance with the required interface.

Do not enable `auditada: true` without actual approval or submit a demo that hides these limitations. Complete and verify the necessary experience before requesting this scope. For inbox, also review general requirements, permissions and architecture; changing the endpoint does not waive general review.

## Prepare the application

1. Register SocialCli's name and identity consistently in the portal and website.
2. Describe the public product and the implemented authorization model accurately, without exclusive-use claims or announcing nonexistent services.
3. Request only necessary products/scopes. Remove Data Portability if no implemented use case exists.
4. Complete the description and explanation; historical fields were truncated.
5. Supply an up-to-date, user-created recording of the complete sandbox flow showing every requested product and permission.
6. Review policies and live URLs, and submit only when the product and evidence are ready.

Official sources: [App Review Guidelines](https://developers.tiktok.com/docs/en/app-review-guidelines) and [Content Sharing Guidelines](https://developers.tiktok.com/docs/en/content-sharing-guidelines), consulted on October 6, 2026. These pages require an application intended for external users, a complete website and evidence of the flow. Direct Post adds controls and requires keeping the secret confidential.

## Sandbox demonstration checklist

Record the actual English CLI and TikTok interfaces, without showing secrets:

1. Open the public product website and the installed `socialcli --help`.
2. Show the shared service and its actual configuration. Creators use browser authorization; the service operator owns the provider app. Do not present a disabled connector or mocked flow as an approved public integration.
3. Use the sandbox application and an authorized test account. Show OAuth consent for the two requested scopes and the HTTPS callback completing successfully.
4. Show the selected brand/account and an existing creator-owned local video.
5. Run the complete dry-run preview, then obtain explicit approval for this exact upload. Do not resend an earlier pilot solely to make a recording.
6. Execute the approved inbox upload, show its pending-confirmation result, and verify arrival in the creator's TikTok inbox. Show the creator-facing completion flow.
7. Keep the recording within the portal limits (MP4/MOV, at most 50 MB per file) and verify that all selected products/scopes appear.

The shared service needs a current real sandbox demonstration before submission. Creators authorize directly on TikTok; SocialCli never collects their TikTok passwords.

The sandbox retains its internal label `Histopast`, while its public name, logo, description, website and policies now identify SocialCli. Direct Post is off and the sandbox configuration includes only `user.info.basic` and `video.upload`. The live token returned additional permissions from an existing authorization; those were recorded as provider-granted scopes, not requested anew. Revoking that historical grant could affect an existing independent integration and was not part of this pilot.
