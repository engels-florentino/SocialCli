"""Read-only pagination helpers; remote links never become request URLs."""
from urllib.parse import parse_qs, urlsplit


def next_time_params(next_url, endpoint, params):
    if not isinstance(next_url, str) or len(next_url) > 16384:
        return None
    try:
        parsed, expected = urlsplit(next_url), urlsplit(endpoint)
        if (parsed.scheme != 'https' or parsed.netloc != expected.netloc
                or parsed.path != expected.path or parsed.fragment):
            return None
        query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=30)
        if any(len(values) != 1 for values in query.values()):
            return None
        for key in query.keys() - {'access_token', 'since', 'until'}:
            if key not in params or query[key][0] != str(params[key]):
                return None
        dates = {key: query[key][0] for key in ('since', 'until')}
        if any(not value.isascii() or not value.isdigit() or len(value) > 12 for value in dates.values()):
            return None
        if int(dates['since']) >= int(dates['until']):
            return None
        if all(str(params.get(key)) == value for key, value in dates.items()):
            return None
        return {**params, **dates}
    except (ValueError, KeyError):
        return None


def merge_metric(previous, incoming):
    """Preserve exact values; never add counts or replace conflicting evidence."""
    if any(previous.get(key) != incoming.get(key) for key in ('name', 'period')):
        return None
    if 'id' in previous and 'id' in incoming and previous['id'] != incoming['id']:
        return None
    old, new = previous.get('values'), incoming.get('values')
    if not isinstance(old, list) or not isinstance(new, list):
        return None
    merged = list(old)
    for value in new:
        if not isinstance(value, dict):
            return None
        if value in merged:
            continue
        stamp = value.get('end_time')
        if not isinstance(stamp, str) or not stamp or any(
                not isinstance(v, dict) or not v.get('end_time') or v['end_time'] == stamp
                for v in merged):
            return None
        merged.append(value)
    return {**previous, 'values': merged}
