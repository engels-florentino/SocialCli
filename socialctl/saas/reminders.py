"""Leased trial-email outbox. SMTP uncertainty is terminal, never auto-replayed."""
import secrets
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formatdate
from html import escape
from importlib.resources import files
from string import Template

from socialctl.saas.access import AccessControl
from socialctl.saas.pages import utc_date
from socialctl.saas.mail import mailbox

DAY=86400
LEASE_SECONDS=300


def message_for(job,recipient,public_url,now):
    mailbox(recipient)
    deadline=utc_date(job['trial_end'])
    link=public_url+'/account/'
    message=EmailMessage(policy=SMTP)
    message['From']='SocialCli <access@socialcli.app>'
    message['To']=recipient
    message['Subject']='Your SocialCli free trial ends soon'
    message['Date']=formatdate(now,usegmt=True)
    message['Message-ID']=f"<socialcli.trial.{job['id']}@socialcli.app>"
    message.set_content(f'Your SocialCli free trial ends {deadline}.\n\n'
        'There is no automatic charge. After your trial, subscribe for USD 10/month '
        'to continue using your connected accounts. Cancel anytime.\n\n'
        f'Manage your account: {link}\n\nYour accounts. Your permissions. Your choice.\n')
    template=files('socialctl.ui').joinpath('email/trial.html').read_text()
    message.add_alternative(Template(template).substitute(deadline=escape(deadline),account_url=escape(link,quote=True)),subtype='html')
    return message


class Reminders:
    def __init__(self,metadata,clock,public_url):
        self.metadata,self.vault,self.clock,self.public_url=metadata,metadata.vault,clock,public_url
        self.access=AccessControl(metadata)

    def eligible(self,db,job,now):
        entitlement=db.execute('SELECT trial_end FROM saas_entitlements WHERE user_id=?',(job['user_id'],)).fetchone()
        if (entitlement is None or entitlement['trial_end']!=job['trial_end']
                or self.access.decision(db,job['user_id'],now).reason!='trial'):
            return False
        end=job['trial_end']
        return end-job['milestone']*DAY<=now<(end-DAY if job['milestone']==3 else end)

    def prepare(self):
        now=self.clock()
        with self.vault.transaction() as db:
            # A crashed worker before DATA can be reclaimed; after DATA starts it cannot.
            db.execute("UPDATE saas_reminders SET state='pending',lease=NULL WHERE state='preparing' AND lease_until<=?",(now,))
            db.execute("UPDATE saas_reminders SET state='uncertain' WHERE state='sending' AND lease_until<=?",(now,))
            rows=db.execute("""SELECT e.user_id,e.trial_end FROM saas_entitlements e
                JOIN saas_users u ON u.id=e.user_id WHERE e.trial_end>? AND u.deleting=0 AND u.suspended=0
                AND (SELECT count(*) FROM saas_reminders r WHERE r.user_id=e.user_id AND r.trial_end=e.trial_end)<2
                ORDER BY e.trial_end LIMIT 100""",(now,)).fetchall()
            for row in rows:
                for milestone in (3,1):
                    db.execute('INSERT OR IGNORE INTO saas_reminders(id,user_id,trial_end,milestone,next_attempt) VALUES(?,?,?,?,?)',
                        (secrets.token_hex(24),row['user_id'],row['trial_end'],milestone,row['trial_end']-milestone*DAY))
            db.execute("UPDATE saas_reminders SET state='cancelled',lease=NULL WHERE state IN ('pending','preparing') AND (trial_end<=? OR (milestone=3 AND trial_end-86400<=?))",(now,now))
            # Retain minimal delivery receipts only briefly after the trial. No addresses.
            db.execute('DELETE FROM saas_reminders WHERE trial_end<?',(now-30*DAY,))

    def run(self,sender,limit=100):
        self.prepare()
        accepted=0
        with self.vault.transaction() as db:
            jobs=([dict(row) for row in db.execute("SELECT * FROM saas_reminders WHERE state='pending' AND next_attempt<=? ORDER BY next_attempt,id LIMIT ?",(self.clock(),limit))]
                if sender is not None else [])
        for job in jobs:
            lease=secrets.token_hex(24)
            with self.vault.transaction() as db:
                claimed=db.execute("UPDATE saas_reminders SET state='preparing',attempts=attempts+1,lease=?,lease_until=? WHERE id=? AND state='pending' AND next_attempt<=?",(lease,self.clock()+LEASE_SECONDS,job['id'],self.clock())).rowcount
                if not claimed:continue
                identity=self.vault.get(db,'saas-user',job['user_id'])
                if identity is None or not self.eligible(db,job,self.clock()):
                    db.execute("UPDATE saas_reminders SET state='cancelled',lease=NULL WHERE id=?",(job['id'],))
                    continue
                recipient=identity['payload']['email']

            def before_send():
                with self.vault.transaction() as db:
                    row=db.execute('SELECT * FROM saas_reminders WHERE id=?',(job['id'],)).fetchone()
                    if row is None or row['lease']!=lease or row['state']!='preparing' or row['lease_until']<=self.clock():return False
                    identity=self.vault.get(db,'saas-user',job['user_id'])
                    if not self.eligible(db,job,self.clock()) or identity is None or identity['payload']['email']!=recipient:
                        db.execute("UPDATE saas_reminders SET state='cancelled',lease=NULL WHERE id=?",(job['id'],))
                        return False
                    db.execute("UPDATE saas_reminders SET state='sending',lease_until=? WHERE id=?",(self.clock()+LEASE_SECONDS,job['id']))
                    return True

            try:
                result=sender.send(recipient,message_for(job,recipient,self.public_url,self.clock()),before_send)
            except ValueError:
                result='rejected'
            except Exception:
                result='error'
            with self.vault.transaction() as db:
                row=db.execute('SELECT * FROM saas_reminders WHERE id=?',(job['id'],)).fetchone()
                if row is None or row['lease']!=lease:continue
                if row['state']=='cancelled':continue
                if result=='accepted' and row['state'] in {'sending','uncertain'}:
                    state='accepted';accepted+=1
                elif result=='rejected':state='rejected'
                elif result=='retry':state='pending'
                elif row['state'] in {'sending','uncertain'}:state='uncertain'
                else:state='pending'
                db.execute('UPDATE saas_reminders SET state=?,next_attempt=?,lease=NULL,lease_until=0 WHERE id=?',
                    (state,self.clock()+300,job['id']))
        with self.vault.transaction() as db:
            counts={row['state']:row['n'] for row in db.execute("SELECT state,count(*) AS n FROM saas_reminders WHERE (state='pending' AND next_attempt<=?) OR state IN ('uncertain','rejected','preparing','sending') GROUP BY state",(self.clock(),))}
            retry_pending=db.execute("SELECT count(*) FROM saas_reminders WHERE state='pending' AND attempts>0").fetchone()[0]
        return {'disabled':sender is None,'accepted':accepted,'retry_pending':retry_pending,'due':counts.get('pending',0),'uncertain':counts.get('uncertain',0),
                'rejected':counts.get('rejected',0),'in_progress':counts.get('preparing',0)+counts.get('sending',0)}
