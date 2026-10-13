"""Durable, enumerated legacy allowance after the commercial cutover."""
from fastapi import HTTPException


class LegacyPolicy:
    def __init__(self,vault,clock,*,close=False):
        self.vault,self.clock=vault,clock
        with vault.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS legacy_cutoff (singleton INTEGER PRIMARY KEY CHECK(singleton=1),closed_at REAL NOT NULL,drain_until REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS legacy_allowance (kind TEXT NOT NULL,id TEXT NOT NULL,expires REAL NOT NULL,activated INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(kind,id))')
            existing=db.execute('SELECT closed_at FROM legacy_cutoff WHERE singleton=1').fetchone()
            if close and existing is None:
                now=clock();deadline=now+600
                db.execute('INSERT INTO legacy_cutoff VALUES(1,?,?)',(now,deadline))
                rows=db.execute("SELECT kind,id FROM records WHERE kind IN ('authorization','connection') AND expires>?",(now,)).fetchall()
                for row in rows:
                    record=vault.get(db,row['kind'],row['id'])
                    if record['payload'].get('saas'):continue
                    activated=row['kind']=='connection' and record['payload'].get('activated',False)
                    expires=record['expires'] if activated else min(record['expires'],deadline)
                    db.execute('INSERT INTO legacy_allowance VALUES(?,?,?,?)',(row['kind'],row['id'],expires,int(activated)))

    def closed(self,db):
        return db.execute('SELECT * FROM legacy_cutoff WHERE singleton=1').fetchone()

    def allow_creation(self,db):
        if self.closed(db):
            raise HTTPException(410,'upgrade_required: Install the current SocialCli and sign in to connect an account.')

    def require(self,db,kind,record_id,record):
        if record and record['payload'].get('saas'):return
        if not self.closed(db):return
        allowance=db.execute('SELECT * FROM legacy_allowance WHERE kind=? AND id=?',(kind,record_id)).fetchone()
        if not allowance or allowance['expires']<=self.clock():
            raise HTTPException(410,'upgrade_required: This legacy connection is unavailable. Sign in to the current SocialCli.')

    def provisional(self,db,authorization_id,connection_id,expires):
        closed=self.closed(db)
        if closed:
            authorization=self.vault.get(db,'authorization',authorization_id)
            self.require(db,'authorization',authorization_id,authorization)
            expires=min(expires,closed['drain_until'])
            db.execute('INSERT INTO legacy_allowance VALUES(?,?,?,0)',('connection',connection_id,expires))
        return expires

    def activated(self,db,connection_id,expires):
        if self.closed(db):
            db.execute("UPDATE legacy_allowance SET activated=1,expires=? WHERE kind='connection' AND id=?",(expires,connection_id))
