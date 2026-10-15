# Files and hosting

SocialCli accepts already produced files. It requires `ffprobe` to inspect dimensions, duration and format; it does not generate or reencode videos.

Store media under `<Brand>/media/` and reference a relative path in `post.yml`. Paths cannot escape the media folder. For SaaS Instagram publishing, SocialCli can stage local originals automatically on florentino.pro when no independent host is configured. The opaque HTTPS URL expires 15 minutes from reservation, upload time included. The CLI requests deletion after the operation; application and host cleanup remove expired files. Files are excluded from backups. Maximum 512 MiB per file/account, ten live reservations per account and 2 GiB globally, with disk headroom. Quotas may delay admission. Do not treat this as storage for future jobs.

Local schedules retain the original on the customer runner and upload only at dispatch; the runner, file and credentials must remain available. Independent-app users still need their own reachable HTTPS host configured through instagram.media_url_base. Existing explicit hosts remain supported.

The commands documented by `socialcli media --help` prepare, transfer and verify bundles on remote deployments configured by the operator. Transferring a file neither approves nor publishes a post. A byte check or HTTP 200 does not prove that a remote post exists.

Do not include client files, third-party protected content, URL tokens or server configuration in the public application repository.
