# SocialCli pilot testing

Copy this single line into Muse, Grok Bot, Dots, or another terminal-enabled assistant:

> Install SocialCli: https://socialcli.app/agents

The assistant reads the guide, downloads the exact reviewed 0.3.0rc1 candidate,
verifies its SHA256, installs it separately and asks which brand to use. Keep its
workspace and protected credentials across conversations.

The agent gives you a sign-in link. Open that link yourself, enter the email code
on the SocialCli page, and approve the assistant and the YouTube connection. Never
paste codes or provider credentials into the agent conversation. Google currently
allows configured test users only; this is not production provider approval.

Ask the agent to verify the connection and read account information. Begin a new
conversation and ask it to use the same workspace and check the connection again.
Installation and account connection never authorize publishing. Any later publishing
requires your original media, the complete preview and your explicit approval.

The hosted pilot currently enables YouTube. Meta and TikTok remain unavailable on
this SaaS service. Existing 0.2.6 installations remain separate. Stripe uses test
mode; no real subscriptions are collected. The planned price is USD10/month with
14free days beginning at the first successful social connection.

If a step fails, retain the installed version, command, sanitized error code and
whether the workspace persists. Do not share keys, tokens, OTPs or pairing URLs.
Do not blindly repeat a write or reconnect after an uncertain network response.
