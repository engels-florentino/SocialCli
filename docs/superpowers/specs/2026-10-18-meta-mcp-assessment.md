# Meta Social Technologies MCP assessment

Source: https://developers.facebook.com/documentation/mcp/devtools-mcp
Official page updated October 7, 2026; read in authenticated Safari on October 18.
Web text retrieval failed. No MCP installation or OAuth authorization was performed.

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
