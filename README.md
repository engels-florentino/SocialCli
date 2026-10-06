# SocialCli

**Publish and manage your own content on YouTube, Facebook, Instagram and TikTok from the terminal.** SocialCli is a public project for creators and teams who want to review posts, keep brands separate and retain evidence of their actions.

[Website and documentation](https://engels-florentino.github.io/SocialCli/) · [Setup](SETUP.md) · [Security](SECURITY.md) · [TikTok limitations](docs/tiktok-review.md)

## Install

Requires Python 3.11 or later. Video checks also require `ffprobe`, included with FFmpeg. SocialCli inspects files; it does not generate, crop or reencode content.

```bash
python -m pip install "git+https://github.com/engels-florentino/SocialCli.git"
socialcli --help
```

For development:

```bash
git clone https://github.com/engels-florentino/SocialCli.git
cd SocialCli
uv sync --group dev
uv run socialcli --help
uv run pytest
```

## Your first brand

Work in a data folder you control. The current folder is the default; set `SOCIALCLI_ROOT` or pass `--root` to choose another folder. Your account files belong outside the application repository.

```bash
mkdir my-social
cd my-social
socialcli brand new MyBrand
```

Complete `MyBrand/brand.md` and `MyBrand/accounts.yml`. Configure provider applications and authorize **your own accounts** as described in [SETUP.md](SETUP.md):

```bash
socialcli auth youtube --brand MyBrand
socialcli auth tiktok --brand MyBrand
socialcli auth status --brand MyBrand --platform tiktok --json
```

Enter credentials through the local setup flow; they are stored in `MyBrand/.secrets/`. No other user's credentials are included. Each operator supplies their own provider application credentials. This release does not offer a shared SocialCli OAuth backend.

## Review and publish

Create `MyBrand/posts/my-video/post.yml` and place your produced file in `MyBrand/media/`. [Example](examples/post.yml):

```yaml
slug: my-video
campaign: vertical-clip
platforms:
  tiktok:
    body: "A story worth telling."
    hashtags: []
    media: ["my-video.mp4"]
```

```bash
socialcli publish my-video --brand MyBrand --only tiktok --dry-run
socialcli publish my-video --brand MyBrand --only tiktok
```

The first command displays a preview without publishing. The second displays it again and requests confirmation. TikTok defaults to **inbox**: the video reaches your inbox and you complete publication in TikTok. A pending confirmation status does not mean the video is published.

## Features

- Separate brands, local credentials and token renewal where supported by the provider.
- File validation, previews and confirmation before publication.
- Publishing, inventory, metrics and content management, subject to each platform's permissions and capabilities.
- Scheduling with approval checks and handling of ambiguous results to reduce duplicates.
- Adapter tests with simulated responses that do not publish to real accounts.

Each command group documents its options with `socialcli <group> --help`. The internal Python package remains `socialctl`; the public command is `socialcli`. `socialctl` remains a compatibility alias.

## Status and limitations

Public source code does not grant production API access. Each platform imposes requirements, permissions, quotas and reviews. SocialCli **has no accredited TikTok production approval**. Direct Post requires an audit and still lacks the complete user experience controls described in [the technical review](docs/tiktok-review.md). Publishing this repository does not satisfy those requirements.

Metrics and management permissions are separate from publishing permissions. A valid connection does not demonstrate authorization for every feature. Check capabilities and verify remote results before retrying an uncertain operation.

## Contributing and support

Read [CONTRIBUTING.md](CONTRIBUTING.md). For questions or bugs, open an [issue](https://github.com/engels-florentino/SocialCli/issues) without tokens, keys, `.env` files or private logs. Report vulnerabilities through the private channel described in [SECURITY.md](SECURITY.md).

[MIT license](LICENSE). SocialCli is not affiliated with the platforms it integrates.
