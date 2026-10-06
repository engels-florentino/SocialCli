# Shared Account Connection Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Allow creators to authorize their own accounts from SocialCli without provider developer registration or copied tokens.

**Architecture:** A dedicated connection service holds provider application secrets and encrypted refresh credentials. The CLI pairs through a browser, explicitly binds a discovered account and stores only a connection credential in an OS keyring. Existing platform adapters receive access tokens in memory.

**Tech Stack:** Python 3.11+, FastAPI, httpx, SQLite, cryptography Fernet and keyring; optional server dependencies.

**Spec:** docs/superpowers/specs/2026-10-06-shared-account-connect-design.md

## Global Constraints

- Product and documentation are English.
- Never infer the brand or replace a different account without explicit confirmation.
- Never publish without a complete dry-run preview and explicit approval.
- Never generate, crop or reencode media.
- Never commit secrets, creator state or private executor data.
- Ten-minute, single-use browser authorization sessions; fixed HTTPS callback origin.
- Keep shared mode separate from existing independent-app authentication.

## Review Focus

- Cross-creator connection or account substitution must fail.
- Callback replay, browser-cookie mismatch and consumed/expired pairing must fail.
- Concurrent refresh must persist the newest rotated credential without duplicate exchanges.
- Transport/provider errors must not leak secrets or silently change accounts.
- Keyring/storage failure must not leave a new connection active or destroy previous credentials.

### Task 1: Service configuration, encrypted vault and pairing state

**Files:** socialctl/connection_service/{settings,store}.py; tests/test_connection_store.py; pyproject.toml.
**Interfaces:** Settings provides validated public origin and platform credentials. Store provides transactional authorization/connection records, encrypted JSON payloads and hashed credentials.
- [ ] Write tests for encrypted disk contents, expiration, single-use state, unauthorized polling, isolated connection credentials and concurrent transactions; run RED.
- [ ] Implement bounded SQLite storage and configuration with Fernet encryption, private permissions and environment-only provider secrets.
- [ ] Run tests GREEN and commit.

### Task 2: Provider connectors and service HTTP API

**Files:** socialctl/connection_service/{providers,app,__main__}.py; tests/test_connection_service.py; tests/test_connection_providers.py.
**Interfaces:** Provider authorization_url, exchange/discover and refresh; create_app(settings, store, provider_client) exposes /v1/authorizations, browser start/callback, poll/complete, connection status/token/delete and /healthz.
- [ ] Write protocol tests for Google consent, discovered account selection, cancellation, missing scopes, callback/cookie replay, isolation and sanitized errors; run RED.
- [ ] Implement Google first, then Meta/TikTok using fixed official endpoints and least-privilege profiles.
- [ ] Verify Google/TikTok refresh rotation, concurrent refresh, Meta reauthorization and disconnect behavior; run GREEN and commit.

### Task 3: CLI connect, status, disconnect and existing adapter bridge

**Files:** socialctl/connections/{client,keychain,cli}.py; socialctl/{cli,auth,brands,identity,inventory}.py and targeted existing token readers; tests/test_connections_cli.py; tests/test_connected_auth.py.
**Interfaces:** register(app) adds connect/connections/disconnect; broker_access_token(brand, platform, client) returns transient token; keychain indexes credentials by resolved workspace/platform/connection.
- [ ] Write tests for complete browser pairing, explicit account confirmation, keyring failure cleanup, no provider secrets on disk, HTTPS-only service and legacy compatibility; run RED.
- [ ] Implement secure OS keyring storage, bounded polling and versioned connection metadata; wire existing token consumers without weakening preview/approval controls.
- [ ] Run targeted and full test suites, build/install smoke tests and commit.

### Task 4: Deployment, English documentation and provider configuration

**Files:** deploy/connection-service/*; docs/{connections.md,privacy.html,terms.html,index.html}; README.md; SETUP.md; docs/tiktok-review.md.
- [ ] Document exact service/CLI setup, data handling, revocation limits and independent-app compatibility.
- [ ] Create an isolated server release/environment/state and container service with no published host port and query-free logs. Back up reverse-proxy/web configuration before adding routes.
- [ ] Deploy to social.florentino.pro, preserve media/private executor behavior, verify external health and TLS, register available provider callbacks and test browser authorization read-only.
- [ ] Run all tests and secret scans, obtain fresh whole-branch code review, fix material findings, push reviewed public changes and verify CI.
- [ ] Record any provider approval or credential-dependent blockers. Do not claim a completed creator pilot without a real authorization, nor send content without its approved preview.
