# Files and hosting

SocialCli accepts already produced files. It requires `ffprobe` to inspect dimensions, duration and format; it does not generate or reencode videos.

Store media under `<Brand>/media/` and reference a relative path in `post.yml`. Paths cannot escape the media folder. For Instagram, the provider must be able to download each file over HTTPS from hosting you control. Set the public base URL in `instagram.media_url_base`.

The commands documented by `socialcli media --help` prepare, transfer and verify bundles on remote deployments configured by the operator. Transferring a file neither approves nor publishes a post. A byte check or HTTP 200 does not prove that a remote post exists.

Do not include client files, third-party protected content, URL tokens or server configuration in the public application repository.
