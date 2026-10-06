# Set up SocialCli with your accounts

SocialCli is a desktop command-line application. This release does not distribute shared developer credentials: each operator configures their own provider applications, including the required permissions and reviews. Do not copy another person's credentials or publish them on GitHub.

## Workspace

```bash
mkdir my-social
cd my-social
socialcli brand new MyBrand
```

Complete `MyBrand/accounts.yml` with **your** account identifiers. Tokens and secrets belong in `MyBrand/.secrets/`, created with restricted permissions. To use this folder from elsewhere, set `SOCIALCLI_ROOT` to its absolute path or use `--root PATH`.

## YouTube

1. Create a project in [Google Cloud](https://console.cloud.google.com/).
2. Enable YouTube Data API v3 and YouTube Analytics API if you need metrics.
3. Configure the consent screen and add your account as a test user if the project is in testing.
4. Create a desktop OAuth client.
5. Run `socialcli auth youtube --brand MyBrand` and provide your client ID and secret when prompted.

The local callback is `http://localhost:8723/callback`. Set the channel in `accounts.yml`. For additional content management, see `socialcli auth youtube --help`; extra permissions are opt-in. Automatic renewal requires a valid refresh token. Revocation or permission changes may require authorization again.

## Facebook and Instagram

Configure your application in [Meta for Developers](https://developers.facebook.com/) with the products and permissions required for your accounts. Facebook publishes to Pages. The implemented Instagram flow requires a professional account linked to a Page. Testing and external users may require review and advanced access.

```bash
socialcli auth facebook --brand MyBrand
socialcli auth instagram --brand MyBrand
```

Set `facebook.page_id` and `instagram.ig_user_id`. Instagram media must have HTTPS URLs accessible to the provider; configure your hosting and `instagram.media_url_base`. Read [MEDIA-INGESTION.md](MEDIA-INGESTION.md). Meta token expiration differs from YouTube or TikTok refresh flows; follow command diagnostics and provider policies.

## TikTok

1. Create your desktop application in [TikTok for Developers](https://developers.tiktok.com/).
2. Configure Login Kit, Content Posting API and the redirect `http://localhost:8723/callback`.
3. Request `user.info.basic` and `video.upload` for inbox. Enable only the extra permissions you actually need.
4. In sandbox, add your account as a target user.
5. Run `socialcli auth tiktok --brand MyBrand` and enter **your application's** credentials locally.

Authorization supplies the `open_id`. The template retains `mode: inbox` and `auditada: false`. The video uploads to your inbox; you must finish publishing in TikTok. Do not upload the same file twice to test the flow.

Successful authorization or a sandbox upload does not establish production approval. Review [docs/tiktok-review.md](docs/tiktok-review.md) before preparing an application. Never distribute a shared application's secret with the CLI.

## Verify

```bash
socialcli auth status --brand MyBrand --platform tiktok --json
socialcli doctor --brand MyBrand --json
socialcli capabilities --brand MyBrand --json
socialcli publish my-video --brand MyBrand --only tiktok --dry-run
```

Use `socialcli COMMAND --help` for available options. Do not post complete private diagnostics in issues. Before deleting a brand, save anything you need and separately revoke platform permissions; deleting a local file does not revoke remote access.
