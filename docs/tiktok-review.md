# TikTok review preparation

SocialCli is a public project that other creators can install to manage their accounts. A new review application must accurately reflect this purpose. The production draft was updated on October 6, 2026 with SocialCli branding and an accurate inbox-only explanation. This does not establish production approval.

## Architecture of this distribution

Each operator uses their own provider application and stores secrets locally. No other creator's client secret is included. This distributes the software without sharing credentials, but does not provide a shared SocialCli OAuth backend. Such a product requires an authorization service that keeps the secret outside the CLI, with updated architecture, policies and demonstration before it can be presented as available.

## Current production draft

- Products: Login Kit and Content Posting API. Data Portability removed.
- Scopes: `user.info.basic` and `video.upload`; Direct Post disabled.
- Desktop redirect: `http://localhost:8723/callback`.
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
2. Explain that this distribution currently requires each operator to register their own provider application. Do not present a shared authorization service as implemented.
3. Use the sandbox application and an authorized test account. Show OAuth consent for the two requested scopes and the local callback completing successfully.
4. Show the selected brand/account and an existing creator-owned local video.
5. Run the complete dry-run preview, then obtain explicit approval for this exact upload. Do not resend an earlier pilot solely to make a recording.
6. Execute the approved inbox upload, show its pending-confirmation result, and verify arrival in the creator's TikTok inbox. Show the creator-facing completion flow.
7. Keep the recording within the portal limits (MP4/MOV, at most 50 MB per file) and verify that all selected products/scopes appear.

A future shared SocialCli application requires a secure authorization service and an updated demonstration. Creators would then authorize on TikTok directly; SocialCli must never collect their TikTok passwords.
