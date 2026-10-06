FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SOCIALCLI_BIND=0.0.0.0
WORKDIR /app
COPY dist/*.whl /tmp/wheels/
RUN python -m pip install --no-cache-dir '/tmp/wheels/socialcli-0.2.0-py3-none-any.whl[connection-server]' && rm -rf /tmp/wheels
USER 1000:1000
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request; urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8787/healthz', headers={'Host':__import__('urllib.parse', fromlist=['urlsplit']).urlsplit(__import__('os').environ['SOCIALCLI_PUBLIC_URL']).netloc}), timeout=3)" || exit 1
CMD ["python", "-m", "socialctl.connection_service"]
