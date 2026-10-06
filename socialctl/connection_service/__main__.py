"""Run the separately configured connection service, never a publication worker."""
import logging
import os
import sys
import time


def main():
    import uvicorn
    from socialctl.connection_service.app import create_app
    from socialctl.connection_service.settings import Settings

    # Callback query strings and outbound OAuth payloads must not enter logs.
    for name in ('httpx', 'httpcore'):
        logging.getLogger(name).disabled = True
    settings = Settings.from_environment()
    if sys.argv[1:] == ['--prune']:
        from socialctl.connection_service.store import Store
        vault = Store(settings.database, settings.encryption_keys)
        with vault.transaction() as db:
            vault.purge(db, now=time.time())
        print('Expired connection records pruned.')
        return
    if sys.argv[1:]:
        raise SystemExit('Usage: python -m socialctl.connection_service [--prune]')
    app = create_app(settings)
    uvicorn.run(app, host=os.environ.get('SOCIALCLI_BIND', '127.0.0.1'),
                port=int(os.environ.get('SOCIALCLI_PORT', '8787')), workers=1,
                access_log=False, proxy_headers=False)


if __name__ == '__main__':
    main()
