# Isolated HTTPS pilot — prepared, not deployed

DNS required in Namecheap: **CNAME `pilot` → `socialcli.app`**, automatic TTL.
This supersedes the earlier fixed-IP A-record request: the WAN address changed
on14October and the root domain now follows the working dynamic DNS updater.
Do not keep an A/AAAA record alongside the CNAME for the same pilot hostname.
The current root A address is71.250.63.127; it is not a permanent server address.
The public `socialcli.app` host remains on0.2.6. On13October the pilot A/AAAA
records were absent and the public account route returned404. Do not interpret
these instructions as a configured proxy or successful public browser test.

## Prepared routing

On 14 October 2026, the operator-authorized CNAME `pilot` → `socialcli.app`
was saved in Namecheap and verified through public resolver 1.1.1.1.
SSH through `florentino.pro:1384` also succeeded using the previously trusted
server host key. DNS is complete; the pilot proxy/TLS route is not yet deployed.
The pilot overlay now pins the already verified candidate image `57efd47`,
avoiding a rollback to the older image previously present in that overlay.

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

References: [NPM dedicated Docker networks](https://nginxproxymanager.com/advanced-config/),
[Namecheap CNAME setup](https://www.namecheap.com/support/knowledgebase/article.aspx/9646/2237/how-to-create-a-cname-record-for-your-domain/).

## Prepared host activation — 14 October 2026

The operator authenticated NPM in Safari over a loopback-only SSH tunnel.
The candidate now uses canonical origin `https://pilot.socialcli.app` and the
verified image `57efd47`. Its Compose configuration explicitly disables all four
providers and customer reminders. The dedicated `socialcli-pilot-edge` membership
is persisted in both Compose files; the running NPM container was connected without
recreation. NPM is not on `socialcli-identity`. Original Compose files were retained
in the server's private directory for rollback.

The candidate and existing public connection service were healthy after recreation;
local Host-bound probes of `/healthz`, `/agents` and `/account/login` returned 200.
The NPM form is prepared with the documented destination, request-size limit,
legacy-create guards, disabled request logs, new certificate, forced HTTPS and
host-only HSTS. The final public activation is awaiting action-time confirmation;
these checks do not establish public TLS or a completed browser login journey.
