#!/usr/bin/env python3
"""Offline read-only inventory; see deploy/saas/billing-inventory.md."""
if __name__ == '__main__':
    try:
        from socialctl.saas.billing_inventory import main
        result = main()
    except Exception:
        print('{"status": "failed", "error": "Inventory inspection failed."}')
        result = 1
    raise SystemExit(result)
