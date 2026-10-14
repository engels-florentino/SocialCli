"""Account-bound calendar evidence. Absence never releases previous reservations."""
from datetime import datetime, time, timedelta
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from .approval import reject_secrets
from .cadence import ZONE
from .models import aware

ACCOUNT_KEYS = {'youtube':'channel_id','facebook':'page_id','instagram':'ig_user_id','tiktok':'open_id'}
INVENTORY_MAX_AGE = timedelta(hours=24)


def date(value):
    return aware(datetime.fromisoformat(value.replace('Z', '+00:00')))


def validate_inventory(inventory, *, accounts=None, now=None):
    reject_secrets(inventory)
    if set(inventory) != {'observed_at','accounts','objects','coverage_start','coverage_end'}:
        raise ValueError('Invalid calendar inventory')
    observed = date(inventory['observed_at'])
    if now is not None and not timedelta(0) <= aware(now)-observed <= INVENTORY_MAX_AGE:
        raise ValueError('Fresh complete read-only inventory required (maximum age 24 hours)')
    if date(inventory['coverage_start']) >= date(inventory['coverage_end']):
        raise ValueError('Invalid inventory coverage')
    occupied = {}
    for item in inventory['accounts']:
        if set(item) != {'platform','account_id','complete'} or item['complete'] is not True:
            raise ValueError('Incomplete native calendar inventory')
        p, a = item['platform'], item['account_id']
        if (p not in ACCOUNT_KEYS or not isinstance(a,str) or not a.strip()
                or (p,a) in occupied or (accounts is not None and a != (accounts.get(p) or {}).get(ACCOUNT_KEYS[p]))):
            raise ValueError('Inventory account mismatch or duplicate')
        occupied[p,a] = set()
    for item in inventory['objects']:
        if not {'platform','account_id','remote_ref','publish_at'} <= set(item) or set(item)-{'platform','account_id','remote_ref','publish_at','occurrence_id'}:
            raise ValueError('Invalid remote inventory object')
        key = item['platform'], item['account_id']
        if key not in occupied or not isinstance(item['remote_ref'],str) or not item['remote_ref'].strip():
            raise ValueError('Remote object lacks verified account/reference')
        occupied[key].add(date(item['publish_at']).astimezone(ZONE).date().isoformat())
    return occupied


def require_coverage(inventory, publish_at, timezone_name='America/New_York'):
    zone = ZoneInfo(timezone_name)
    day = publish_at.astimezone(zone).date()
    if not (date(inventory['coverage_start']) <= datetime.combine(day,time.min,zone)
            and datetime.combine(day+timedelta(days=1),time.min,zone) <= date(inventory['coverage_end'])):
        raise ValueError('Full local publication day must be inside observed inventory coverage')


def reschedule_inventory(root, target, *, now):
    """Read fresh, account-bound evidence before an exact-time calendar change."""
    root = Path(root).resolve()
    path = (root / 'native-calendar.json').resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError('Fresh native-calendar.json required before rescheduling')
    inventory = json.loads(path.read_text())
    calendars = validate_inventory(inventory, now=now)
    if (target.platform, target.account_id) not in calendars:
        raise ValueError('Complete destination account calendar required before rescheduling')
    require_coverage(inventory,target.publish_at,target.timezone_name)
    for item in inventory['objects']:
        if (item['platform'] == target.platform and item['account_id'] == target.account_id
                and date(item['publish_at']) == target.publish_at):
            raise ValueError('Destination time is occupied in the current external calendar')
    return inventory
