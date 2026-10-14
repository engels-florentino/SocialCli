# Whole-branch review and fixes

Reviewed baseline `ad89ad4` through `9f158d9`, followed by a read-only review of the
fixes below. No critical issues were found; four important findings were reproduced
with fictional accounts and corrected before candidate rollout.

| Finding | Correction and regression evidence |
| --- | --- |
| Account forms failed after the auxiliary login cookie expired at 30 minutes | Authenticated CSRF binds to the actual session cookie; login and OTP still use the separate browser cookie. Tests expire/remove the auxiliary cookie and successfully submit logout. |
| Logout/login left old installation capabilities blocking attachment | `account attach` verifies the existing same-service SaaS capability and replaces it only after a specific revoked/expired installation response, with the same account ID. Keyring and encrypted-file flows pass; active, timed-out and unrecognized responses preserve local credentials. |
| Another platform could change accounts.yml before a pending rollback restored its snapshot | The brand mutation lock rejects all conflicting commands while any platform recovery is pending. Recovery remains available and locked; interrupted activation tests verify other-platform connect/attach and logout are blocked. |
| Cleanup removed the encrypted recovery journal before its pointer | Successful and rejected activation paths unlink and sync the pointer directory before removing the journal. An injected unlink failure remains recoverable on the next command. |

Every reproduced failure was observed before its fix. The follow-up reviewer found
no remaining Critical, Important or Minor findings in the fixes. The full suite
passed **2,416 tests**, with one existing upstream Starlette deprecation warning;
UI generation and whitespace checks passed. This is code-review evidence, not a
public launch or provider approval.

The reviewer did not certify external DNS/TLS/NPM configuration, live webhook
receipt, actual provider approval, hosted branding/email delivery, Google Drive
recovery, release-environment protections or real Muse/Grok installation. Visual
acceptance and commercial policies remain their separate gates. Google production
and real billing remain explicitly deferred. Existing unchanged publication/provider
code was outside this branch's full behavioral audit.

An independent [offline proxy rehearsal](proxy-rehearsal.md) additionally verified
both hostnames' normalized creation-path guard against the old and candidate
images. The real public proxy cutover has not occurred.
