# Meta Social Technologies MCP assessment

Source: https://developers.facebook.com/documentation/mcp/devtools-mcp
Official page updated October 7, 2026; read in authenticated Safari on October 18.
Web text retrieval failed; the official page was read in authenticated Safari.

## Pilot preparation

Execution on October 18 added `meta_social_technologies` to the operator's local
Codex configuration with the official endpoint, an eight-tool read allowlist,
and explicit `developer_tools_mcp_app_read` OAuth scope. The owner completed the
browser authorization and `codex mcp login` reported success. App selection and
returned app-level privileges still need verification through `devtools_app_list`;
OAuth success alone does not prove which app is accessible. No MCP app data has
been read yet: the current conversation has not loaded the new server tools.

The client's default OAuth discovery included management and business scopes;
that unapproved flow was cancelled before use. The replacement explicitly requests
only the read scope, also persisted for future logins. No credentials, authorization
URLs or callback codes are stored in this document.

The initial manual callback flow expired. A new login using the CLI's automatic
local callback completed successfully, without requiring the owner to copy a URL.
The configured tool allowlist was verified with `codex mcp get`. The automation
tool cannot control the Codex application, so activation in this conversation
requires an owner-initiated reload/new session. No restart was forced.

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

First pilot success would be a read-only report for SocialCli matching the console,
including `instagram_manage_contents` Ready for testing, without changing any app,
webhook, credential or creator content.
