<p align="center"><img src="docs/assets/socialcli-logo.png" width="128" height="128" alt="SocialCli logo: S and terminal prompt &gt;_"></p>

# SocialCli

**Current source version: 0.3.0.** SocialCli SaaS is the current SocialCli
product. The repository root on `main` is its single development workspace;
the CLI, hosted connection service and account/billing components live together
here. Earlier versions remain in Git history for reference and migration.

**A social media CLI for AI agents and the people who supervise them.** Use a terminal-enabled assistant to inspect your accounts, draft posts and prepare user-approved actions on YouTube, Facebook, Instagram and TikTok. SocialCli provides concrete commands, validation, previews and local records for creators and teams.

[Website and documentation](https://socialcli.app/) · [AI agent guide](docs/ai-agents.md) · [Setup](SETUP.md) · [Security](SECURITY.md) · [TikTok limitations](docs/tiktok-review.md)

## Work with your AI agent

Install SocialCli where your agent can run shell commands, point it at your creator workspace and name the brand explicitly. Try:

> Check MyBrand's TikTok connection and available capabilities. Summarize missing permissions without publishing.

> Use MyBrand/media/my-video.mp4, draft a caption in our brand voice and show the complete TikTok dry-run preview. Wait for my approval before sending it.

> Inspect my YouTube video and propose a description change. Show the proposal and fingerprint before applying anything.

[The AI agent guide](docs/ai-agents.md) includes the commands behind these prompts, workspace instructions and the approval workflow. JSON is available on supported diagnostics and inventory commands. SocialCli runs through the agent's shell; it does not include an AI model or an MCP server. Your agent must keep credentials private and preserve the distinction between confirmed, pending and uncertain results.

## Public SaaS pilot

Copy this line into your terminal-enabled AI assistant:

> Install SocialCli: https://socialcli.app/agents

The installation guide verifies the selected release download and uses
https://pilot.socialcli.app for email sign-in, assistant approval and account
connections. The pilot supports eligible YouTube, Facebook, Instagram and TikTok
test accounts. Provider verification is separate from the OAuth publishing
toggle; Meta development access and TikTok sandbox restrictions still apply.
Stripe uses test mode. Planned access costs
USD 10/month after 14 free days from the first successful connection. No real
subscriptions are collected in this pilot.

The current client release target is **0.3.0** (published first to TestPyPI and
then to PyPI through the reviewed release workflow). The compatible hosted
runtime is `socialcli-saas-candidate:45fda63`. The client's wheel and
source archive are verified against the
reviewed build hashes before publication. Provider commercial approvals and live
billing cutover remain separate from package publication. See the
[release record](deploy/saas/release.md).
The [stable release scope](docs/stable-scope.md) excludes unsupported automatic
operations; disabled research catalogue entries are not offered features.

## Install and connect

Use the short installation prompt above. Your assistant needs a persistent shell,
Python 3.11+, macOS or Linux and protected credential storage. The bootstrap
verifies the exact wheel before installation and resolves dependencies from normal
PyPI. Video validation also requires ffprobe from FFmpeg.

The setup order is **brand → credentials → account login → connect**. Ask for the
brand name explicitly, preserve its workspace outside the application repository,
and read socialcli agent-guide setup. The account step uses
https://pilot.socialcli.app. Give each printed link directly to the user without
opening it; the user signs in and approves the assistant and provider account in
their browser. Never request OTPs or provider tokens in chat.

```bash
socialcli account login --brand MyBrand --service https://pilot.socialcli.app --no-browser
socialcli connect youtube --brand MyBrand --management --analytics --no-browser
socialcli connections --brand MyBrand --json
```

Run these after creating the brand and configuring protected credentials as shown
in the [connection guide](docs/connections.md). OS keyring and explicit encrypted
file storage are supported; there is no silent fallback. Use the same workspace,
store and key in later conversations. Connecting never authorizes publication.

For source development use uv sync --group dev, uv run pytest and
uv run socialcli --help in the repository. [SETUP.md](SETUP.md) covers independent
operator apps; customers of the hosted SaaS do not need developer credentials.

## Review and publish

Create `MyBrand/posts/my-video/post.yml` and place your produced file in `MyBrand/media/`. [Example](examples/post.yml):

```yaml
slug: my-video
campaign: clip-vertical
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

Public source code does not grant production API access. Each platform imposes requirements, permissions, quotas and reviews. SocialCli **has no accredited TikTok production approval**. Direct Post is disabled and excluded from the current offer; creator-reviewed inbox upload is supported. Direct Post still lacks the complete user experience controls described in [the technical review](docs/tiktok-review.md). Publishing this repository does not satisfy those requirements.

Metrics and management permissions are separate from publishing permissions. A valid connection does not demonstrate authorization for every feature. Check capabilities and verify remote results before retrying an uncertain operation.

## Contributing and support

Read [CONTRIBUTING.md](CONTRIBUTING.md). For questions or bugs, open an [issue](https://github.com/engels-florentino/SocialCli/issues) without tokens, keys, `.env` files or private logs. Report vulnerabilities through the private channel described in [SECURITY.md](SECURITY.md).

[MIT license](LICENSE). SocialCli is not affiliated with the platforms it integrates.

## Account, media and scheduling

[Account](https://socialcli.app/account/) lets you manage your brand, connected
platforms, approved assistants and billing. The plan covers one brand and one
account per supported platform. Subscribe during the 14-day trial without losing
its remaining days; the first USD 10 payment is due at the original trial end.
The current pilot uses Stripe test mode and collects no live payments.

Instagram can use automatic 15-minute temporary staging on florentino.pro for
supplied originals, up to 512 MiB and subject to quotas. Files are excluded from
backups and removed after the operation or by expiry cleanup. Local schedules
keep the original on the customer runner and stage it only at dispatch. Verified
native scheduling uses provider storage where supported. See
[media handling](MEDIA-INGESTION.md) and [scheduling](NATIVE-SCHEDULING.md).

Read the packaged socialcli agent-guide topic for the operation, the
[capability matrix](docs/capabilities.md), [security](SECURITY.md),
[privacy policy](https://socialcli.app/privacy.html) and
[terms](https://socialcli.app/terms.html). Historical 0.2.6 releases and deploy
records are migration evidence, not a second current product.

SocialCli is operated by **Florentino.pro**. For private account, billing or privacy
questions, contact [admin@socialcli.app](mailto:admin@socialcli.app).
