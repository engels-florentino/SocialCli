# TikTok review preparation

SocialCli is a public project that other creators can install to manage their accounts. A new review application must accurately reflect this purpose. The historical application described a private tool for one operator; publishing this repository does not update that application or demonstrate compliance with every control.

## Architecture of this distribution

Each operator uses their own provider application and stores secrets locally. No other creator's client secret is included. This distributes the software without sharing credentials, but does not provide a shared SocialCli OAuth backend. Such a product requires an authorization service that keeps the secret outside the CLI, with updated architecture, policies and demonstration before it can be presented as available.

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
