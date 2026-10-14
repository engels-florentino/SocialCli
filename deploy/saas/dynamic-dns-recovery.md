# Dynamic address recovery — 14 October 2026

During the signed Stripe test, public-name SSH and HTTPS requests began timing out.
The Mac retained general Internet access. The server remained available through
its pre-existing LAN address192.168.1.197; stored SSH host-key fingerprints for the
LAN and public names matched before switching the administration connection.
The private/public application containers were running and healthy.

The server's externally observed IPv4 address had changed from71.250.63.180 to
71.250.63.127. ddclient successfully updated other configured Florentino records,
but authoritative DNS for socialcli.app retained the old address. Configuration
inspection found the exact error: the `login=socialcli.app` block targeted
`@.florent.ren`. Namecheap reported no matching A record for those failed updates.

Corrected only that host line to `@.socialcli.app`, preserving the domain login,
existing private password, IP detector and all unrelated domain blocks. Kept a
mode0600 pre-change copy in the server's private SocialCli directory and restarted
the existing enabled ddclient service. No DNS credentials were printed or copied
to this repository. No NPM, public application, TLS or router configuration changed.

Verified afterward:

- ddclient cache contains @.socialcli.app with status good and IP71.250.63.127.
- Authoritative Namecheap DNS and1.1.1.1 return71.250.63.127, TTL180.
- HTTPS home page returns200 at the new address with normal certificate validation
  and Host/SNI socialcli.app. Local cached-name requests initially still timed out;
  this is not evidence that all resolver caches have expired.
- Public application container identity and healthy status are unchanged.
- Candidate billing/backup/backup-check timers are active; maintenance and the
  post-webhook encrypted backup completed successfully, with diagnostics status ok.

The `/healthz` path on the existing public static/proxy host returns404 and is not
the private candidate health endpoint. Use candidate loopback probes with the
canonical Host for that check; do not mistake this existing route distinction for
a failed candidate deployment.

Pilot DNS remains absent. Its requested record is now CNAME pilot → socialcli.app
so it follows the root's dynamic address; this replaces the stale fixed-IP request.
The user retains the Namecheap record-creation task. Updating the root address did
not create a pilot record or expose the candidate publicly.
