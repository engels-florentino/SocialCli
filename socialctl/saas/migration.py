"""Explicit human-approved transfer of an existing legacy capability."""
import asyncio
import hmac
import secrets
from html import escape
from fastapi import HTTPException,Request
from fastapi.responses import HTMLResponse,RedirectResponse
from pydantic import Field
from socialctl.saas import pages
from socialctl.saas.auth import digest
from socialctl.saas.connections import AttachRequest,OAUTH_SECONDS
from socialctl.saas.pairing import proof
from socialctl.saas.routes import read_form


class MigrationRequest(AttachRequest):
    legacy_id:str=Field(pattern='^[A-Za-z0-9_-]{16,256}$')
    poll_challenge:str=Field(pattern='^[0-9a-f]{64}$')


def legacy_record(connections,db,legacy_id,credential_hash,platform):
    record=connections.vault.get(db,'connection',legacy_id)
    if (record is None or record['expires']<=connections.clock()
            or not hmac.compare_digest(record['credential_hash'],credential_hash)
            or record['payload'].get('saas') or not record['payload'].get('activated')
            or record['payload']['platform']!=platform):
        raise HTTPException(404,'legacy_unavailable: The old connection is unavailable.')
    connections.legacy.require(db,'connection',legacy_id,record)
    return record


def install_migration_routes(app,settings,metadata,sessions,clock):
    vault=metadata.vault;connections=app.state.saas_connections
    csrf,valid_form=app.state.saas_csrf,app.state.saas_valid_form

    def require(db,migration_id,request=None,user=None):
        record=vault.get(db,'saas-migration',migration_id)
        if record is None or record['expires']<=clock() or request is not None and not hmac.compare_digest(record['credential_hash'],proof(request)):
            raise HTTPException(404,'migration_expired: Request a new migration link.')
        data=record['payload'];owner=connections.owner(db,data['installation_id'])
        if owner!=data['user_id'] or user is not None and owner!=user.id:
            raise HTTPException(403,'This migration belongs to another account.')
        return record

    @app.post('/api/saas/v1/installations/{installation_id}/migrations',status_code=201)
    def start(installation_id:str,body:MigrationRequest,request:Request):
        secret=request.headers.get('x-socialcli-legacy-secret','')
        if not 16<=len(secret)<=512:raise HTTPException(404,'legacy_unavailable: The old connection is unavailable.')
        with vault.transaction() as db:
            installation=connections.installations.require(db,installation_id,request)
            user_id=installation['payload']['user_id']
            if not metadata.admit(db,'migration-installation:'+installation_id,clock(),limit=5):
                raise HTTPException(429,'Too many migration attempts. Wait a minute.')
            connections.allowed(db,user_id,new=True);connections.brand(db,user_id,body.brand)
            record=legacy_record(connections,db,body.legacy_id,digest(secret),body.platform)
            current=db.execute('SELECT grant_id FROM saas_slots WHERE user_id=? AND platform=?',(user_id,body.platform)).fetchone()
            migration_id=secrets.token_urlsafe(24)
            data={'user_id':user_id,'installation_id':installation_id,'brand':body.brand,'platform':body.platform,
                  'legacy_id':body.legacy_id,'legacy_hash':record['credential_hash'],'account':record['payload']['account'],
                  'expected_grant':current['grant_id'] if current else None,'status':'pending'}
            vault.put(db,'saas-migration',migration_id,data,expires=min(record['expires'],clock()+OAUTH_SECONDS),credential_hash=body.poll_challenge)
        return {'version':1,'migration_id':migration_id,'browser_url':settings.public_url+'/account/migrate/'+migration_id,
                'expires_in':OAUTH_SECONDS,'poll_interval':2}

    @app.get('/account/migrate/{migration_id}',response_class=HTMLResponse)
    def page(migration_id:str,request:Request):
        try:user=sessions.require_user(request)
        except HTTPException as exc:
            if exc.status_code!=401:raise
            return RedirectResponse('/account/login?next=/account/migrate/'+migration_id,status_code=303)
        with vault.transaction() as db:
            data=require(db,migration_id,user=user)['payload']
            if data['status']!='pending':raise HTTPException(409,'This migration has already been confirmed.')
        account=data['account']
        return HTMLResponse(pages.page('Move your connection',f'''<p class="eyebrow">REVIEW EXISTING ACCESS</p><h1 id="page-title">Move your connection</h1>
<p class="description">Link {escape(account.get('name',account['id']))} ({escape(account['id'])}) on {escape(data['platform'])} to {escape(data['brand'])} in your SocialCli account.</p>
<div class="status-notice">The old connection stays active until your assistant saves and activates the replacement. This starts your 14-day trial if you are eligible. No content will be published.</div>
<form method="post" action="/account/migrate/{escape(migration_id,quote=True)}" class="form-stack">{pages.hidden('csrf',csrf(request,'migrate:'+migration_id))}<button class="primary-button" type="submit">Confirm migration</button></form><a class="text-link" href="/account/">Cancel and return to account</a>'''))

    @app.post('/account/migrate/{migration_id}',response_class=HTMLResponse)
    async def approve(migration_id:str,request:Request):
        form=await read_form(request,settings.public_url);valid_form(request,form,'migrate:'+migration_id)
        user=await asyncio.to_thread(sessions.require_user,request)
        def commit():
            with vault.transaction() as db:
                record=require(db,migration_id,user=user);data=record['payload']
                if data['status']!='pending':raise HTTPException(409,'This migration has already been confirmed.')
                connections.allowed(db,user.id,new=True)
                old=legacy_record(connections,db,data['legacy_id'],data['legacy_hash'],data['platform'])
                result=connections.candidate(db,data['installation_id'],user.id,data['platform'],data['brand'],
                    data['account'],grant=old['payload']['grant'],expected=data['expected_grant'])
                candidate=vault.get(db,'saas-candidate',result['connection_id'])
                candidate['payload'].update(legacy_id=data['legacy_id'],legacy_hash=data['legacy_hash'])
                vault.put(db,'saas-candidate',result['connection_id'],candidate['payload'],
                    expires=min(candidate['expires'],record['expires']),credential_hash=candidate['credential_hash'])
                data.update(status='selected',selection=result)
                vault.put(db,'saas-migration',migration_id,data,expires=record['expires'],credential_hash=record['credential_hash'])
        await asyncio.to_thread(commit)
        return HTMLResponse(pages.page('Migration confirmed','<p class="eyebrow">CONNECTION CONFIRMED</p><h1 id="page-title">Return to your assistant</h1><p class="description">Keep the command running while it finishes moving your connection. No content was published.</p><a class="secondary-button" href="/account/">Your account</a>'))

    @app.post('/api/saas/v1/migrations/{migration_id}/poll')
    def poll(migration_id:str,request:Request):
        with vault.transaction() as db:
            data=require(db,migration_id,request)['payload']
            return data['selection'] if data['status']=='selected' else {'version':1,'status':data['status']}
