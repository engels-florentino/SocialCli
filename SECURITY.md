# Security

Never publish `.env`, `.secrets/`, OAuth tokens, client secrets, SSH credentials or private account data. `.gitignore` protects these names in this repository; it does not remove secrets from an existing Git history.

Each brand stores credentials locally with restricted permissions (0700 directories, 0600 files). These are plain text files, not an encrypted vault: protect your system account, disk and backups. API requests send necessary data to their providers.

The public code contains no shared application credentials. Each operator configures their own application. A future shared SocialCli authorization flow would require a design that keeps the secret in a controlled service outside the distributed CLI.

To report a vulnerability, use **Security → Report a vulnerability** in the repository. If this channel is unavailable, contact the maintainer through [their profile](https://github.com/engels-florentino) before publishing exploitable details. Do not open a public issue containing secrets or evidence that exposes accounts.

If you suspect a leak, revoke and rotate the credential with its provider; deleting a file or commit does not invalidate it.
