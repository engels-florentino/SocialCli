# Meta Social Technologies MCP assessment

Source: https://developers.facebook.com/documentation/mcp/devtools-mcp
Official page updated October 7, 2026; read in authenticated Safari on October 18.
Web text retrieval failed; the official page was read in authenticated Safari.

## Pilot preparation

Execution on October 18 added `meta_social_technologies` to the operator's local
Codex configuration with the official endpoint, an eight-tool read allowlist,
and explicit `developer_tools_mcp_app_read` OAuth scope. The owner completed the
browser authorization and `codex mcp login` reported success. After the owner
reloaded Codex, `devtools_app_list` confirmed SocialCli with Read access. The
initial administrative audit completed through the eight-tool allowlist.

The client's default OAuth discovery included management and business scopes;
that unapproved flow was cancelled before use. The replacement explicitly requests
only the read scope, also persisted for future logins. No credentials, authorization
URLs or callback codes are stored in this document.

The initial manual callback flow expired. A new login using the CLI's automatic
local callback completed successfully, without requiring the owner to copy a URL.
The configured tool allowlist was verified with `codex mcp get`. The automation
tool cannot control the Codex application; the owner performed the reload.
No restart was forced.

## Verified pilot findings

The October 18 audit read app settings, App Review status/history/requirements/
privileges, compliance, rate limits, deprecations and webhook subscriptions.
SocialCli is in development mode, with no open compliance violations, no webhook
subscriptions and no reported deprecations. These are administrative observations,
not production approval or proof that creator operations work.

The official console showed `instagram_manage_contents` Ready for testing but
absent from the App Review draft. Under the owner's existing request to add it,
the permission was added to the draft through the console. Both the console and
MCP now show it among thirteen requested permissions. The review remains
**Not submitted**; no production permission was granted by this change.

The beta MCP's review fields are inconsistent: it reports an approved boolean
alongside UNSUBMITTED, rejection flags without review history/reasons, and a
submission-blocking reason about an earlier review. The console shows Not
submitted and history reports no previous reviews. Do not interpret those isolated
flags as approvals or actual rejected submissions. Use console/history evidence
and retain the discrepancy in the private audit. The MCP is useful for inventory,
but does not yet replace the console as the sole authority for review outcomes.

Only SocialCli was inspected. OAuth also exposed another app in discovery; no
other app's settings or data were queried. No Manage scope or support contact
was used. Sanitized evidence remains in the private operator workspace.

## Recommendation

Use it as an optional administrative audit tool with Read access to SocialCli only.
It can reduce repetitive console work for app settings, App Review status/history/
privileges/requirements, compliance, API limits/deprecations and webhook inventory.
It does not replace SocialCli's creator-account publication and management APIs.
It does not grant missing provider scopes or prove a content operation passed live.

## Documented boundaries

- Endpoint: `https://mcp.facebook.com/devtools`, remote Streamable HTTP, OAuth.
- Read and Manage are per-app grants. Manage adds webhook subscription changes and
  test-event delivery; Read suffices for the proposed permission audit.
- The page lists 11 core tools and says app lifecycle tools are coming soon.
  Do not promise automatic app creation/use-case changes from this document alone.
- Availability is gradual/beta. Tool presence and account eligibility must be
  verified if the owner elects to connect it.
- The owner's OAuth consent is required; never reuse account tokens or secrets to
  silently create a new integration. No support contact is needed for this pilot.

## Concrete comparison

| Need | Existing SocialCli/browser | MCP potential |
| --- | --- | --- |
| Inspect requested versus app-review permissions | Source plus manual console inspection | Structured privileges/requirements reads |
| Determine actual creator token grants | SocialCli grant diagnostics | App-level approval alone is insufficient |
| Publish/edit/delete creator content | SocialCli's existing provider APIs | Not a replacement |
| Review webhook configuration | Console plus offline CLI verification | List subscriptions; Manage can change/test delivery |
| Resolve an ambiguous write | Existing receipt/readback journal | Does not replace reconciliation |

Pilot access and administrative reads are verified. The separately authorized
draft correction was made in the console, not through MCP. No webhook, credential
or creator content was changed. Review-status parity remains an explicit limitation.
