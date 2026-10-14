"""Immediate local revocation and durable, owner-bound external deletion jobs."""
import asyncio
import secrets
from html import escape
from fastapi import HTTPException,Request
from fastapi.responses import HTMLResponse,RedirectResponse
from socialctl.saas import pages, permissions
from socialctl.saas.auth import IdentityError,IdentityUnavailable
from socialctl.saas.routes import read_form
from socialctl.saas.budget import using_budget


class AccountLifecycle:
    def __init__(self,metadata,clock):
        self.metadata,self.vault,self.clock=metadata,metadata.vault,clock

    def owned_records(self,db,user_id):
        owned=[]
        rows=db.execute("SELECT kind,id FROM records WHERE kind LIKE 'saas-%' OR kind='authorization'").fetchall()
        for row in rows:
            record=self.vault.get(db,row['kind'],row['id']);data=record['payload']
            if data.get('user_id')==user_id or data.get('saas',{}).get('user_id')==user_id:
                owned.append((row['kind'],row['id'],record))
        return owned

    def revoke(self,db,user_id,installation_id):
        record=self.vault.get(db,'saas-installation',installation_id)
        if record is None or record['payload']['user_id']!=user_id:
            raise HTTPException(404,'Assistant unavailable.')
        self.vault.delete(db,'saas-installation',installation_id)
        for kind,record_id,entry in self.owned_records(db,user_id):
            data=entry['payload']
            if data.get('installation_id')==installation_id or data.get('saas',{}).get('installation_id')==installation_id:
                self.vault.delete(db,kind,record_id)

    def disconnect(self,db,user_id,platform):
        slot=db.execute('SELECT grant_id FROM saas_slots WHERE user_id=? AND platform=?',(user_id,platform)).fetchone()
        if not slot:raise HTTPException(404,'Connection unavailable.')
        for kind,record_id,entry in self.owned_records(db,user_id):
            if entry['payload'].get('grant_id')==slot['grant_id'] or kind=='saas-grant' and record_id==slot['grant_id']:
                self.vault.delete(db,kind,record_id)
        db.execute('DELETE FROM saas_slots WHERE user_id=? AND platform=?',(user_id,platform))

    def request_delete(self,user_id):
        with self.vault.transaction() as db:
            user=self.metadata.user(db,user_id)
            if user is None:raise HTTPException(404,'Account unavailable.')
            if user['deleting']:return
            identity=self.vault.get(db,'saas-user',user_id)
            email=identity['payload']['email'] if identity else None
            db.execute('UPDATE saas_users SET deleting=1,session_version=session_version+1 WHERE id=?',(user_id,))
            db.execute('INSERT OR IGNORE INTO saas_deletions(user_id,requested) VALUES(?,?)',(user_id,self.clock()))
            owned=self.owned_records(db,user_id)
            installations={record_id for kind,record_id,_ in owned if kind=='saas-installation'}
            for row in db.execute("SELECT id FROM records WHERE kind='saas-pair'").fetchall():
                pair=self.vault.get(db,'saas-pair',row['id'])
                if pair['payload'].get('installation_id') in installations:
                    self.vault.delete(db,'saas-pair',row['id'])
            for kind,record_id,_ in owned:
                self.vault.delete(db,kind,record_id)
            if email:
                for row in db.execute("SELECT id FROM records WHERE kind='saas-login'").fetchall():
                    attempt=self.vault.get(db,'saas-login',row['id'])
                    if attempt['payload'].get('email')==email:self.vault.delete(db,'saas-login',row['id'])
            self.vault.delete(db,'saas-user',user_id)
            db.execute('DELETE FROM saas_slots WHERE user_id=?',(user_id,))
            db.execute('DELETE FROM saas_brands WHERE user_id=?',(user_id,))
            # Keep only eligibility markers and minimal billing IDs until cleanup.
            db.execute('DELETE FROM saas_entitlements WHERE user_id=?',(user_id,))

    def prune_expired(self):
        with self.vault.transaction() as db:
            self.vault.purge(db,now=self.clock())
            for slot in db.execute('SELECT * FROM saas_slots').fetchall():
                if self.vault.get(db,'saas-grant',slot['grant_id']) is None:
                    db.execute('DELETE FROM saas_slots WHERE user_id=? AND platform=?',(slot['user_id'],slot['platform']))
            for row in db.execute("SELECT id FROM records WHERE kind='saas-capability'").fetchall():
                record=self.vault.get(db,'saas-capability',row['id']);data=record['payload']
                if (self.vault.get(db,'saas-grant',data['grant_id']) is None
                        or self.vault.get(db,'saas-installation',data['installation_id']) is None):
                    self.vault.delete(db,'saas-capability',row['id'])
            db.execute('DELETE FROM saas_trial_markers WHERE retain_until<=?',(self.clock(),))

    def panel(self,user_id,csrf):
        with self.vault.transaction() as db:
            records=self.owned_records(db,user_id)
        connections=[];installations=[]
        for kind,record_id,record in records:
            data=record['payload']
            if kind=='saas-grant':
                from socialctl.saas.renewal import notice
                label=escape(data['account'].get('name',data['account']['id']))
                platform=escape(data['platform'],quote=True)
                message=('This SocialCli connection expired after inactivity. Ask your assistant to help you connect again.'
                    if record['expires']<=self.clock() else notice(data,self.clock()))
                diagnostic=f'<p class="status-notice">{escape(message)}</p>' if message else ''
                connections.append(f'<li><strong>{label}</strong><p>{platform.title()} · {escape(data["account"]["id"])}</p>{diagnostic}<form method="post" action="/account/connections/{platform}/disconnect">{pages.hidden("csrf",csrf)}<button class="secondary-button" type="submit">Disconnect {platform.title()}</button></form></li>')
            elif kind=='saas-installation':
                label=escape(data['label']);rid=escape(record_id,quote=True)
                expired=' · Expired' if record['expires']<=self.clock() else ''
                installations.append(f'<li><strong>{label}{expired}</strong>{permissions.summary_html(data)}<form method="post" action="/account/installations/{rid}/revoke">{pages.hidden("csrf",csrf)}<button class="secondary-button" type="submit">Revoke assistant</button></form></li>')
        return '<section aria-label="Connected accounts"><h2>Connected accounts</h2>'+('<ul class="account-list">'+''.join(connections)+'</ul>' if connections else '<p class="description">No social accounts connected yet. Ask your assistant to connect your account.</p>')+'</section><section aria-label="Assistants"><h2>Assistants</h2>'+('<ul class="account-list">'+''.join(installations)+'</ul>' if installations else '<p class="description">No assistants linked yet.</p>')+'<p class="expiry-note">Selections control access to SocialCli connections. They do not narrow provider permissions, including permissions for linked accounts or individual actions. Previously issued provider tokens may remain usable until they expire or are revoked at the provider. To change an assistant’s selected platforms, revoke it and link it again.</p></section><a class="text-link" href="/account/delete">Delete my account</a>'


def run_deletions(metadata,identity,billing,clock,limit=100,*,budget=None):
    with using_budget(budget,identity,billing):
        return _run_deletions(metadata,identity,billing,clock,limit,budget)


def _run_deletions(metadata,identity,billing,clock,limit,budget):
    completed=0;pending=0;vault=metadata.vault
    with vault.transaction() as db:
        jobs=[dict(r) for r in db.execute("SELECT * FROM saas_deletions WHERE state='pending' AND next_attempt<=? AND lease_until<=? ORDER BY next_attempt,requested LIMIT ?",(clock(),clock(),limit))]
    for job in jobs:
        if budget and budget.expired():break
        user_id=job['user_id'];token=secrets.token_hex(20)
        with vault.transaction() as db:
            claimed=db.execute("UPDATE saas_deletions SET lease=?,lease_until=? WHERE user_id=? AND state='pending' AND lease_until<=? AND next_attempt<=?",(token,clock()+300,user_id,clock(),clock())).rowcount
            row=db.execute('SELECT * FROM saas_billing WHERE user_id=?',(user_id,)).fetchone()
            billing_row=dict(row) if row else None
        if not claimed:continue
        try:
            if not job['billing_done']:
                if billing_row and (billing_row['customer_id'] or billing_row['customer_started']):
                    if billing is None:raise IdentityUnavailable()
                    billing.delete_account_billing(user_id,billing_row)
                with vault.transaction() as db:
                    db.execute('UPDATE saas_deletions SET billing_done=1 WHERE user_id=? AND lease=?',(user_id,token))
            identity.delete_user(user_id)
            with vault.transaction() as db:
                current=db.execute('SELECT lease FROM saas_deletions WHERE user_id=?',(user_id,)).fetchone()
                if current is None or current['lease']!=token:continue
                current_billing=db.execute('SELECT * FROM saas_billing WHERE user_id=?',(user_id,)).fetchone()
                if current_billing and current_billing['customer_id']:
                    billing_row=dict(current_billing)
                    events=db.execute('SELECT account_id,mode,event_id FROM saas_billing_events WHERE customer_id=?',(billing_row['customer_id'],)).fetchall()
                    for event in events:vault.delete(db,'saas-stripe-event',':'.join(event))
                    db.execute('DELETE FROM saas_billing_events WHERE customer_id=?',(billing_row['customer_id'],))
                db.execute('DELETE FROM saas_billing WHERE user_id=?',(user_id,))
                db.execute('DELETE FROM saas_users WHERE id=?',(user_id,))
                db.execute("UPDATE saas_deletions SET state='complete',lease=NULL,lease_until=0 WHERE user_id=?",(user_id,))
                completed+=1
        except (HTTPException,IdentityError,IdentityUnavailable):
            with vault.transaction() as db:
                db.execute("UPDATE saas_deletions SET lease=NULL,lease_until=0,next_attempt=? WHERE user_id=? AND lease=?",(clock()+300,user_id,token))
            pending+=1
    with vault.transaction() as db:
        due=db.execute("SELECT count(*) FROM saas_deletions WHERE state='pending' AND next_attempt<=?",(clock(),)).fetchone()[0]
    return {'completed':completed,'pending':pending,'due':due,
            'budget_exhausted':bool(budget and budget.expired())}


def install_lifecycle_routes(app,settings,metadata,sessions,clock):
    lifecycle=AccountLifecycle(metadata,clock)
    app.state.saas_lifecycle=lifecycle
    async def verified(request,purpose='saas-account'):
        form=await read_form(request,settings.public_url)
        app.state.saas_valid_form(request,form,purpose)
        user=await asyncio.to_thread(sessions.require_user,request)
        return user,form

    @app.post('/account/installations/{installation_id}/revoke')
    async def revoke(installation_id:str,request:Request):
        user,_=await verified(request)
        def commit():
            with metadata.vault.transaction() as db:lifecycle.revoke(db,user.id,installation_id)
        await asyncio.to_thread(commit)
        return RedirectResponse('/account/',status_code=303)

    @app.post('/account/connections/{platform}/disconnect')
    async def disconnect(platform:str,request:Request):
        user,_=await verified(request)
        def commit():
            with metadata.vault.transaction() as db:lifecycle.disconnect(db,user.id,platform)
        await asyncio.to_thread(commit)
        return RedirectResponse('/account/',status_code=303)

    @app.get('/account/delete',response_class=HTMLResponse)
    def delete_page(request:Request):
        sessions.require_user(request)
        return HTMLResponse(pages.page('Delete your account',f'''<p class="eyebrow">ACCOUNT SETTINGS</p><h1 id="page-title">Delete your account?</h1>
<p class="description">This immediately stops your assistants from using SocialCli and removes your saved social credentials. We will cancel future subscription billing and delete your sign-in account. Cleanup retries automatically if a service is temporarily unavailable.</p>
<p class="status-notice">Published posts stay on your social networks. Remove local SocialCli credentials from your assistants separately. Previously delivered provider tokens keep their own expiry. Required payment records stay with Stripe; a minimal free-trial eligibility marker is retained for 365 days.</p>
<form method="post" action="/account/delete" class="form-stack">{pages.hidden('csrf',app.state.saas_csrf(request,'saas-delete'))}<label for="confirmation">Type DELETE to confirm</label><input id="confirmation" name="confirmation" autocomplete="off" required pattern="DELETE"><button class="secondary-button" type="submit">Delete my account</button></form><a class="text-link" href="/account/">Keep my account</a>'''))

    @app.post('/account/delete',response_class=HTMLResponse)
    async def delete_account(request:Request):
        user,form=await verified(request,'saas-delete')
        if form.get('confirmation')!='DELETE':raise HTTPException(400,'Type DELETE to confirm account deletion.')
        await asyncio.to_thread(lifecycle.request_delete,user.id)
        response=HTMLResponse(pages.page('Deletion requested','<h1 id="page-title">Deletion requested</h1><p class="description">Your assistants no longer have access through SocialCli. Subscription and sign-in cleanup will finish automatically. Remove the local credentials saved by your assistants.</p>'))
        response.delete_cookie(sessions.cookie,path='/',secure=not settings.allow_http_local,httponly=True,samesite='lax')
        return response
