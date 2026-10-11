<p align="center"><img src="docs/assets/socialcli-logo.png" width="128" height="128" alt="SocialCli logo: S and terminal prompt &gt;_"></p>

# SocialCli

**A social media CLI for AI agents and the people who supervise them.** Use a terminal-enabled assistant to inspect your accounts, draft posts and prepare user-approved actions on YouTube, Facebook, Instagram and TikTok. SocialCli provides concrete commands, validation, previews and local records for creators and teams.

[Website and documentation](https://engels-florentino.github.io/SocialCli/) · [AI agent guide](docs/ai-agents.md) · [Setup](SETUP.md) · [Security](SECURITY.md) · [TikTok limitations](docs/tiktok-review.md)

## Work with your AI agent

Install SocialCli where your agent can run shell commands, point it at your creator workspace and name the brand explicitly. Try:

> Check MyBrand's TikTok connection and available capabilities. Summarize missing permissions without publishing.

> Use MyBrand/media/my-video.mp4, draft a caption in our brand voice and show the complete TikTok dry-run preview. Wait for my approval before sending it.

> Inspect my YouTube video and propose a description change. Show the proposal and fingerprint before applying anything.

[The AI agent guide](docs/ai-agents.md) includes the commands behind these prompts, workspace instructions and the approval workflow. JSON is available on supported diagnostics and inventory commands. SocialCli runs through the agent's shell; it does not include an AI model or an MCP server. Your agent must keep credentials private and preserve the distinction between confirmed, pending and uncertain results.

## Install

Use macOS or Linux with Python 3.11 or later, Git, and an available OS credential store. Video checks also require `ffprobe`, included with FFmpeg. If `python3 --version` reports an older version, install a supported Python first or replace `python3` below with your supported interpreter, such as `python3.12`. SocialCli inspects files; it does not generate, crop or reencode content.

```bash
python3 --version  # Requires Python 3.11 or later
mkdir my-social
cd my-social
python3 -m venv .venv
source .venv/bin/activate
python -m pip install "git+https://github.com/engels-florentino/SocialCli.git"
socialcli --help
socialcli brand new MyBrand
```

Keep this workspace outside the application repository. In a new terminal, run `cd my-social` and `source .venv/bin/activate` before using SocialCli.

For development:

```bash
git clone https://github.com/engels-florentino/SocialCli.git
cd SocialCli
uv sync --group dev
uv run socialcli --help
uv run pytest
```

## Your first brand

The installation steps create `MyBrand`. Replace that example name with your own brand and complete `MyBrand/brand.md` with its voice and identity. To connect through the hosted service, sign in on the provider's page and confirm your own account in the terminal:

```bash
socialcli connect tiktok --brand MyBrand
socialcli connections --brand MyBrand --json
```

**AI agents:** run `socialcli connect PLATFORM --brand MyBrand --no-browser`. Give the printed login link directly to the user without opening, fetching or previewing it. The user opens the landing page and presses Connect to start the single-use flow. Keep the command running while the user authorizes; generate a fresh link if it expires or was already used. See the [agent connection instructions](docs/ai-agents.md#hand-the-login-link-to-the-user-without-opening-it).

`connect` saves the selected account identifiers in `MyBrand/accounts.yml` automatically. You do not need your own developer app, client ID, client secret or pasted provider token for shared mode. Your OS credential store must be available and unlocked. Connecting does not upload or publish content.

**Hosted access today:** TikTok is in sandbox and accepts only authorized target accounts. Google is in Testing for authorized test users; Meta is unpublished and restricted to development access. Anyone can install the public CLI, but installation does not grant access to these hosted provider integrations. If your account is not eligible, wait for production availability or use a service whose operator has the required provider access. See the [connection guide](docs/connections.md).

Other configured networks use the same browser workflow:

```bash
socialcli connect youtube --brand MyBrand
socialcli connect facebook --brand MyBrand
socialcli connect instagram --brand MyBrand
```

Operators who deliberately use their own developer applications can instead follow [independent-app setup](SETUP.md) and use `socialcli auth PLATFORM --brand MyBrand`.

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

## Browser account connections

The shared connection mode lets creators authorize their accounts without their own provider developer registration. Availability depends on the operator configuring and obtaining approval for each integration; implementation alone does not establish public production access.

```sh
socialcli connect youtube --brand MyBrand
socialcli connections --brand MyBrand --json
socialcli disconnect youtube --brand MyBrand
```

[Connection guide](docs/connections.md) · [Self-host the connection service](deploy/connection-service/README.md). Independent-app `socialcli auth` remains supported.


For an agent without interactive stdin, obtain the user’s explicit authorization for the exact account ID and brand first, then use `socialcli connect PLATFORM --brand MyBrand --no-browser --account-id ACCOUNT_ID --yes`. `--yes` confirms this account connection only; it does not authorize publication. Never guess an ID or use its position in a returned list. Replacing independent credentials additionally requires `--replace-independent`. Without these explicit arguments, noninteractive execution stops before creating an authorization link.


Agent reliability: see the [AI agent guide](docs/ai-agents.md) for bounded JSON
reads, unified inbox, exact source links and supervised community batches, and
the [capability matrix](docs/capabilities.md) for permissions and network limits.
For servers, [explicit encrypted capability storage](docs/connections.md#headless-encrypted-capability-storage)
is available with the `headless-credentials` extra; no automatic fallback is used.
