# Isolated legacy cutoff and rollback rehearsal

The checked-in `nginx-legacy-cutoff.conf` was exercised using the installed
`jc21/nginx-proxy-manager:latest` image in disposable containers on Florentino.
This did not change NPM's live configuration or route public traffic.

Two fresh upstreams were tested: the actual `socialcli-connections:local` image
(public 0.2.6 baseline), then `socialcli-saas-candidate:0.3.0rc1` with its persistent
legacy cutoff enabled. Each used a new temporary vault, random disposable key,
fictional provider configuration, no mounted production state, no published ports
and Docker networking disabled. The proxy shared only that disposable upstream's
network namespace. No provider authorization link was followed.

The baseline accepted a direct anonymous creation request with HTTP 201; the
candidate rejected it with 410. Through the proxy, both images passed the same
26 cases each, covering `socialcli.app` and `social.florentino.pro`:

- POST to the creation path and its trailing slash, percent-encoded letter,
  encoded slash, repeated slashes, dot segments and query-string variants: 410.
- GET, OPTIONS and HEAD to creation: 410.
- A double-encoded nonmatching path and nonexistent poll ID: 404 from upstream.
- Health remained reachable with HTTP 200. `nginx -t` succeeded.

All disposable containers and plaintext rehearsal state were removed. The test
configuration supplied temporary runtime directories and a worker user because
NPM's normal container initialization was deliberately bypassed. Production NPM's
process configuration was not changed.

This proves the normalized-path guard works with the installed Nginx binary and
survives replacing this upstream with the baseline image. It does **not** prove
that both real public hosts have the guard installed, that TLS/DNS and the full
NPM configuration are correct, or that real grants have migrated. Public cutover
still requires inspecting both host configurations, retaining the edge guard,
verifying the shared vault/keys and 600-second drain, and testing the public paths.
Do not expose a rollback container directly or restore an older database to roll
back code.

Reference: [NGINX location matching and request processing](https://nginx.org/en/docs/http/request_processing.html).
