"""Two-phase installation/capability rotation; provider grants remain independent."""
import hmac
import secrets
from fastapi import HTTPException,Request
from pydantic import BaseModel,ConfigDict,Field
from socialctl.saas.pairing import proof,INSTALLATION_SECONDS


class CapabilityRotation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    connection_id:str=Field(pattern='^[A-Za-z0-9_-]{16,256}$')
    challenge:str=Field(pattern='^[0-9a-f]{64}$')


class RotationRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    installation_challenge:str=Field(pattern='^[0-9a-f]{64}$')
    connections:list[CapabilityRotation]=Field(default_factory=list,max_length=4)


def install_rotation_routes(app,metadata,clock):
    vault=metadata.vault;installations=app.state.saas_installations

    def owned_capability(db,connection_id,installation_id,user_id):
        record=vault.get(db,'saas-capability',connection_id)
        if record is None:raise HTTPException(409,'connection_changed: Review the current connection.')
        data=record['payload']
        if data['installation_id']!=installation_id or data['user_id']!=user_id:
            raise HTTPException(403,'This connection belongs to another installation.')
        slot=db.execute('SELECT grant_id FROM saas_slots WHERE user_id=? AND platform=?',(user_id,data['platform'])).fetchone()
        grant=vault.get(db,'saas-grant',data['grant_id'])
        if slot is None or slot['grant_id']!=data['grant_id'] or grant is None or grant['expires']<=clock():
            raise HTTPException(409,'connection_changed: Review the current connection.')
        return record

    @app.post('/api/saas/v1/installations/{installation_id}/rotations/{rotation_id}',status_code=201)
    def prepare(installation_id:str,rotation_id:str,body:RotationRequest,request:Request):
        import re
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,64}',rotation_id):raise HTTPException(400,'Invalid rotation identifier.')
        wanted=body.model_dump()
        with vault.transaction() as db:
            old=installations.require(db,installation_id,request);user_id=old['payload']['user_id']
            pending=vault.get(db,'saas-rotation',rotation_id)
            if pending:
                data=pending['payload']
                if (pending['expires']<=clock() or data.get('status')!='prepared'
                        or data['installation_id']!=installation_id or data['user_id']!=user_id
                        or data['request']!=wanted or data['old_hash']!=old['credential_hash']):
                    raise HTTPException(409,'rotation_changed: Resume the original rotation or review installation access.')
                return data['result']
            if not metadata.admit(db,'rotation:'+installation_id,clock(),limit=5):
                raise HTTPException(429,'Too many rotation attempts. Wait a minute.')
            if len({c.connection_id for c in body.connections})!=len(body.connections):
                raise HTTPException(400,'Duplicate connection in rotation.')
            next_id=secrets.token_urlsafe(24);connections=[];platforms=set()
            for entry in body.connections:
                cap=owned_capability(db,entry.connection_id,installation_id,user_id)['payload']
                if cap['platform'] in platforms:raise HTTPException(400,'Only one connection per platform can be rotated.')
                platforms.add(cap['platform'])
                connections.append({'connection_id':secrets.token_urlsafe(24),'previous_id':entry.connection_id,
                    'platform':cap['platform'],'account':cap['account'],'grant_id':cap['grant_id']})
            result={'version':1,'status':'prepared','installation_id':next_id,'connections':connections}
            payload={'status':'prepared','user_id':user_id,'installation_id':installation_id,
                     'old_hash':old['credential_hash'],'request':wanted,'result':result}
            vault.put(db,'saas-rotation',rotation_id,payload,expires=min(old['expires'],clock()+600),
                credential_hash=body.installation_challenge)
        return result

    @app.post('/api/saas/v1/rotations/{rotation_id}/commit')
    def commit(rotation_id:str,request:Request):
        with vault.transaction() as db:
            record=vault.get(db,'saas-rotation',rotation_id)
            if record is None or record['expires']<=clock() or not hmac.compare_digest(record['credential_hash'],proof(request)):
                raise HTTPException(404,'rotation_unavailable: Review pending rotation recovery.')
            data=record['payload']
            if data['status']=='committed':
                installations.require(db,data['installation_id'],request)
                return {'version':1,'status':'active'}
            old=vault.get(db,'saas-installation',data['installation_id'])
            if old is None or old['expires']<=clock() or old['credential_hash']!=data['old_hash']:
                raise HTTPException(401,'installation_revoked: Sign in to SocialCli again.')
            user=metadata.user(db,data['user_id'])
            if user is None or user['deleting']:raise HTTPException(401,'account_unavailable: Sign in to SocialCli again.')
            next_id=data['result']['installation_id'];expires=clock()+INSTALLATION_SECONDS
            for item,entry in zip(data['result']['connections'],data['request']['connections'],strict=True):
                cap=owned_capability(db,entry['connection_id'],data['installation_id'],data['user_id'])
                if cap['payload']['grant_id']!=item['grant_id'] or cap['payload']['account']!=item['account']:
                    raise HTTPException(409,'connection_changed: Review the current connection.')
                vault.put(db,'saas-capability',item['connection_id'],{**cap['payload'],'installation_id':next_id},
                    expires=expires,credential_hash=entry['challenge'])
            vault.put(db,'saas-installation',next_id,{**old['payload'],'rotated_at':clock()},
                expires=expires,credential_hash=record['credential_hash'])
            vault.delete(db,'saas-installation',data['installation_id'])
            # Old caps and pending OAuth are unusable because their installation
            # no longer exists. Remove owned capability records immediately.
            for row in db.execute("SELECT id FROM records WHERE kind='saas-capability'").fetchall():
                cap=vault.get(db,'saas-capability',row['id'])
                if cap['payload']['installation_id']==data['installation_id']:vault.delete(db,'saas-capability',row['id'])
            vault.put(db,'saas-rotation',rotation_id,{'status':'committed','user_id':data['user_id'],'installation_id':next_id},
                expires=expires,credential_hash=record['credential_hash'])
        return {'version':1,'status':'active'}

    @app.delete('/api/saas/v1/installations/{installation_id}/rotations/{rotation_id}')
    def cancel(installation_id:str,rotation_id:str,request:Request):
        with vault.transaction() as db:
            installations.require(db,installation_id,request)
            record=vault.get(db,'saas-rotation',rotation_id)
            if record:
                if record['payload']['installation_id']!=installation_id or record['payload']['status']!='prepared':
                    raise HTTPException(409,'rotation_changed: Resume the committed rotation.')
                vault.delete(db,'saas-rotation',rotation_id)
        return {'version':1,'status':'cancelled'}
