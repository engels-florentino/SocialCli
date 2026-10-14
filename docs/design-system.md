# SocialCli visual system

The approved 0.2.6 connection screens are the visual reference. All customer
surfaces use the existing SocialCli logo, system font stack, ink `#10213d`,
muted `#627087`, blue `#1464ec`, line `#e5eaf2`, white cards and `#f5f7fb`
background. No external fonts or new generated artwork.

Edit `socialctl/ui/assets/`, then run `uv run python scripts/build_ui_assets.py`.
`--check` verifies both generated bundles without changing files. The static
website and packaged server consume the same tokens and controls; their
layouts can differ to suit content. Account screens reuse the connection shell.

Controls use visible labels, a minimum 48px input height, visible keyboard focus,
field-specific errors, no color-only states and reduced-motion overrides.
Copy is English. Account names and supplied values must be escaped. Preserve
CSP with same-origin styles and images, and never put credentials into HTML.

Review new states at 360, 390 and 1440px, with keyboard focus, 200% zoom,
long account/email names, validation errors and reduced motion. Use fictional
fixtures; snapshots alone do not prove OTP, payment or platform authorization.

The development-only [account catalogue](account-ui-verification.md) renders
25 fictional states from the current routes and views at selectable widths.
It records source coverage separately from completed visual acceptance.

## Email and hosted billing

Supabase OTP emails use the same system font, ink and primary blue as this
source. The email uses an inline generated style because email clients strip
CSS links. It must show the one-time code, expiry and "If you didn't request
this code, you can ignore this email." Keep the token out of logs and URLs.

Set Stripe account and Customer Portal branding to the existing SocialCli logo,
primary `#1464ec`, background `#f5f7fb`, display name SocialCli and support URL
`https://socialcli.app/connections.html`. Stripe controls its hosted layout;
review Checkout, portal, receipts, recovery and cancellation screens in test
mode before making the service public. Price copy is USD 10/month everywhere.
