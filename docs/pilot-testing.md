# SocialCli pilot testing

Copy this single line into Muse, Grok Bot, Dots, or another terminal-enabled assistant:

> Install SocialCli: https://socialcli.app/agents

The assistant reads the guide, downloads the exact reviewed 0.3.0rc9 candidate,
verifies its SHA256, installs it separately and asks which brand to use. Keep its
workspace and protected credentials across conversations.

The agent gives you a sign-in link. Open that link yourself, enter the email code
on the SocialCli page, and approve the assistant and your selected social account connection. Never
paste codes or provider credentials into the agent conversation. Google is External / In production with verified branding; sensitive YouTube
scopes remain unverified, with an unverified-app warning and user cap.

Ask the agent to verify the connection and read account information. Begin a new
conversation and ask it to use the same workspace and check the connection again.
Installation and account connection never authorize publishing. Any later publishing
requires your original media, the complete preview and your explicit approval.

The SaaS is the successor to 0.2.6. Its pilot enables YouTube, Facebook, Instagram
and TikTok. Meta access depends on app roles and granted permissions. TikTok uses
the existing sandbox and configured target users, with draft uploads only.
The service retains existing authorized connections. That
does not prove every feature for every creator; verify the exact account and
permissions in each supervised pilot. Stripe uses test
mode; no real subscriptions are collected. The planned price is USD 10/month with
14 free days beginning at the first successful social connection.

If a step fails, retain the installed version, command, sanitized error code and
whether the workspace persists. Do not share keys, tokens, OTPs or pairing URLs.
Do not blindly repeat a write or reconnect after an uncertain network response.
