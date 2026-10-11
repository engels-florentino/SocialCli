"""Bounded Graph diagnostics; HTTP 400 alone never identifies a permission."""
import json
import re
from socialctl.read_reports import safe_error


def graph_error(response, method, path, token):
    try:
        raw = bytearray()
        for chunk in response.iter_bytes(chunk_size=4096):
            raw.extend(chunk)
            if len(raw) > 65536:
                raise ValueError()
        error = json.loads(raw).get('error', {})
        if not isinstance(error, dict):
            error = {}
    except Exception:
        error = {}
    code, subcode = error.get('code'), error.get('error_subcode')
    detail = f'Meta HTTP {response.status_code} during {method} {path}'
    if type(code) is int:
        detail += f'; code={code}'
    if type(subcode) is int:
        detail += f'; subcode={subcode}'
    trace = error.get('fbtrace_id')
    if isinstance(trace, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,200}', trace):
        detail += f'; reference={trace}'
    message = error.get('message')
    if isinstance(message, str):
        detail += '; ' + safe_error(message, secrets=(token,))
    if code in {190, 459}:
        detail += '; Reconnect this account after resolving provider login/token checks.'
    elif code in {10, 200, 283}:
        detail += '; Check granted permissions, app eligibility and Page tasks; reconnect after consent changes.'
        if path.endswith('/likes') and method != 'GET':
            detail += ' Facebook likes require a Page token and pages_manage_engagement; object eligibility also applies.'
    elif code == 100:
        detail += '; Check the target ID, supported fields and operation; this code does not identify a missing permission.'
    elif code in {4, 17, 32, 613, 80001}:
        detail += '; Provider rate limit: wait before a new read; do not replay an uncertain write.'
    elif code == 368:
        detail += '; Provider disallowed the action; check object/account eligibility.'
    return safe_error(detail, secrets=(token,))
