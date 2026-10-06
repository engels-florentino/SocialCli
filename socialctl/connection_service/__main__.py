"""Run the separately configured connection service, never a publication worker."""
import logging
import os


def main():
    import uvicorn
    from socialctl.connection_service.app import create_app
    from socialctl.connection_service.settings import Settings

    # Callback query strings and outbound OAuth payloads must not enter logs.
    for name in ('httpx', 'httpcore'):
        logging.getLogger(name).disabled = True
    settings = Settings.from_environment()
    app = create_app(settings)
    uvicorn.run(app, host=os.environ.get('SOCIALCLI_BIND', '127.0.0.1'),
                port=int(os.environ.get('SOCIALCLI_PORT', '8787')), workers=1,
                access_log=False, proxy_headers=False)


if __name__ == '__main__':
    main()
