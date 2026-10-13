"""Local entitlement decisions; no clock or payment status comes from the CLI."""
import hashlib
import hmac
from dataclasses import dataclass

TRIAL_SECONDS=14*86400
MARKER_RETENTION_SECONDS=365*86400


@dataclass(frozen=True)
class AccessDecision:
    allowed:bool
    reason:str
    access_until:float|None=None


class AccessControl:
    def __init__(self,metadata):
        self.metadata,self.vault=metadata,metadata.vault

    def previous_trial(self,db,email,now):
        markers=[hmac.new(key,b'socialcli-trial-v1\0'+email.casefold().encode(),hashlib.sha256).hexdigest() for key in self.metadata.eligibility_keys]
        original=None
        for marker in markers:
            row=db.execute('SELECT * FROM saas_trial_markers WHERE marker=? AND retain_until>?',(marker,now)).fetchone()
            if row and (original is None or row['trial_start']<original['trial_start']):
                original=row
        return markers,original

    def restore_trial(self,db,user_id,email,now):
        """Restore eligibility on verified login without starting a new trial."""
        _,original=self.previous_trial(db,email,now)
        if original is None:
            return
        db.execute('INSERT OR IGNORE INTO saas_entitlements(user_id) VALUES(?)',(user_id,))
        db.execute('UPDATE saas_entitlements SET trial_start=?,trial_end=? WHERE user_id=? AND trial_start IS NULL',
                   (original['trial_start'],original['trial_end'],user_id))

    def activate_trial(self,db,user_id,now):
        user=self.metadata.user(db,user_id)
        if user is None or user['deleting'] or user['suspended']:
            raise ValueError('Account is unavailable for activation.')
        current=db.execute('SELECT * FROM saas_entitlements WHERE user_id=?',(user_id,)).fetchone()
        if current and current['trial_start'] is not None:
            return current['trial_start'],current['trial_end']
        identity=self.vault.get(db,'saas-user',user_id)
        if identity is None:
            raise ValueError('Verified identity is required for activation.')
        markers,original=self.previous_trial(db,identity['payload']['email'],now)
        start,end=(original['trial_start'],original['trial_end']) if original else (now,now+TRIAL_SECONDS)
        retain=max(now+MARKER_RETENTION_SECONDS,original['retain_until'] if original else 0)
        for marker in markers:
            db.execute('INSERT OR REPLACE INTO saas_trial_markers VALUES(?,?,?,?)',(marker,start,end,retain))
        db.execute('INSERT OR IGNORE INTO saas_entitlements(user_id) VALUES(?)',(user_id,))
        db.execute('UPDATE saas_entitlements SET trial_start=?,trial_end=? WHERE user_id=?',(start,end,user_id))
        return start,end

    def decision(self,db,user_id,now):
        user=self.metadata.user(db,user_id)
        if user is None or user['deleting']:
            return AccessDecision(False,'account_unavailable')
        if user['suspended']:
            return AccessDecision(False,'suspended')
        row=db.execute('SELECT * FROM saas_entitlements WHERE user_id=?',(user_id,)).fetchone()
        if row is None or row['trial_start'] is None:
            return AccessDecision(False,'trial_not_started')
        if row['subscription_status'] in {'active','canceled'} and row['paid_until']>now:
            return AccessDecision(True,'paid',row['paid_until'])
        if row['trial_end']>now:
            return AccessDecision(True,'trial',row['trial_end'])
        return AccessDecision(False,'trial_expired',row['trial_end'])

    def access_for(self,user_id,now):
        with self.vault.transaction() as db:
            return self.decision(db,user_id,now)
