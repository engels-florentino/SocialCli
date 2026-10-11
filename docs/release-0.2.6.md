# SocialCli 0.2.6

October 11, 2026. A polished account-connection experience for creators.

- Connection screens use the approved SocialCli logo, a clear provider-specific
  sign-in button, connection steps and concise guidance about account access.
- Responsive layouts support desktop and mobile, with keyboard focus indicators
  and reduced-motion preferences. All customer-facing copy remains in English.
- Authorization received, cancellation, expired links and errors share the same
  visual language and explain how to continue or request a fresh link.
- CSS and the logo ship inside the service wheel and load from the same origin.
  No external fonts, scripts or tracking services are added.
- Browser errors receive branded HTML. CLI API errors remain JSON. OAuth state,
  browser binding, form verification and account confirmation are unchanged.

Deploy the updated connection service to make these screens available. Existing
clients receive the new design automatically on their next connection; they do
not need an upgrade or account reconnection for this UI change.

## Verification

Full suite on Python 3.13.5: **2,183 passed**. Independent read-only review found
no Critical or Important issues. Visual checks covered desktop, iPhone 16 Pro Max
and iPhone SE layouts using offline fictional connection pages. Integration
tests cover provider buttons, packaged assets, callback states, expired links,
HTML escaping and unchanged JSON errors.
Source and wheel builds passed. A clean wheel installation outside the checkout
served the CSS and PNG with the correct content types, passed the health check
and preserved branded browser errors and JSON API errors.

No live authorization or publication was performed for this release.
