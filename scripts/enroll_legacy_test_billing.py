#!/usr/bin/env python3
"""Offline operator-only tool; see deploy/saas/billing-enrollment.md."""
if __name__ == '__main__':
    try:
        from socialctl.saas.billing_enrollment import main
        result = main()
    except BaseException:
        print('{"status":"failed","error":"Enrollment operation failed."}')
        result = 1
    raise SystemExit(result)
