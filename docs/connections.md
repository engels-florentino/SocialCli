# Connect your accounts with SocialCli

The current hosted SaaS runs at **https://pilot.socialcli.app**. The public website is **https://socialcli.app**. SocialCli 0.3.0rc9 is published on TestPyPI and deployed to the hosted pilot. New connections through the former social.florentino.pro service are closed; do not point a new installation at that legacy origin.

## Sign in before connecting

Ask your assistant to follow the [verified installation guide](agents.md). Keep your brand workspace and protected credentials across conversations. The setup order is brand → credential storage → SocialCli sign-in → provider connection.

```bash
socialcli brand new MyBrand
socialcli agent-guide setup
socialcli account login --brand MyBrand --service https://pilot.socialcli.app --no-browser
socialcli connect youtube --brand MyBrand --management --analytics --no-browser
```

Replace MyBrand with your exact brand. Configure credential storage before account login, as described below. The assistant gives you each printed link without opening or previewing it. You open it, enter the email code and approve the assistant installation; then authorize the provider and choose the exact account in your browser. Keep the command running. Never send codes, tokens or passwords to an assistant chat. Connecting does not approve a post.

Assistant pairing has a 30-minute window; provider connection has a separate 10-minute window. A stale, used or canceled connection link requires a fresh command. Do not reuse it or infer authorization from the account's position in a list. The SaaS flow does not use legacy --account-id/--yes browser-account confirmation.

## Headless encrypted capability storage

Use the reviewed wheel with its headless-credentials extra. The default OS keyring must be supported and unlocked; there is no silent fallback. For a server or assistant without an OS keyring, configure encrypted-file storage explicitly:

```bash
mkdir -m 700 -p "$HOME/.local/share/socialcli-credentials"
socialcli credentials generate-key --key-file "$HOME/.local/share/socialcli-credentials/master.key"
socialcli credentials configure --brand MyBrand --backend encrypted-file --store-file "$HOME/.local/share/socialcli-credentials/store.json" --key-file "$HOME/.local/share/socialcli-credentials/master.key" --yes
```

The generated key is a Fernet key, not an arbitrary password. Keep the key outside the workspace, with mode 0600 in a private directory, and preserve it along with the encrypted store. Never copy either into a public repository. The configure --yes flag confirms that local configuration only. For other brands or runners, choose their own protected storage paths and follow the installed guide.

## Platforms and permissions

YouTube supports upload/read access; --management requests consent for supported management and comment actions, and --analytics requests Analytics access. Facebook uses Pages; Instagram requires a professional account linked to a Page. Add --management or --analytics only for features the user needs. Check the [capability matrix](capabilities.md) and the installed command's --help.

```bash
socialcli connect facebook --brand MyBrand --management --analytics --no-browser
socialcli connect instagram --brand MyBrand --management --analytics --no-browser
socialcli connect tiktok --brand MyBrand --no-browser
socialcli connections --brand MyBrand --json
socialcli account status --brand MyBrand --json
```

Google is External / In production with verified branding, but sensitive YouTube scopes remain unverified and subject to the unverified-app warning and user cap. Meta remains in development and TikTok in sandbox for eligible test accounts. TikTok draft delivery requires the user to complete publication in TikTok. Installed code, a configured connector or a working connection does not establish provider production approval or every permission.

In rc8, `auth PLATFORM` also routes signed-in SaaS installations through this same browser-owned connection flow. SaaS customers never supply a TikTok developer client key or secret. Without a signed-in installation, the command exits with setup guidance instead of waiting for credentials. Independent developer-app authentication requires explicit `--independent` and an interactive terminal.

## Manage your account

Open [Account](/account/) to see connections and approved assistant installations, rename your brand, revoke an assistant, disconnect a provider, manage billing or request account deletion. The plan covers one brand and one connected account per supported platform. Disconnecting the final platform releases the brand label; renaming and reconnecting do not reset your trial.

Your 14 free days start at the first successful social connection. The plan is USD 10/month. You may arrange a subscription before the trial ends without losing free days. Stripe currently uses test mode, so the pilot collects no live payments.

## Renewal, revocation and persistence

Browser sign-in lasts at most 12 hours with encrypted token renewal. Assistant credentials have a 90-day validity window; social grants have a 90-day inactivity window. Provider tokens have their own expiry and revocation rules. Use connection status and the installed guide for explicit renewal/recovery; never delete credentials or reconnect merely because a read timed out.

Revoking an assistant or disconnecting removes the relevant SaaS access. Already issued provider tokens may remain usable until provider expiry or revocation. Use the provider's authorized-app settings to withdraw the grant itself. Deleting the SocialCli account does not delete published content or local files held by your assistant. See the [privacy policy](privacy.html).

## Media and scheduling

For SaaS Instagram publishing, local originals can use temporary storage on florentino.pro automatically when no independent media host is configured. HTTPS links expire 15 minutes after reservation, including upload time. The CLI requests early deletion; the server also cleans expired and interrupted files. The maximum file size is 512 MiB, with account/server quotas; files are excluded from backups.

A local scheduled job keeps its original on your runner and stages it only when the job executes. Keep the runner and file available. Verified provider-native scheduling uses the platform's own storage where supported; Instagram/TikTok do not have an automatic native API scheduling route in this product. Read socialcli agent-guide schedule before choosing a route.

## Independent operator installations

Operators deliberately using their own developer apps can follow [independent setup](../SETUP.md). Those credentials and storage choices are separate from the hosted SaaS. Legacy deployment instructions are historical migration material, not the current customer setup.
