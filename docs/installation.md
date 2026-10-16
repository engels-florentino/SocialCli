# Install SocialCli with your assistant

Copy this line into Muse, Grok, Dots or another assistant that can run terminal commands:

> Install SocialCli: https://socialcli.app/agents

The assistant follows a compact bootstrap, verifies the published 0.3.0rc10 wheel from TestPyPI by SHA256 and installs it in an isolated environment. Dependencies come from normal PyPI. The pilot is not a stable production PyPI release; do not substitute a similarly named package or overwrite an existing installation silently.

## If your assistant still reports rc1

The server and the CLI installed in an assistant's environment update separately.
Ask the assistant to fetch https://socialcli.app/agents again using its
terminal HTTP client, rather than reuse a previous conversation or search excerpt.
It must follow the verified wheel installation instructions and run the selected
executable with `--version`; the result must be exactly `0.3.0rc10`. If PATH still
selects an older installation, use the new environment's absolute executable for
all commands. Never infer the installed version from the website.

Updating the client must preserve the existing workspace, credential store and
external encryption key. Do not create another brand, reset credentials or
reconnect accounts merely because a new client is available. Do not replace an
existing installation silently; use an isolated environment when needed.

## Requirements

Use macOS or Linux, Python 3.11 or newer, persistent workspace storage and a protected credential store. Video inspection requires ffprobe from FFmpeg. SocialCli uses supplied originals and does not generate, crop or transcode media. An assistant without a persistent shell cannot run the CLI by itself.

## First connection

Tell the assistant your exact brand name. It creates your workspace outside the application repository, configures protected credentials and runs SocialCli account login against https://pilot.socialcli.app. You open the printed link, enter your email code on the SocialCli page and approve the installation. The assistant then starts the requested provider connection; you authorize and select your account in your browser.

The setup order is brand → credentials → account login → connect. See the [connection guide](connections.md), including [headless storage](connections.md#headless-encrypted-capability-storage). Never share an OTP or token in chat. A fresh conversation should reuse the same workspace and credentials.

## Start with a read

Ask your assistant to read account status, connections and capabilities before writing. Then use your own produced content, inspect the complete preview and explicitly approve the exact operation. Installation and connection consent do not approve publication.

Google is External / In production, but its sensitive YouTube scopes are not yet verified; the unverified-app warning and user cap still apply. Meta development and TikTok sandbox eligibility remain separate. Stripe uses test payments only. The plan is USD 10/month after 14 free days from the first successful social connection. See [availability](platforms.html) and [terms](terms.html).

## Commands and development

Run socialcli --version to verify 0.3.0rc10. The packaged socialcli agent-guide provides offline setup, publish, schedule, inbox and error topics. The internal Python module is socialctl; socialctl remains a command alias.

For source development only:

```bash
git clone https://github.com/engels-florentino/SocialCli.git
cd SocialCli
uv sync --group dev
uv run pytest
uv run socialcli --help
```

Your creator workspace belongs outside that repository. Independent provider-app setup remains available for operators who deliberately manage their own applications.
