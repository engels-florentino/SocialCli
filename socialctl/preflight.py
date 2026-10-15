"""Read-only local checks reused by doctor, without credential repair or media edits."""
from pathlib import Path

from socialctl.models import Platform


def credential_status(brand):
    from socialctl.connections.credential_store import configuration_path, store_for
    from socialctl.connections.file_store import EncryptedFileStore, protected_parent
    result = {'backend': 'not_required', 'available': True, 'failure_class': None}
    try:
        if not configuration_path(brand).exists() and not any(
            brand.leer_secreto(p).get('auth_mode') == 'broker' for p in Platform
        ):
            return result
        store = store_for(brand)
        result['backend'] = 'encrypted-file' if isinstance(store, EncryptedFileStore) else 'keyring'
        if isinstance(store, EncryptedFileStore):
            cipher = store.cipher()
            # No lock-file creation, claim, migration, rotation or credential renewal.
            with protected_parent(store.path) as (parent, name):
                store.bind_data(store._load(parent, name, cipher), brand)
        else:
            store.ensure_available()
    except (ValueError, OSError):
        result.update(available=False, failure_class='credential_store_unavailable',
            action='Check the selected backend, external key format, owner-only directory permissions and mode 0600 credential files. No repair was attempted.')
    return result


def supplied_media_status(paths):
    from socialctl.media import leer_media, MediaInvalida, MediaNoEncontrada
    rows = []
    for path in paths:
        row = {'path': str(path), 'valid': False, 'failure_class': None}
        try:
            asset = leer_media(Path(path))
            row.update(valid=True, asset=asset.model_dump(mode='json'))
        except MediaInvalida as exc:
            row['failure_class'] = exc.code
        except MediaNoEncontrada:
            row['failure_class'] = 'media_missing'
        except (ValueError, OSError):
            row['failure_class'] = 'media_unreadable'
        rows.append(row)
    return rows


def post_status(brand, slug):
    from socialctl.media_preflight import check_post
    if slug is None:
        return None
    try:
        result = check_post(brand, slug)
        return {**result, 'valid': result['status'] == 'passed',
            'failure_class': None if result['status'] == 'passed' else 'post_invalid'}
    except Exception:
        return {'slug': slug, 'valid': False, 'failure_class': 'post_invalid',
            'action': 'Run media check SLUG --json for detailed supplied-media validation.'}


def scope_requirements(statuses, required):
    """Compare exact scopes actually recorded as granted, never requested scopes."""
    rows = []
    for status in statuses:
        observed = status.get('granted_scopes') or {}
        known = observed.get('state') == 'known'
        missing = sorted(set(required) - set(observed.get('values') or [])) if known else None
        rows.append({'platform': status['platform'], 'required': sorted(set(required)),
            'source': observed.get('source'), 'state': 'known' if known else 'unknown',
            'missing': missing, 'satisfied': not missing if known else not required,
            'write_permissions_verified': False})
    return rows
