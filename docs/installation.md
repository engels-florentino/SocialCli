# Install SocialCli with your assistant

Copy this line into Muse, Grok, Dots or another assistant that can run terminal commands:

> Install SocialCli: https://socialcli.app/agents

The assistant follows a compact bootstrap, verifies the published 0.3.0rc4 wheel from TestPyPI by SHA256 and installs it in an isolated environment. Dependencies come from normal PyPI. The pilot is not a stable production PyPI release; do not substitute a similarly named package or overwrite an existing installation silently.

## Requirements

Use macOS or Linux, Python 3.11 or newer, persistent workspace storage and a protected credential store. Video inspection requires ffprobe from FFmpeg. SocialCli uses supplied originals and does not generate, crop or transcode media. An assistant without a persistent shell cannot run the CLI by itself.

## First connection

Tell the assistant your exact brand name. It creates your workspace outside the application repository, configures protected credentials and runs SocialCli account login against https://pilot.socialcli.app. You open the printed link, enter your email code on the SocialCli page and approve the installation. The assistant then starts the requested provider connection; you authorize and select your account in your browser.

The setup order is brand → credentials → account login → connect. See the [connection guide](connections.md), including [headless storage](connections.md#headless-encrypted-capability-storage). Never share an OTP or token in chat. A fresh conversation should reuse the same workspace and credentials.

## Start with a read

Ask your assistant to read account status, connections and capabilities before writing. Then use your own produced content, inspect the complete preview and explicitly approve the exact operation. Installation and connection consent do not approve publication.

Google Testing, Meta development and TikTok sandbox eligibility still apply. Stripe uses test payments only. The plan is USD 10/month after 14 free days from the first successful social connection. See [availability](platforms.html) and [terms](terms.html).

## Commands and development

Run socialcli --version to verify0.3.0rc4. The packaged socialcli agent-guide provides offline setup, publish, schedule, inbox and error topics. The internal Python module is socialctl; socialctl remains a command alias.

For source development only:

```bash
git clone https://github.com/engels-florentino/SocialCli.git
cd SocialCli
uv sync --group dev
uv run pytest
uv run socialcli --help
```

Your creator workspace belongs outside that repository. Independent provider-app setup remains available for operators who deliberately manage their own applications.
