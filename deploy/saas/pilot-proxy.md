# Isolated HTTPS pilot — prepared, not deployed

DNS required in Namecheap: A `pilot` → `71.250.63.180`, automatic TTL.
The public `socialcli.app` host remains on0.2.6. On13October the pilot A/AAAA
records were absent and the public account route returned404. Do not interpret
these instructions as a configured proxy or successful public browser test.

## Prepared routing

Use the existing Nginx Proxy Manager2.15.1, creating a separate Proxy Host:

| Setting | Value |
| --- | --- |
| Domain | `pilot.socialcli.app` |
| Scheme | `http` |
| Forward hostname | `socialcli-saas-candidate` |
| Forward port | `8787` |
| Cache Assets | Off |
| Websocket support | Off (not required) |
| Block Common Exploits | On; verify OAuth/form requests before enabling providers |
| SSL | New Let's Encrypt certificate for this exact pilot hostname |
| Force SSL | On |
| HSTS | Pilot host only; no subdomains/preload |
| Access list | Public HTTPS pilot, application login protects account routes |

Advanced configuration: include `nginx-legacy-cutoff.conf` and:

```nginx
client_max_body_size 1m;
client_body_buffer_size 1m;
access_log off;
error_log /dev/null crit;
```

Do not cache authorization/account responses. Application audit/health records
provide diagnostics without retaining access-log URLs containing single-use links.
The one-megabyte request limit matches the Stripe webhook cap and disallows large
uploads; there is no customer-media upload/storage endpoint.

Create the dedicated `socialcli-pilot-edge` Docker network. Persist membership of
NPM and the candidate in their Compose configurations, then connect the existing
NPM container without recreating the shared proxy. NPM must not join the private
`socialcli-identity` network. Apply `pilot.compose.yaml` with the candidate base
Compose file; this changes only the candidate origin/network. Preserve its database,
keys, test Stripe settings, provider-off and reminder-off state. Health probes must
use Host `pilot.socialcli.app`. The loopback8789 listener remains for local probes.

Before changing the candidate, take and verify the encrypted backup pair and retain
its previous image ID. For rollback restore the previous candidate image/origin and
remove/disable only the pilot proxy host; never restore a historical database over
current payment/identity state or change the public host39/certificate43.

## Verification required after DNS and routing

- `nginx -t`, valid public TLS hostname/chain and no unexpected redirects to main.
- `/healthz`, `/agents`, `/account/login`200; unauthenticated `/account/`303 to login.
- Canonical Host validation, secure host-only cookies, CSRF/origin rejection and
  no-cache account responses. Verify direct/encoded legacy create routes410.
- OTP account registration and installation pairing in an explicit fictional
  workspace using `socialcli account login --service https://pilot.socialcli.app`.
  The user opens all pairing/connection links; an agent must never prefetch them.
- A dedicated Stripe test webhook at the pilot billing path, pinned API version
  and exact event allowlist. Keep signing secret exclusively in private server env.
  Prove one actual signed delivery, durable processing and duplicate idempotency.
- Stripe test Checkout/Portal return to the pilot origin. Shared Stripe merchant
  branding remains a separate owner decision; do not rename other products.
- Real social-provider pilots need separately configured callbacks and explicit
  account/media approvals; the initial pilot overlay keeps all providers disabled.

Reference: [NPM dedicated Docker networks](https://nginxproxymanager.com/advanced-config/).
