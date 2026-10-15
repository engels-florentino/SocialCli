"""Immediate local revocation and durable, owner-bound external deletion jobs."""
import asyncio
import secrets
from html import escape
from fastapi import HTTPException,Request
from fastapi.responses import HTMLResponse,RedirectResponse
from socialctl.saas import pages, permissions
from socialctl.saas.auth import IdentityError,IdentityUnavailable
from socialctl.saas.routes import read_form
from socialctl.saas.budget import WorkBudget,using_budget
from socialctl.saas import revocation


class AccountLifecycle:
    def __init__(self,metadata,clock):
        self.metadata,self.vault,self.clock=metadata,metadata.vault,clock

    def owned_records(self,db,user_id,*,kinds=None):
        owned=[]
        query='SELECT kind,id FROM saas_record_owners WHERE user_id=?'
        params=[user_id]
        if kinds:
            query+=' AND kind IN ('+','.join('?' for _ in kinds)+')'
            params.extend(kinds)
        rows=db.execute(query,params).fetchall()
        for row in rows:
            record=self.vault.get(db,row['kind'],row['id'])
            if record is None:continue
            data=record['payload']
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
        return self.disconnect_grant(db,user_id,platform,slot['grant_id'])

    def disconnect_grant(self,db,user_id,platform,grant_id):
        record=self.vault.get(db,'saas-grant',grant_id)
        job=None
        if record:
            if record['payload'].get('user_id')!=user_id:
                raise HTTPException(404,'Connection unavailable.')
            job=revocation.enqueue(self.metadata,db,grant_id,record['payload'],self.clock())
            if platform=='youtube' and record['payload'].get('refresh_lease'):
                revocation.reserve(db,grant_id+':refresh:'+record['payload']['refresh_lease'],user_id,self.clock())
        else:
            revocation.tombstone(db,'grant',grant_id,self.clock())
        current=db.execute('SELECT grant_id FROM saas_slots WHERE user_id=? AND platform=?',(user_id,platform)).fetchone()
        for kind,record_id,entry in self.owned_records(db,user_id):
            data=entry['payload']
            # A pre-disconnect OAuth result cannot reinstall an authorization we
            # just revoked. A stale capability must not cancel a newer connection.
            pending=(current is not None and current['grant_id']==grant_id and
                platform=='youtube' and data.get('platform')=='youtube' and
                kind in {'saas-candidate','authorization'})
            if pending:
                # Fence a provider exchange that has started but has not returned
                # a credential yet. Its late result still needs revocation.
                if kind=='authorization' and data.get('status')=='exchanging':
                    revocation.reserve(db,record_id,user_id,self.clock())
                payload={**data,'user_id':user_id}
                if kind=='authorization':payload['grant']=data.get('result',{}).get('grant')
                if payload.get('grant'):
                    revocation.enqueue(self.metadata,db,record_id,payload,self.clock())
            if pending or data.get('grant_id')==grant_id or kind=='saas-grant' and record_id==grant_id:
                self.vault.delete(db,kind,record_id)
        db.execute('DELETE FROM saas_slots WHERE user_id=? AND platform=? AND grant_id=?',(user_id,platform,grant_id))
        self.release_empty_brand(db,user_id)
        return job

    def release_empty_brand(self,db,user_id):
        db.execute('DELETE FROM saas_brands WHERE user_id=? AND NOT EXISTS (SELECT 1 FROM saas_slots WHERE user_id=?)',(user_id,user_id))

    def rename_brand(self,db,user_id,label):
        label=label.strip()
        if not label or len(label)>80 or any(ord(c)<32 or ord(c)==127 for c in label):
            raise HTTPException(400,'Enter a brand name of 1–80 characters without control characters.')
        if not db.execute('UPDATE saas_brands SET label=? WHERE user_id=?',(label,user_id)).rowcount:
            raise HTTPException(404,'Connect a social account before renaming your brand.')
        # Pending authorizations retain their reviewed label and cannot silently
        # activate under the new name. Existing capabilities use stable grant IDs.

    def request_delete(self,user_id):
        with self.vault.transaction() as db:
            user=self.metadata.user(db,user_id)
            if user is None:raise HTTPException(404,'Account unavailable.')
            if user['deleting']:return
            revocation.tombstone(db,'user',user_id,self.clock())
            for kind,record_id,entry in self.owned_records(db,user_id,kinds=('saas-grant','saas-candidate','authorization')):
                payload=entry['payload']
                if payload.get('platform')=='youtube':
                    if kind=='authorization' and payload.get('status')=='exchanging':
                        revocation.reserve(db,record_id,user_id,self.clock())
                    if kind=='saas-grant' and payload.get('refresh_lease'):
                        revocation.reserve(db,record_id+':refresh:'+payload['refresh_lease'],user_id,self.clock())
                if kind=='authorization':
                    payload={**payload, 'user_id':user_id, 'grant':payload.get('result',{}).get('grant')}
                if payload.get('grant'):
                    revocation.enqueue(self.metadata,db,record_id,payload,self.clock())
            db.execute('UPDATE saas_users SET deleting=1,session_version=session_version+1 WHERE id=?',(user_id,))
            db.execute('INSERT OR IGNORE INTO saas_deletions(user_id,requested) VALUES(?,?)',(user_id,self.clock()))
            # Historical approved pairs may lack user_id. Their installation link
            # proves ownership; unclaimed pairs have no such link and are preserved.
            db.execute("""DELETE FROM records WHERE kind='saas-pair' AND id IN (
                SELECT link.id FROM saas_record_links link JOIN saas_record_owners owner
                ON owner.kind='saas-installation' AND owner.id=link.installation_id
                WHERE link.kind='saas-pair' AND owner.user_id=?)""",(user_id,))
            db.execute("""DELETE FROM records WHERE kind='saas-login' AND id IN (
                SELECT attempt.id FROM saas_record_emails attempt JOIN saas_record_emails identity
                ON identity.email_hash=attempt.email_hash
                WHERE attempt.kind='saas-login' AND identity.kind='saas-user' AND identity.id=?)""",(user_id,))
            db.execute("""DELETE FROM records WHERE (kind,id) IN (
                SELECT kind,id FROM saas_record_owners WHERE user_id=?)""",(user_id,))
            self.vault.delete(db,'saas-user',user_id)
            db.execute('DELETE FROM saas_slots WHERE user_id=?',(user_id,))
            db.execute('DELETE FROM saas_brands WHERE user_id=?',(user_id,))
            # Keep only eligibility markers and minimal billing IDs until cleanup.
            db.execute('DELETE FROM saas_entitlements WHERE user_id=?',(user_id,))

    def prune_expired(self):
        with self.vault.transaction() as db:
            # Capture expired Google grants before generic expiry discards their
            # only revocation token. Live records are never decrypted by pruning.
            for row in db.execute("SELECT id FROM records WHERE kind='saas-grant' AND expires<=?",(self.clock(),)).fetchall():
                entry=self.vault.get(db,'saas-grant',row['id'])
                revocation.enqueue(self.metadata,db,row['id'],entry['payload'],self.clock())
                self.vault.delete(db,'saas-grant',row['id'])
            self.vault.purge(db,now=self.clock())
            db.execute('DELETE FROM saas_deletion_tombstones WHERE requested<?',(self.clock()-30*86400,))
            db.execute("DELETE FROM saas_google_revocations WHERE state='complete' AND requested<?",(self.clock()-30*86400,))
            db.execute("""DELETE FROM saas_slots WHERE NOT EXISTS (
                SELECT 1 FROM records WHERE kind='saas-grant' AND id=saas_slots.grant_id)""")
            db.execute("""DELETE FROM records WHERE kind='saas-capability' AND id IN (
                SELECT link.id FROM saas_record_links link
                JOIN saas_record_owners owner ON owner.kind=link.kind AND owner.id=link.id
                LEFT JOIN records grant_record ON grant_record.kind='saas-grant' AND grant_record.id=link.grant_id
                LEFT JOIN records installation ON installation.kind='saas-installation' AND installation.id=link.installation_id
                WHERE link.kind='saas-capability' AND (grant_record.id IS NULL OR installation.id IS NULL))""")
            db.execute('DELETE FROM saas_brands WHERE NOT EXISTS (SELECT 1 FROM saas_slots WHERE saas_slots.user_id=saas_brands.user_id)')
            db.execute('DELETE FROM saas_rates WHERE expires<=?',(self.clock(),))
            db.execute('DELETE FROM saas_media_allocations WHERE expires<=?',(self.clock(),))
            db.execute('DELETE FROM saas_trial_markers WHERE retain_until<=?',(self.clock(),))

    def panel(self,user_id,csrf):
        with self.vault.read_transaction() as db:
            records=self.owned_records(db,user_id,kinds=('saas-grant','saas-installation'))
            brand=db.execute('SELECT label FROM saas_brands WHERE user_id=?',(user_id,)).fetchone()
            pending=db.execute("SELECT count(*) FROM saas_google_revocations WHERE user_id=? AND state='pending'",(user_id,)).fetchone()[0]
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
        brand_panel=(f'<section aria-label="Brand"><h2>Brand</h2><form method="post" action="/account/brand" class="form-stack">{pages.hidden("csrf",csrf)}<label for="brand">Brand name</label><input id="brand" name="brand" maxlength="80" required value="{escape(brand["label"],quote=True)}"><button class="secondary-button" type="submit">Rename brand</button></form><p class="expiry-note">Existing connections stay active. Use this name when connecting another network; update your assistant’s local brand name separately.</p></section>' if brand else '')
        pending_notice='<p class="status-notice">Your Google connection is blocked in SocialCli. Google authorization cleanup is pending and retries automatically. An interrupted request whose credential has not arrived may require operator review. Connect again after cleanup completes.</p>' if pending else ''
        return brand_panel+pending_notice+'<section aria-label="Connected accounts"><h2>Connected accounts</h2>'+('<ul class="account-list">'+''.join(connections)+'</ul>' if connections else '<p class="description">No social accounts connected yet. Ask your assistant to connect your account.</p>')+'</section><section aria-label="Assistants"><h2>Assistants</h2>'+('<ul class="account-list">'+''.join(installations)+'</ul>' if installations else '<p class="description">No assistants linked yet.</p>')+'<p class="expiry-note">Selections control access to SocialCli connections. They do not narrow provider permissions, including permissions for linked accounts or individual actions. Previously issued provider tokens may remain usable until they expire or are revoked at the provider. To change an assistant’s selected platforms, revoke it and link it again.</p></section><a class="text-link" href="/account/delete">Delete my account</a>'


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
        user=await asyncio.to_thread(sessions.require_user,request,privacy=False)
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
            with metadata.vault.transaction() as db:
                job=lifecycle.disconnect(db,user.id,platform)
            if job:
                revocation.run_revocations(metadata,app.state.saas_connections.providers,clock,user_id=user.id,budget=WorkBudget(20))
        await asyncio.to_thread(commit)
        return RedirectResponse('/account/',status_code=303)

    @app.post('/account/brand')
    async def rename_brand(request:Request):
        user,form=await verified(request)
        def commit():
            with metadata.vault.transaction() as db:lifecycle.rename_brand(db,user.id,form.get('brand',''))
        await asyncio.to_thread(commit)
        return RedirectResponse('/account/',status_code=303)

    @app.get('/account/delete',response_class=HTMLResponse)
    def delete_page(request:Request):
        sessions.require_user(request,privacy=False)
        return HTMLResponse(pages.page('Delete your account',f'''<p class="eyebrow">ACCOUNT SETTINGS</p><h1 id="page-title">Delete your account?</h1>
<p class="description">This immediately stops your assistants from using SocialCli and removes your saved social credentials. We will cancel future subscription billing and delete your sign-in account. Cleanup retries automatically if a service is temporarily unavailable.</p>
<p class="status-notice">Published posts stay on your social networks. Remove local SocialCli credentials from your assistants separately. Google authorization is revoked after local access is removed; temporary failures retry automatically. Other previously delivered provider tokens keep their own expiry. Required payment records stay with Stripe; a minimal free-trial eligibility marker is retained for 365 days.</p>
<form method="post" action="/account/delete" class="form-stack">{pages.hidden('csrf',app.state.saas_csrf(request,'saas-delete'))}<label for="confirmation">Type DELETE to confirm</label><input id="confirmation" name="confirmation" autocomplete="off" required pattern="DELETE"><button class="secondary-button" type="submit">Delete my account</button></form><a class="text-link" href="/account/">Keep my account</a>'''))

    @app.post('/account/delete',response_class=HTMLResponse)
    async def delete_account(request:Request):
        user,form=await verified(request,'saas-delete')
        if form.get('confirmation')!='DELETE':raise HTTPException(400,'Type DELETE to confirm account deletion.')
        await asyncio.to_thread(lifecycle.request_delete,user.id)
        await asyncio.to_thread(revocation.run_revocations,metadata,app.state.saas_connections.providers,clock,user_id=user.id,budget=WorkBudget(20))
        response=HTMLResponse(pages.page('Deletion requested','<h1 id="page-title">Deletion requested</h1><p class="description">Your assistants no longer have access through SocialCli. Google authorization, subscription and sign-in cleanup will finish automatically. Remove the local credentials saved by your assistants.</p>'))
        response.delete_cookie(sessions.cookie,path='/',secure=not settings.allow_http_local,httponly=True,samesite='lax')
        return response
