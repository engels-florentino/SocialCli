"""Conservative snippet equality for post-write observation only."""
from collections import Counter


def snippet_mismatch_fields(current, expected):
    """Keep exact fields, except omitted clears and exact keyword multisets."""
    if not isinstance(current, dict) or not isinstance(expected, dict):
        return ['snippet']
    actual, wanted = dict(current), dict(expected)
    for field, empty in (("tags", []), ("description", "")):
        actual.setdefault(field, empty)
        wanted.setdefault(field, empty)
    left, right = actual['tags'], wanted['tags']
    valid_tags = (isinstance(left, list) and isinstance(right, list)
                  and all(isinstance(tag, str) for tag in [*left, *right]))
    if valid_tags:
        actual['tags'], wanted['tags'] = Counter(left), Counter(right)
    return [field for field in sorted(set(actual) | set(wanted))
            if (field == 'tags' and not valid_tags)
            or (field == 'description' and not all(isinstance(v, str) for v in (actual[field], wanted[field])))
            or field not in actual
            or field not in wanted or actual[field] != wanted[field]]


def same_snippet_readback(current, expected):
    return not snippet_mismatch_fields(current, expected)
