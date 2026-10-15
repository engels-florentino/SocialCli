# Temporary Instagram media

The approved provider original is stored on florentino.pro for a maximum URL
lifetime of 900 seconds from reservation (upload time included). The CLI deletes
it when publishing finishes or fails. A service janitor runs every second;
a host timer provides cleanup every 15 seconds even if the container is stopped.
Physical cleanup resumes at boot after a server outage. Downloads stop at expiry;
GET/HEAD support a single byte range. This is not permanent or scheduled storage.

Enable `SOCIALCLI_TEMP_MEDIA_DIR=/temporary-media` and bind the private host
`/home/blades3/projects/socialcli-saas/temporary-media` to that path. Keep it outside
`candidate/state`, backup manifests, Restic inputs and auth database backups.
Files and partials are 0600 in a 0700 directory; filenames contain only the expiry
and a random identifier. No client filenames are accepted. Quotas: 512 MiB per
file and account, ten reservations per account, 2 GiB across live reservations,
plus 256 MiB disk headroom. Admission is serialized in SQLite; upload bytes stream
outside database transactions and have a five-minute transport limit.

In the Nginx proxy for the pilot add a dedicated upload location with
`client_max_body_size 512m; proxy_request_buffering off; proxy_buffering off;`
and `proxy_read_timeout 310s; proxy_send_timeout 310s; client_body_timeout 60s;`.
Route `/media/` to the same service, disable caching, response buffering and
access logging for opaque URLs. Preserve `Host` and overwrite `X-Real-IP` with
`$remote_addr`. Set `SOCIALCLI_TRUSTED_PROXY_CIDRS` to the actual dedicated proxy
peer or isolated socialcli-pilot-edge CIDR; never trust the public Internet.
Uvicorn proxy-header interpretation remains disabled.

Install the paired `socialcli-saas-candidate-media-cleanup` service/timer and
`temporary_media_cleanup.py` under `ops/`. Verify an expired fixture is deleted
with the app stopped, then restart it and check current expiry/owner/size tests.
Do not generate production media to test this feature. An original file remains
on the customer runner for local scheduling; the temporary URL is created only
when that approved job dispatches. Native Instagram video helpers continue to
use Meta direct uploads; their image URL contracts remain explicit.
