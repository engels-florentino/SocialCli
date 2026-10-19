#!/usr/bin/env python3
"""Offline operator-only tool; see deploy/saas/billing-enrollment.md."""
if __name__ == '__main__':
    try:
        from socialctl.saas.billing_enrollment import main
        result = main()
    except BaseException:
        print('{"status":"failed","error":"Enrollment operation failed.",'
              '"diagnostic":{"stage":"cli_bootstrap","error_code":"bootstrap_failed"}}')
        result = 1
    raise SystemExit(result)
