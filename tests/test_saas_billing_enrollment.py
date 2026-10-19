"""Fictional one-row enrollment: exact delta and fail-closed custody."""
import json
import os
from pathlib import Path
import sqlite3

from cryptography.fernet import Fernet
import pytest

from tests.test_saas_billing_inventory import fixture, frozen


def api():
    from socialctl.saas import billing_enrollment as enrollment
    return enrollment


def test_snapshot_adapter_matches_reviewed_inventory_and_row_encoder(tmp_path):
    from socialctl.saas import billing_inventory as inventory
    vault, keys = fixture(tmp_path)
    with sqlite3.connect(vault.path) as db:
        db.execute("INSERT INTO rates VALUES('fictional',1,2)")
    report = inventory.inspect_database(vault.path, keys)
    with api().database(vault.path, keys) as snapshot:
        assert snapshot.root == api().root_from_inventory(report)
        row = snapshot.rows['rates'][0]
        assert row['ref'].key_sha256 == inventory._rows_digest([('fictional',)])
        assert row['ref'].row_sha256 == inventory._rows_digest([(1, 2, 'fictional')])
    assert report == inventory.inspect_database(vault.path, keys)


@pytest.mark.parametrize('attack', ['symlink', 'hardlink', 'mode', 'replacement', 'parent_mode'])
def test_private_artifact_reads_reject_unsafe_custody(tmp_path, attack):
    root = tmp_path / 'private'; root.mkdir(mode=0o700)
    path = root / 'artifact.json'; path.write_bytes(b'{}'); path.chmod(0o600)
    if attack == 'symlink':
        link = root / 'link.json'; link.symlink_to(path); path = link
    elif attack == 'hardlink':
        os.link(path, root / 'alias.json')
    elif attack == 'mode':
        path.chmod(0o640)
    elif attack == 'parent_mode':
        root.chmod(0o750)
    else:
        path.unlink(); path.mkdir()
    with pytest.raises(ValueError):
        api().read_private(path)


def test_artifact_creation_is_private_durable_and_never_clobbers(tmp_path):
    root = tmp_path / 'private'; root.mkdir(mode=0o700)
    path = root / 'artifact.json'
    assert api().write_private(path, b'{}') == api().sha256(b'{}')
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError):
        api().write_private(path, b'{"replaced":true}')
    assert path.read_bytes() == b'{}'


def test_empty_inspection_never_binds(tmp_path):
    vault, keys = fixture(tmp_path)
    before = frozen(vault.path)
    with api().database(vault.path, keys) as snapshot:
        assert snapshot.records == {}
    assert frozen(vault.path) == before


class Package:
    """Independent fictional custody inputs; no production paths or keys."""
    now = 2_000_000_000
    image_digest = 'a' * 64
    collector = 'b' * 64

    def __init__(self, tmp_path, monkeypatch):
        from socialctl.saas import billing_enrollment_schema as s
        from socialctl.saas import billing_inventory as inventory
        self.s = s
        self.vault, self.keys = fixture(tmp_path)
        self.directory = tmp_path / 'custody'; self.directory.mkdir(mode=0o700)
        self.policy_path = self.directory / 'policy.json'
        self.registry_path = self.directory / 'registry.json'
        self.raw = {}; self.roles = {}; self.objects = {}
        package = self
        class FrozenClock:
            def now(self):
                return package.now
        monkeypatch.setattr(api(), 'Clock', FrozenClock)
        with self.vault.transaction() as db:
            for user in ('owner', 'reviewer', 'trial', 'deleting'):
                db.execute('INSERT INTO saas_users(id,created,deleting) VALUES(?,?,?)',
                           (user, self.now - 1000, int(user == 'deleting')))
            db.execute("INSERT INTO saas_billing(user_id,customer_id,subscription_id,customer_key,customer_started,checkout_id) VALUES('owner','cus_fictional','sub_fictional','opaque-local-key',?,'cs_fictional')", (self.now-1000,))
            db.execute("INSERT INTO saas_billing_events(account_id,mode,event_id,body_hash,customer_id,type,state,received) VALUES('acct_fictional','test','evt_fictional','body-digest','cus_fictional','invoice.paid','done',?)", (self.now-1000,))
            db.execute("INSERT INTO saas_entitlements VALUES('owner',NULL,NULL,'active',?)", (self.now+1000,))
            db.execute("INSERT INTO saas_entitlements VALUES('reviewer',NULL,NULL,'',?)", (self.now+2000,))
            db.execute("INSERT INTO saas_entitlements VALUES('trial',?,?,'',0)", (self.now-10,self.now+100))
            db.execute("INSERT INTO saas_deletions(user_id,requested) VALUES('deleting',?)", (self.now-10,))
            db.execute("INSERT INTO saas_google_revocations(id,user_id,requested,state) VALUES('revoked','deleting',?,'complete')", (self.now-10,))
            db.execute("INSERT INTO rates VALUES('rate_fictional',1,2)")
            self.vault.put(db, 'saas-checkout-intent', 'orphan_attempt', {'user_id':'orphan_owner','deferred':{}}, expires=1)
            self.vault.put(db, 'review-access-grant', 'manual_grant', {'user_id':'reviewer'}, expires=self.now+2000)
            self.vault.put(db, 'saas-grant', 'unrelated_grant', {'user_id':'owner','opaque':'fictional'}, expires=self.now+1000)
        with sqlite3.connect(self.vault.path) as db:
            db.execute('PRAGMA journal_mode=DELETE')
        inventory_report = inventory.inspect_database(self.vault.path, self.keys)
        self.target = dict(host_authority_id='host_fictional',service_id='service_fictional',mount_id='mount_fictional',
            database_path=str(self.vault.path),device=self.vault.path.stat().st_dev,inode=self.vault.path.stat().st_ino,
            account_id='acct_fictional',mode='test')
        with api().database(self.vault.path, self.keys) as snap:
            required, records = api().required_coverage(snap)
            self.rows = {key: item['ref'].model_dump() for key,item in required.items()}
            self.row_by_user = {(key[0],item['values'].get('user_id','owner')): item['ref'].model_dump() for key,item in required.items()}
            baseline = snap.root.state_sha256
        self.add('inventory','inventory',inventory_report)
        source_files = [{'name': name, 'sha256':api().sha256(path.read_bytes())} for name,path in api().source_paths().items()]
        self.fact('source','source',{'files':source_files})
        self.fact('image','image',{'image_digest':self.image_digest,'source_sha256':self.digest('source')})
        self.fact('configuration','configuration',{'account_id':'acct_fictional','mode':'test','app_id':'app_fictional','price_id':'price_fictional','portal_id':'bpc_fictional','observed_at':self.now-10})
        self.fact('initial_empty','initial_empty',{'source_receipt_sha256':'2'*64,'endpoint_id':'empty_endpoint','account_id':'acct_fictional','mode':'test',
                    'observed_at':self.now-1000,'billing_rows':0,'event_rows':0,'paid_state_rows':0,'encrypted_records':0})
        self.fact('deployment','deployment',{'source_receipt_sha256':'3'*64,'transition_id':'transition_initial','from_id':'empty_endpoint','to_id':'current_endpoint',
                    'kind':'initial','account_id':'acct_fictional','mode':'test','observed_at':self.now-900,'finding':'continuous'})
        for name, endpoint in [('authority','current_endpoint'),('auth_authority','auth_endpoint')]:
            self.fact(name,name,{'target':self.target,'endpoint_id':endpoint,'observed_at':self.now-2,'expires_at':self.now+600,
                                 'state':'authoritative','competing_authority_ids':[]})
        self.fact('freeze','freeze',{'target':self.target,'freeze_id':'freeze_fictional','started_at':self.now-10,
                  'observed_at':self.now-2,'writers':[{'name':name,'state':'drained'} for name in
                  ('ingress','webhooks','workers','schedulers','deletion','revocation','reminders')],
                  'arrivals':'reject_for_retry','network':'deny_all'})
        self.fact('backup','backup',{'target':self.target,'authority_id':'current_endpoint','auth_authority_id':'auth_endpoint',
                  'sqlite_snapshot_sha256':'c'*64,'auth_snapshot_sha256':'d'*64,'verified_at':self.now-2})
        self.fact('rehearsal','rehearsal',{'source_sha256':self.digest('source'),'image_sha256':self.digest('image'),
                  'previous_image_sha256':'e'*64,'baseline_sha256':baseline,'exact_delta_sha256':'f'*64,
                  'matching_test_startup':'accepted','foreign_account_startup':'rejected_before_provider',
                  'live_startup':'rejected_before_provider','network':'deny_all','restore':'ready',
                  'uncertainty_ids':['billing_uncertain','orphan_uncertain','unknown_kind_uncertain']})
        self.fact('compatibility','compatibility',{'previous_image_sha256':'e'*64,'binding':'preserved','baseline':'preserved'})
        objects=[]
        for kind,oid,customer,subscription,checkout,invoice,user in [
            ('account','acct_fictional',None,None,None,None,None),
            ('price','price_fictional',None,None,None,None,None),
            ('portal','bpc_fictional',None,None,None,None,None),
            ('customer','cus_fictional',None,None,None,None,'owner'),
            ('subscription','sub_fictional','cus_fictional',None,None,None,'owner'),
            ('checkout','cs_fictional','cus_fictional','sub_fictional',None,None,'owner'),
            ('invoice','in_fictional','cus_fictional','sub_fictional','cs_fictional',None,'owner'),
            ('event','evt_fictional','cus_fictional','sub_fictional','cs_fictional','in_fictional','owner'),
            ('line_item','li_fictional',None,None,'cs_fictional',None,'owner'),
        ]:
            objects.append(dict(request_id='req_'+kind,response_sha256='4'*64,reference_id=kind,requested_id=oid,returned_id=oid,object_type=kind,
                  account_id='acct_fictional',mode='test',user_id=user,app_id='app_fictional',customer_id=customer,
                  subscription_id=subscription,checkout_id=checkout,invoice_id=invoice,
                  price_id='price_fictional' if kind in {'subscription','checkout','invoice','line_item'} else None,
                  event_object_id='in_fictional' if kind == 'event' else None,observed_at=self.now-2,
                  outcome='present',retained_refs=[]))
        collections=[{'collection':kind,'parent_id':parent,'object_ids':[oid],'pagination':'complete','observed_at':self.now-2}
                     for kind,parent,oid in [('subscriptions','cus_fictional','sub_fictional'),('sessions','cus_fictional','cs_fictional'),
                                             ('invoices','cus_fictional','in_fictional'),('line_items','cs_fictional','li_fictional')]]
        self.fact('provider','provider',{'objects':objects,'collections':collections})
        manual_row=self.row_by_user[('saas_entitlements','reviewer')]
        self.fact('owner_instruction','owner_instruction',{'user_id':'reviewer','rows':[manual_row],
                'conversation_receipt_sha256':'1'*64,'observed_at':self.now-500})
        policy=dict(schema='socialcli-legacy-enrollment-policy/v1',custody_directory=str(self.directory),
            registry_path=str(self.registry_path),target=self.target,
            principals=[{'id':'owner_principal','role':'owner'},{'id':'reviewer_principal','role':'independent_reviewer'}],
            source_sha256=self.digest('source'),tool_sha256=api().sha256(api().source_paths()['enroll_legacy_test_billing.py'].read_bytes()),
            image_sha256=self.digest('image'),config_sha256=self.digest('configuration'),collector_sha256=self.collector,
            app_id='app_fictional',provider_max_age=900,authority_max_age=300,manifest_max_age=300)
        self.add('policy','policy',policy)
        def lineage(name,category,user,rows,record_refs,providers):
            manual=category=='paid_manual'
            return dict(id=name,category=category,rows=rows,record_refs=record_refs,user_id=user,
                account_id=None if manual else 'acct_fictional',mode=None if manual else 'test',
                origin_transition_id='transition_initial',historical_refs=[self.ref('deployment')],
                provider_refs=[self.ref('provider','objects.'+obj) for obj in providers],
                owner_instruction_refs=[self.ref('owner_instruction')] if manual else [])
        self.evidence=dict(schema='socialcli-legacy-enrollment-evidence/v1',bundle_id='bundle_'+'1'*32,created_at=self.now-1,
            artifacts=[],transitions=[dict(id='transition_initial',from_id='empty_endpoint',to_id='current_endpoint',kind='initial',
                account_id='acct_fictional',mode='test',receipts=[self.ref('initial_empty'),self.ref('deployment')],limitation='')],
            lineages=[
                lineage('billing','billing','owner',[self.row_by_user[('saas_billing','owner')]],[],['customer','subscription','checkout']),
                lineage('event','event','owner',[self.row_by_user[('saas_billing_events','owner')]],[],['event']),
                lineage('paid','paid_provider','owner',[self.row_by_user[('saas_entitlements','owner')]],[],['subscription']),
                lineage('manual','paid_manual','reviewer',[manual_row],[{'kind':'review-access-grant','id':'manual_grant'}],[]),
                lineage('intent','intent','orphan_owner',[],[{'kind':'saas-checkout-intent','id':'orphan_attempt'}],['account']),
            ],uncertainties=[
                dict(id='billing_uncertain',row_refs=[self.row_by_user[('saas_billing','owner')]],record_refs=[],
                     reason_code='billing_classification',provenance_lineage_ids=['billing'],preserved=True),
                dict(id='orphan_uncertain',row_refs=[],record_refs=[{'kind':'saas-checkout-intent','id':'orphan_attempt'}],
                     reason_code='orphan_record',provenance_lineage_ids=['intent'],preserved=True),
                dict(id='unknown_kind_uncertain',row_refs=[],record_refs=[{'kind':'review-access-grant','id':'manual_grant'}],
                     reason_code='unknown_kind',provenance_lineage_ids=['manual'],preserved=True),
            ],documentary_review_ref=self.ref('documentary_review'))
        self.documentary=dict(account_id='acct_fictional',mode='test',original_empty_id='empty_endpoint',
            current_authority_id='current_endpoint',transition_ids=['transition_initial'],
            lineage_ids=[l['id'] for l in self.evidence['lineages']],rows=list(self.rows.values()),
            record_refs=[{'kind':kind,'id':oid} for kind,oid in records],
            uncertainty_ids=[u['id'] for u in self.evidence['uncertainties']],evidence_sha256=[],findings=[])
        self.refresh_rehearsal()
        self.seal()

    def refresh_rehearsal(self):
        # Independent fictional fixture uses the actual existing restore recipe;
        # enrollment's in-memory verifier is not used to manufacture its roots.
        import uuid
        from socialctl.saas import billing_inventory as inventory
        from socialctl.saas.restore_guard import export_manifest, prepare_restore, release_restore
        copy_dir = self.directory.parent / ('prepared_' + uuid.uuid4().hex)
        copy_dir.mkdir(mode=0o700)
        copy_path = copy_dir / 'copy.sqlite3'
        authority = export_manifest(self.vault.path, now=self.now)
        prepare_restore(self.vault.path, copy_path, self.keys, authority,
                        now=self.now, snapshot_created=self.now)
        release_restore(copy_path, self.keys, authority, now=self.now, identity_cleanup_confirmed=True)
        with sqlite3.connect(copy_path) as db:
            db.execute('PRAGMA journal_mode=DELETE')
        report = inventory.inspect_database(copy_path, self.keys)
        root = api().root_from_inventory(report)
        source = api().root_from_inventory(self.objects['inventory'])
        target = self.target | {'database_path':str(copy_path), 'device':report['target']['device'],
            'inode':report['target']['inode'], 'service_id':'rehearsal_service', 'mount_id':'rehearsal_mount'}
        before = {table.name:table for table in source.tables}
        reasons = {'records':'access_cleanup', 'saas_record_owners':'access_cleanup',
            'saas_record_links':'access_cleanup', 'saas_record_emails':'access_cleanup',
            'saas_users':'session_invalidation', 'saas_google_revocations':'revocation_authority',
            'legacy_allowance':'legacy_cleanup', 'saas_restore_guard':'restore_guard'}
        changes = [dict(table=table.name, before=before[table.name].model_dump() if table.name in before else None,
                    after=table.model_dump(), reason=reasons.get(table.name,'deletion_authority'))
                   for table in root.tables if before.get(table.name) != table]
        binding = dict(kind='saas-billing-environment',id='active',expires=253402300799,
                       credential_hash='',payload={'account_id':'acct_fictional','mode':'test'})
        ciphertext = Fernet(self.keys[0]).encrypt(json.dumps(binding,separators=(',',':')).encode())
        with sqlite3.connect(copy_path) as db:
            db.execute('INSERT INTO records(kind,id,expires,credential_hash,payload) VALUES(?,?,?,?,?)',
                       ('saas-billing-environment','active',253402300799,'',ciphertext))
        post = api().root_from_inventory(inventory.inspect_database(copy_path,self.keys))
        delta = self.s.RehearsalDelta.model_validate(dict(post=post.model_dump(),post_excluding_binding=root.model_dump(),
            binding=binding,binding_ciphertext_sha256=api().sha256(ciphertext),result='one_binding_insert_verified'))
        self.objects['rehearsal']['facts'].update(prepared_copy=dict(
            source_target=self.target, source_authority_id='current_endpoint', source_baseline=source.model_dump(),
            target=target, identity=report['target'], baseline=root.model_dump(),
            preparation_receipt_sha256='5'*64, prepared_at=self.now,snapshot_created_at=self.now,changes=changes),
            enrollment_delta=delta.model_dump(),baseline_sha256=root.state_sha256,
            exact_delta_sha256=api().sha256(api().encode(delta)))
        self.add('rehearsal','rehearsal',self.objects['rehearsal'])
        self.objects['compatibility']['facts'].update(rehearsal_sha256=self.digest('rehearsal'),copy_target=target,
            before=post.model_dump(),after=post.model_dump(),binding_ciphertext_sha256=api().sha256(ciphertext))
        self.add('compatibility','compatibility',self.objects['compatibility'])

    @staticmethod
    def ref(name,selector='facts'):
        return {'artifact_id':name,'selector':selector}

    def add(self,name,role,obj):
        self.objects[name]=obj; self.roles[name]=role
        self.raw[name]=(json.dumps(obj,sort_keys=True,separators=(',',':'))+'\n').encode()
        return self

    def fact(self,name,role,facts):
        return self.add(name,role,{'schema':'socialcli-legacy-enrollment-facts/v1','collector_sha256':self.collector,'facts':facts})

    def digest(self,name):
        return api().sha256(self.raw[name])

    def approval(self,name,scope,subject,role,index):
        return self.add(name,'approval',dict(schema='socialcli-legacy-enrollment-approval/v1',approval_id='approval_'+str(index)*32,
            principal_id='owner_principal' if role=='owner' else 'reviewer_principal',role=role,scope=scope,
            subject_sha256=subject,policy_sha256=self.digest('policy'),created_at=self.now,expires_at=self.now+600,decision='approve'))

    def seal(self):
        # New fictional approvals deliberately re-sign edited facts, so failures
        # test semantic policy as well as byte-digest tampering.
        for name in list(self.objects):
            self.add(name,self.roles[name],self.objects[name])
        artifacts=[dict(id=name,sha256=self.digest(name),role=role) for name,role in self.roles.items()
                   if role not in {'approval','manifest','receipt','evidence','documentary_review'}]
        self.documentary['evidence_sha256']=[a['sha256'] for a in artifacts]
        self.fact('documentary_review','documentary_review',self.documentary)
        self.evidence['artifacts']=artifacts+[dict(id='documentary_review',sha256=self.digest('documentary_review'),role='documentary_review')]
        self.add('evidence','evidence',self.evidence)
        for index,role in enumerate(('owner','independent_reviewer'),1):
            self.approval('documentary_'+role,'documentary_review',self.digest('evidence'),role,index)
        self.flush()

    def flush(self):
        for name,raw in self.raw.items():
            path=self.directory/(name+'.json'); path.write_bytes(raw); path.chmod(0o600)
        registry={'schema':'socialcli-legacy-enrollment-registry/v1','entries':[
            dict(id=name,filename=name+'.json',sha256=self.digest(name),role=self.roles[name]) for name in self.raw]}
        self.registry_path.write_text(json.dumps(registry)); self.registry_path.chmod(0o600)

    def runtime(self):
        return api().load_runtime(self.policy_path,image_digest=self.image_digest)

    def prepare(self):
        path=self.directory/'prepared.json'
        result=api().prepare(self.runtime(),self.keys,path)
        self.add('manifest','manifest',json.loads(path.read_bytes()))
        for index,role in enumerate(('owner','independent_reviewer'),3):
            self.approval('final_'+role,'enrollment_manifest',self.digest('manifest'),role,index)
        self.flush()
        return result

    def apply(self):
        return api().apply(self.runtime(),self.keys,self.directory/'receipt.json')

    def register_receipt(self):
        self.add('receipt','receipt',json.loads((self.directory/'receipt.json').read_bytes())); self.flush()


def test_complete_fictional_history_changes_exactly_one_record_and_fresh_verifies(tmp_path,monkeypatch):
    p=Package(tmp_path,monkeypatch)
    with sqlite3.connect(p.vault.path) as db:
        before=db.execute('SELECT * FROM records ORDER BY kind,id').fetchall()
    assert p.prepare()['status']=='prepared'
    assert p.apply()['status']=='one_binding_insert_verified'
    with sqlite3.connect(p.vault.path) as db:
        after=db.execute("SELECT * FROM records WHERE kind!='saas-billing-environment' ORDER BY kind,id").fetchall()
        assert db.execute("SELECT count(*) FROM records WHERE kind='saas-billing-environment'").fetchone()==(1,)
    assert before==after
    p.register_receipt()
    p.now+=1000  # Verify original operation window, not revived apply authority.
    assert api().verify(p.runtime(),p.keys,p.directory/'verified.json')['status']=='exact_delta_verified'


@pytest.mark.parametrize('attack',[
    'missing_lineage','matching_events_only','current_config_only','broken_chain','foreign_transition','live_transition',
    'contradictory_continuity','unresolved_documentary','partial_pagination','wrong_returned_id','wrong_customer',
    'wrong_owner','missing_price','missing_collection','unknown_intent_provenance','missing_manual_grant',
    'incomplete_row_map','duplicate_lineage','orphan_record_omitted','unknown_kind_unexplained','forged_selector',
    'wrong_collector','active_writer','stale_provider','future_authority','wrong_freeze_target',
])
def test_semantically_invalid_resigned_evidence_never_prepares_or_mutates(tmp_path,monkeypatch,attack):
    p=Package(tmp_path,monkeypatch)
    facts=lambda name:p.objects[name]['facts']
    if attack=='missing_lineage': p.evidence['lineages']=[]
    elif attack=='matching_events_only': p.evidence['lineages']=[p.evidence['lineages'][1]]
    elif attack=='current_config_only': p.evidence['transitions']=[]
    elif attack=='broken_chain': p.evidence['transitions'][0]['from_id']='unexplained_import'
    elif attack=='foreign_transition': p.evidence['transitions'][0]['account_id']='acct_foreign'
    elif attack=='live_transition': p.evidence['transitions'][0]['mode']='live'
    elif attack=='contradictory_continuity': facts('deployment')['finding']='contradictory'
    elif attack=='unresolved_documentary': p.documentary['findings']=['unresolved_provenance']
    elif attack=='partial_pagination': facts('provider')['collections'][0]['pagination']='partial'
    elif attack=='wrong_returned_id': facts('provider')['objects'][3]['returned_id']='cus_foreign'
    elif attack=='wrong_customer': next(o for o in facts('provider')['objects'] if o['object_type']=='subscription')['customer_id']='cus_foreign'
    elif attack=='wrong_owner': next(o for o in facts('provider')['objects'] if o['object_type']=='customer')['user_id']='another_owner'
    elif attack=='missing_price': facts('provider')['objects']=[o for o in facts('provider')['objects'] if o['object_type']!='price']
    elif attack=='missing_collection': facts('provider')['collections'].pop()
    elif attack=='unknown_intent_provenance': p.evidence['lineages'][-1]['historical_refs']=[]
    elif attack=='missing_manual_grant': p.evidence['lineages'][3]['owner_instruction_refs']=[]
    elif attack=='incomplete_row_map': p.documentary['rows'].pop()
    elif attack=='duplicate_lineage': p.evidence['lineages'].append(p.evidence['lineages'][0])
    elif attack=='orphan_record_omitted': p.evidence['lineages'][-1]['record_refs']=[]
    elif attack=='unknown_kind_unexplained': p.evidence['uncertainties'].pop()
    elif attack=='forged_selector': p.evidence['lineages'][0]['historical_refs'][0]['selector']='SELECT * FROM records'
    elif attack=='wrong_collector': p.objects['provider']['collector_sha256']='9'*64
    elif attack=='active_writer': facts('freeze')['writers'][0]['state']='active'
    elif attack=='stale_provider': facts('provider')['objects'][0]['observed_at']=p.now-901
    elif attack=='future_authority': facts('authority')['observed_at']=p.now+1
    elif attack=='wrong_freeze_target': facts('freeze')['target']=facts('freeze')['target']|{'inode':0}
    p.seal()
    before=frozen(p.vault.path)
    with pytest.raises(ValueError): p.prepare()
    assert frozen(p.vault.path)==before
    assert not (p.directory/'prepared.json').exists()


@pytest.mark.parametrize('change',[
    "UPDATE rates SET count=count+1",
    "UPDATE saas_billing SET sync_due=sync_due+1",
    "UPDATE saas_entitlements SET paid_until=paid_until+1 WHERE user_id='reviewer'",
    "UPDATE saas_entitlements SET trial_end=trial_end+1 WHERE user_id='trial'",
    "UPDATE saas_deletions SET requested=requested+1",
    "UPDATE saas_google_revocations SET attempts=attempts+1",
    "UPDATE saas_users SET deleting=1 WHERE id='owner'",
    "UPDATE saas_billing SET lease='lease_fictional',lease_until=2000000001",
    "DELETE FROM records WHERE kind='saas-checkout-intent'",
    "UPDATE saas_record_owners SET user_id='other' WHERE kind='review-access-grant'",
])
def test_full_cas_rejects_every_changed_table_including_rates_and_orphans(tmp_path,monkeypatch,change):
    p=Package(tmp_path,monkeypatch); p.prepare()
    with sqlite3.connect(p.vault.path) as db: db.execute(change)
    before=frozen(p.vault.path)
    with pytest.raises(ValueError): p.apply()
    assert frozen(p.vault.path)==before
    assert not (p.directory/'receipt.json').exists()


@pytest.mark.parametrize('kind',['matching','foreign','stale_owner','stale_link','stale_email'])
def test_existing_binding_or_stale_marker_index_never_replaced(tmp_path,monkeypatch,kind):
    p=Package(tmp_path,monkeypatch); p.prepare()
    with p.vault.transaction() as db:
        if kind in {'matching','foreign'}:
            p.vault.put(db,'saas-billing-environment','active',{'account_id':'acct_fictional' if kind=='matching' else 'acct_foreign','mode':'test'},expires=253402300799)
        elif kind=='stale_owner': db.execute("INSERT INTO saas_record_owners VALUES('owner','saas-billing-environment','active')")
        elif kind=='stale_link': db.execute("INSERT INTO saas_record_links VALUES('saas-billing-environment','active',NULL,NULL)")
        else: db.execute("INSERT INTO saas_record_emails VALUES('saas-billing-environment','active','fictional_hash')")
    with sqlite3.connect(p.vault.path) as db: db.execute('PRAGMA journal_mode=DELETE')
    before=frozen(p.vault.path)
    with pytest.raises(ValueError): p.apply()
    assert frozen(p.vault.path)==before


@pytest.mark.parametrize('attack',['wrong_key','tampered_payload','duplicate_json_envelope','extra_schema','extra_trigger','quarantine','replace_path'])
def test_key_schema_restore_and_target_guards_fail_before_insert(tmp_path,monkeypatch,attack):
    p=Package(tmp_path,monkeypatch); p.prepare()
    if attack=='wrong_key': p.keys=[Fernet.generate_key()]
    elif attack=='replace_path':
        import shutil
        copy=p.vault.path.parent/'copy.sqlite3'; shutil.copyfile(p.vault.path,copy); copy.chmod(0o600); os.replace(copy,p.vault.path)
    else:
        with sqlite3.connect(p.vault.path) as db:
            if attack=='tampered_payload': db.execute("UPDATE records SET payload=X'00' WHERE kind='saas-checkout-intent'")
            elif attack=='duplicate_json_envelope':
                raw=b'{"kind":"saas-checkout-intent","kind":"saas-checkout-intent","id":"orphan_attempt","expires":1,"credential_hash":"","payload":{"user_id":"orphan_owner"}}'
                db.execute("UPDATE records SET payload=? WHERE kind='saas-checkout-intent'",(Fernet(p.keys[0]).encrypt(raw),))
            elif attack=='extra_schema': db.execute('CREATE TABLE extra(value)')
            elif attack=='extra_trigger': db.execute('CREATE TRIGGER extra AFTER INSERT ON records BEGIN DELETE FROM rates; END')
            elif attack=='quarantine':
                from socialctl.saas.billing_inventory import _RESTORE_SCHEMA
                db.execute(_RESTORE_SCHEMA[3]); db.execute("INSERT INTO saas_restore_guard VALUES(1,'quarantined',0,0)")
    before=frozen(p.vault.path)
    with pytest.raises(ValueError): p.apply()
    assert frozen(p.vault.path)==before


@pytest.mark.parametrize('when',['before_lock','during_hash','before_commit'])
def test_expiry_is_rechecked_after_lock_hash_and_before_commit(tmp_path,monkeypatch,when):
    p=Package(tmp_path,monkeypatch); p.prepare(); before=frozen(p.vault.path)
    if when=='before_lock': p.now+=300
    elif when=='during_hash':
        original=api().snapshot
        def delayed(*args,**kwargs):
            result=original(*args,**kwargs); p.now+=300; return result
        monkeypatch.setattr(api(),'snapshot',delayed)
    else:
        original=api().exact_delta
        def delayed(*args,**kwargs):
            result=original(*args,**kwargs); p.now+=300; return result
        monkeypatch.setattr(api(),'exact_delta',delayed)
    with pytest.raises(ValueError): p.apply()
    assert frozen(p.vault.path)==before


def test_lost_receipt_is_uncertain_full_delta_only_and_never_replays(tmp_path,monkeypatch):
    p=Package(tmp_path,monkeypatch); p.prepare()
    original=api().write_private
    def disk_full(*args,**kwargs): raise OSError('fictional private disk diagnostic')
    monkeypatch.setattr(api(),'write_private',disk_full)
    assert p.apply()=={'status':'uncertain', 'diagnostic': {
        'stage': 'apply_receipt', 'error_code': 'completion_unverified'}}
    monkeypatch.setattr(api(),'write_private',original)
    before=frozen(p.vault.path)
    p.now+=1000
    result=api().recover(p.runtime(),p.keys,p.directory/'recovered.json')
    assert result['status']=='uncertain' and result['delta']=='exact_delta_verified'
    assert not (p.directory/'recovered.json').exists()
    with pytest.raises(ValueError): p.apply()
    assert frozen(p.vault.path)==before


def test_normal_write_ends_exact_delta_window_without_repair(tmp_path,monkeypatch):
    p=Package(tmp_path,monkeypatch); p.prepare(); p.apply(); p.register_receipt()
    with sqlite3.connect(p.vault.path) as db: db.execute('UPDATE rates SET count=count+1')
    before=frozen(p.vault.path)
    with pytest.raises(api().BaselineMismatch): api().verify(p.runtime(),p.keys,p.directory/'verify.json')
    assert frozen(p.vault.path)==before
    assert not (p.directory/'verify.json').exists()


def test_policy_role_forgery_and_unapproved_tool_image_are_rejected(tmp_path,monkeypatch):
    p=Package(tmp_path,monkeypatch); p.prepare()
    p.objects['final_independent_reviewer']['principal_id']='owner_principal'; p.add('final_independent_reviewer','approval',p.objects['final_independent_reviewer']); p.flush()
    before=frozen(p.vault.path)
    with pytest.raises(ValueError): p.apply()
    with pytest.raises(ValueError): api().load_runtime(p.policy_path,image_digest='0'*64)
    p.objects['policy']['tool_sha256']='0'*64; p.seal()
    with pytest.raises(ValueError): p.runtime()
    assert frozen(p.vault.path)==before


@pytest.mark.parametrize('fault',['insert_conflict','disk_full','before_commit','lost_commit_ack','readback_failure', 'postcommit_check'])
def test_transaction_faults_rollback_before_commit_and_preserve_uncertainty_after(tmp_path,monkeypatch,fault):
    p=Package(tmp_path,monkeypatch); p.prepare()
    before=frozen(p.vault.path)
    connect=sqlite3.connect
    committed=[False]
    class Crash(BaseException): pass
    class Connection:
        def __init__(self,connection): self.connection=connection
        def execute(self,sql,*args,**kwargs):
            if sql.startswith('INSERT INTO records'):
                if fault=='insert_conflict': raise sqlite3.IntegrityError('fictional conflict')
                if fault=='disk_full': raise sqlite3.OperationalError('fictional disk full')
            if sql=='COMMIT':
                if fault=='before_commit': raise Crash()
                result=self.connection.execute(sql,*args,**kwargs); committed[0]=True
                if fault=='lost_commit_ack': raise OSError('fictional lost acknowledgement')
                return result
            return self.connection.execute(sql,*args,**kwargs)
        def rollback(self): return self.connection.rollback()
        def close(self): return self.connection.close()
    def wrapped(path,*args,**kwargs):
        if committed[0] and fault=='readback_failure' and 'mode=ro' in str(path):
            raise OSError('fictional readback failure')
        return Connection(connect(path,*args,**kwargs))
    monkeypatch.setattr(api().sqlite3,'connect',wrapped)
    final_check = api().final_time_check
    def check(*args):
        if committed[0] and fault == 'postcommit_check':
            raise ValueError('fictional private postcommit check')
        return final_check(*args)
    monkeypatch.setattr(api(), 'final_time_check', check)
    if fault in {'insert_conflict','disk_full'}:
        with pytest.raises(api().EnrollmentError) as caught: p.apply()
        assert caught.value.stage == 'apply_precommit'
        assert str(caught.value) == api().ERROR
    else:
        stage = {'before_commit': 'apply_commit', 'lost_commit_ack': 'apply_commit',
                 'readback_failure': 'apply_readback', 'postcommit_check': 'apply_postcommit'}[fault]
        assert p.apply()=={'status':'uncertain', 'diagnostic': api().diagnostic(stage)}
    monkeypatch.setattr(api().sqlite3,'connect',connect)
    if not committed[0]: assert frozen(p.vault.path)==before
    else:
        result=api().recover(p.runtime(),p.keys,p.directory/'recovered.json')
        assert result['status']=='uncertain' and result['delta']=='exact_delta_verified'
    assert not (p.directory/'receipt.json').exists()


def test_encryption_failure_rolls_back_without_partial_artifacts(tmp_path,monkeypatch):
    p=Package(tmp_path,monkeypatch); p.prepare(); before=frozen(p.vault.path)
    class Cipher:
        def __init__(self,*args): pass
        def encrypt(self,*args): raise OSError('fictional encryption failure')
    monkeypatch.setattr(api(),'Fernet',Cipher)
    with pytest.raises(ValueError): p.apply()
    assert frozen(p.vault.path)==before


def test_busy_writer_uses_bounded_timeout_and_never_inserts(tmp_path,monkeypatch):
    p=Package(tmp_path,monkeypatch); p.prepare()
    connection=sqlite3.connect(p.vault.path); connection.execute('BEGIN IMMEDIATE')
    connect=sqlite3.connect
    def impatient(*args,**kwargs):
        assert kwargs['timeout']==5
        kwargs['timeout']=0.01
        return connect(*args,**kwargs)
    monkeypatch.setattr(api().sqlite3,'connect',impatient)
    try:
        with pytest.raises(ValueError): p.apply()
    finally:
        connection.rollback(); connection.close()
    monkeypatch.setattr(api().sqlite3,'connect',connect)
    with connect(p.vault.path) as db:
        assert db.execute("SELECT count(*) FROM records WHERE kind='saas-billing-environment'").fetchone()==(0,)


def test_matching_startup_accepts_and_foreign_live_reject_before_provider_calls(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from socialctl.saas.billing import BillingService
    p=Package(tmp_path,monkeypatch); p.prepare(); p.apply()
    class NoProvider:
        def __getattr__(self,name): raise AssertionError('provider call forbidden')
    metadata=SimpleNamespace(vault=p.vault)
    before_rows=None
    with sqlite3.connect(p.vault.path) as db:
        before_rows=db.execute('SELECT * FROM records ORDER BY kind,id').fetchall()
    for account,mode in [('acct_fictional','test'),('acct_foreign','test'),('acct_fictional','live')]:
        settings=SimpleNamespace(account_id=account,mode=mode)
        if (account,mode)==('acct_fictional','test'):
            BillingService(metadata,settings,NoProvider(),lambda:p.now,'https://fictional.invalid')
        else:
            with pytest.raises(ValueError,match='billing environment'):
                BillingService(metadata,settings,NoProvider(),lambda:p.now,'https://fictional.invalid')
    with sqlite3.connect(p.vault.path) as db:
        assert db.execute('SELECT * FROM records ORDER BY kind,id').fetchall()==before_rows


def test_constructor_and_network_access_are_not_used_by_enrollment(tmp_path,monkeypatch):
    import socket
    from socialctl.connection_service.store import Store
    from socialctl.saas.store import SaaSStore
    from socialctl.saas.billing import BillingService
    p=Package(tmp_path,monkeypatch)
    def forbidden(*args,**kwargs): raise AssertionError('forbidden constructor or network')
    for cls in (Store,SaaSStore,BillingService): monkeypatch.setattr(cls,'__init__',forbidden)
    monkeypatch.setattr(socket,'socket',forbidden)
    p.prepare(); assert p.apply()['status']=='one_binding_insert_verified'


def test_rotated_keys_preserve_existing_ciphertexts(tmp_path,monkeypatch):
    p=Package(tmp_path,monkeypatch)
    p.keys.insert(0,Fernet.generate_key())
    p.prepare(); assert p.apply()['status']=='one_binding_insert_verified'
    with sqlite3.connect(p.vault.path) as db:
        marker=db.execute("SELECT payload FROM records WHERE kind='saas-billing-environment'").fetchone()[0]
    assert json.loads(Fernet(p.keys[0]).decrypt(marker))['payload']=={'account_id':'acct_fictional','mode':'test'}


def test_script_failures_are_fixed_sanitized_even_for_bad_args_and_imports(tmp_path):
    import subprocess,sys
    script=api().source_paths()['enroll_legacy_test_billing.py']
    result=subprocess.run([sys.executable,str(script),'apply','private-token-never-echo'],capture_output=True,text=True)
    assert result.returncode==1
    assert result.stderr==''
    assert json.loads(result.stdout)=={'status':'failed','error':'Enrollment operation failed.',
        'diagnostic': {'stage': 'cli_arguments', 'error_code': 'invalid_arguments'}}
    isolated=tmp_path/'isolated.py'; isolated.write_bytes(script.read_bytes())
    result=subprocess.run([sys.executable,'-I','-S',str(isolated),'apply'],capture_output=True,text=True,env={'PATH':os.environ['PATH']})
    assert result.returncode==1 and result.stderr==''
    assert json.loads(result.stdout)=={'status':'failed','error':'Enrollment operation failed.',
        'diagnostic': {'stage': 'cli_bootstrap', 'error_code': 'bootstrap_failed'}}


@pytest.mark.parametrize('attack', ['extra_field', 'wrong_scalar', 'false_classification'])
def test_signed_inventory_extra_fields_and_false_classification_are_rejected(tmp_path,monkeypatch,attack):
    p=Package(tmp_path,monkeypatch)
    if attack == 'extra_field': p.objects['inventory']['unexpected']='not part of inventory contract'
    elif attack == 'wrong_scalar': p.objects['inventory']['billing']['rows'][0]['leased']=0
    else: p.objects['inventory']['billing']['rows'][0]['classification']='review'
    p.seal()
    with pytest.raises(ValueError): p.prepare()


def test_duplicate_structured_references_are_rejected(tmp_path,monkeypatch):
    p=Package(tmp_path,monkeypatch)
    p.evidence['lineages'][0]['historical_refs']*=2
    p.seal()
    with pytest.raises(ValueError): p.prepare()


def test_approval_expiry_at_final_commit_check_rolls_back(tmp_path,monkeypatch):
    p=Package(tmp_path,monkeypatch); p.prepare()
    p.objects['final_owner']['expires_at']=p.now+2
    p.add('final_owner','approval',p.objects['final_owner']); p.flush()
    before=frozen(p.vault.path)
    original=api().Snapshot.check_identity
    calls=[0]
    def expire_at_last_identity(self):
        original(self)
        # Trigger only once the binding is present; the final per-operation
        # identity check follows full manifest validation immediately before commit.
        if api().BINDING_KEY in self.records: return
        if self.db.in_transaction and self.db.execute("SELECT count(*) FROM records WHERE kind='saas-billing-environment'").fetchone()[0]:
            calls[0]+=1; p.now+=2
    monkeypatch.setattr(api().Snapshot,'check_identity',expire_at_last_identity)
    with pytest.raises(ValueError): p.apply()
    assert calls[0] and frozen(p.vault.path)==before


def orphan_event_package(p, payload):
    """Retain a fully enumerated orphan so rejection tests provenance, not coverage."""
    from copy import deepcopy
    from socialctl.saas import billing_inventory as inventory
    with p.vault.transaction() as db:
        p.vault.put(db, 'saas-stripe-event', 'orphan_event', payload, expires=1)
    with sqlite3.connect(p.vault.path) as db:
        db.execute('PRAGMA journal_mode=DELETE')
    p.add('inventory', 'inventory', inventory.inspect_database(p.vault.path, p.keys))
    lineage = deepcopy(p.evidence['lineages'][1])
    lineage.update(id='orphan_event_lineage', rows=[], record_refs=[{'kind':'saas-stripe-event','id':'orphan_event'}])
    p.evidence['lineages'].append(lineage)
    p.documentary['lineage_ids'].append(lineage['id'])
    p.documentary['record_refs'].extend(lineage['record_refs'])
    uncertainty = dict(id='orphan_event_uncertain', row_refs=[], record_refs=lineage['record_refs'],
                       reason_code='orphan_record', provenance_lineage_ids=[lineage['id']], preserved=True)
    p.evidence['uncertainties'].append(uncertainty)
    p.documentary['uncertainty_ids'].append(uncertainty['id'])
    p.objects['rehearsal']['facts']['uncertainty_ids'].append(uncertainty['id'])
    p.refresh_rehearsal()
    return p


def retained_event_payload():
    return {'id':'evt_fictional','livemode':False,'account':'acct_fictional',
            'data':{'object':{'id':'in_fictional','object':'invoice','livemode':False,
                    'customer':'cus_fictional','subscription':'sub_fictional','checkout_session':'cs_fictional',
                    'metadata':{'user_id':'owner','app':'app_fictional'}}}}


def test_review_r1_reapproved_conflicting_historical_receipt_is_denied(tmp_path, monkeypatch):
    p=Package(tmp_path,monkeypatch)
    assertion=p.objects['deployment']['facts'] | {'account_id':'acct_foreign','from_id':'foreign_empty','to_id':'foreign_current'}
    p.fact('conflicting_deployment','deployment',assertion)
    p.evidence['lineages'][0]['historical_refs']=[p.ref('conflicting_deployment')]
    p.seal(); before=frozen(p.vault.path)
    with pytest.raises(ValueError): p.prepare()
    assert frozen(p.vault.path)==before


def test_review_r1_reapproved_orphan_nested_foreign_relationships_are_denied(tmp_path,monkeypatch):
    p=Package(tmp_path,monkeypatch)
    payload=retained_event_payload()
    payload['data']['object'].update(customer='cus_foreign',subscription='sub_foreign',
                                    metadata={'user_id':'foreign_user','app':'foreign_app'})
    orphan_event_package(p,payload).seal(); before=frozen(p.vault.path)
    with pytest.raises(ValueError): p.prepare()
    assert frozen(p.vault.path)==before


def test_review_r1_reapproved_unrelated_rehearsal_hashes_are_denied(tmp_path,monkeypatch):
    p=Package(tmp_path,monkeypatch)
    p.objects['rehearsal']['facts'].update(baseline_sha256='6'*64,exact_delta_sha256='7'*64)
    p.seal(); before=frozen(p.vault.path)
    with pytest.raises(ValueError): p.prepare()
    assert frozen(p.vault.path)==before


@pytest.mark.parametrize('field', ['id','customer','subscription','checkout_session','missing_customer',
                                  'user','app','app_id','nested_metadata','nested_subscription','missing_object'])
def test_review_r1_each_orphan_relationship_is_checked_independently(tmp_path,monkeypatch,field):
    p=Package(tmp_path,monkeypatch); payload=retained_event_payload(); obj=payload['data']['object']
    if field in {'id','customer','subscription','checkout_session'}: obj[field]='foreign_object'
    elif field=='missing_customer': obj.pop('customer')
    elif field=='missing_object': payload['data'].pop('object')
    elif field=='nested_metadata': obj['subscription_details']={'metadata':{'user_id':'foreign_user'}}
    elif field=='nested_subscription': obj['parent']={'subscription_details':{'subscription':'sub_foreign'}}
    else: obj['metadata']['user_id' if field=='user' else field]='foreign_value'
    orphan_event_package(p,payload).seal()
    with pytest.raises(ValueError): p.prepare()


def test_review_r1_consistent_orphan_event_and_distinct_prepared_copy_succeed(tmp_path,monkeypatch):
    p=orphan_event_package(Package(tmp_path,monkeypatch),retained_event_payload()); p.seal()
    copied=p.objects['rehearsal']['facts']['prepared_copy']
    assert copied['source_target']['inode']!=copied['target']['inode']
    assert copied['source_baseline']['state_sha256']!=copied['baseline']['state_sha256']
    assert {change['table'] for change in copied['changes']} >= {'saas_users','saas_restore_guard','saas_google_revocations'}
    p.prepare(); assert p.apply()['status']=='one_binding_insert_verified'


def bind_rehearsal_compatibility(p):
    """Re-approve changed documentary bytes, including their compatibility link."""
    p.add('rehearsal','rehearsal',p.objects['rehearsal'])
    p.objects['compatibility']['facts']['rehearsal_sha256']=p.digest('rehearsal')
    p.seal()


@pytest.mark.parametrize('field',['source_target','source_authority','source_root','copy_identity',
                                  'missing_change','extra_change','wrong_reason','post_count','post_other_table',
                                  'compatibility_root','compatibility_ciphertext','compatibility_target'])
def test_review_r1_resealed_unrelated_copy_preparation_or_delta_is_denied(tmp_path,monkeypatch,field):
    p=Package(tmp_path,monkeypatch)
    rehearsal=p.objects['rehearsal']['facts']; copy=rehearsal['prepared_copy']; delta=rehearsal['enrollment_delta']
    comp=p.objects['compatibility']['facts']
    if field=='source_target': copy['source_target']=copy['source_target']|{'inode':0}
    elif field=='source_authority': copy['source_authority_id']='unrelated_authority'
    elif field=='source_root': copy['source_baseline']['state_sha256']='a'*64
    elif field=='copy_identity': copy['identity']['inode']+=1
    elif field=='missing_change': copy['changes'].pop()
    elif field=='extra_change': copy['changes'].append(copy['changes'][0])
    elif field=='wrong_reason': copy['changes'][0]['reason']='deletion_authority'
    elif field=='post_count': next(t for t in delta['post']['tables'] if t['name']=='records')['count']+=1
    elif field=='post_other_table': next(t for t in delta['post']['tables'] if t['name']=='rates')['sha256']='a'*64
    elif field=='compatibility_root': comp['after']['state_sha256']='a'*64
    elif field=='compatibility_ciphertext': comp['binding_ciphertext_sha256']='a'*64
    elif field=='compatibility_target': comp['copy_target']=comp['copy_target']|{'inode':0}
    # Re-bind the structured delta hash, so a fresh detached approval cannot
    # turn contradictory contents into an eligible rehearsal.
    rehearsal['exact_delta_sha256']=api().sha256(api().encode(p.s.RehearsalDelta.model_validate(delta)))
    bind_rehearsal_compatibility(p)
    with pytest.raises(ValueError): p.prepare()


@pytest.mark.parametrize('table',['saas_entitlements','records'])
def test_review_r1_preparation_reason_cannot_authorize_unrelated_access_or_ciphertext_changes(tmp_path,monkeypatch,table):
    p=Package(tmp_path,monkeypatch)
    from socialctl.saas import billing_inventory as inventory
    rehearsal=p.objects['rehearsal']['facts']; copy=rehearsal['prepared_copy']; delta=rehearsal['enrollment_delta']
    # Forge internally consistent hashes for a different prepared table, while
    # retaining this source's actual authority and original inventory root.
    for root in [copy['baseline'],delta['post_excluding_binding'],delta['post']]:
        target=next(t for t in root['tables'] if t['name']==table); target['sha256']='d'*64
        logical={t['name']:{'rows':t['count'],'digest':t['sha256'],
            'columns':p.objects['inventory']['tables'][t['name']]['columns'] if t['name']!='saas_restore_guard'
            else ['prepared','singleton','snapshot_created','state']} for t in root['tables']}
        root['state_sha256']=inventory._state_root(root['schema_sha256'],inventory._data_root(logical),copy['identity'])
    old=next(t for t in copy['source_baseline']['tables'] if t['name']==table)
    new=next(t for t in copy['baseline']['tables'] if t['name']==table)
    copy['changes']=[c for c in copy['changes'] if c['table']!=table]
    copy['changes'].append(dict(table=table,before=old,after=new,
                               reason='access_cleanup' if table=='records' else 'deletion_authority'))
    rehearsal['baseline_sha256']=copy['baseline']['state_sha256']
    rehearsal['exact_delta_sha256']=api().sha256(api().encode(p.s.RehearsalDelta.model_validate(delta)))
    p.objects['compatibility']['facts'].update(before=delta['post'],after=delta['post'])
    bind_rehearsal_compatibility(p)
    with pytest.raises(ValueError): p.prepare()


@pytest.mark.parametrize('change',['session_cleanup','terminal_revocation','grant_tombstone','user_tombstone'])
def test_review_r1_exact_existing_restore_cleanup_is_allowed_only_on_rehearsal_copy(tmp_path,monkeypatch,change):
    from socialctl.saas import billing_inventory as inventory
    p=Package(tmp_path,monkeypatch)
    with p.vault.transaction() as db:
        if change=='session_cleanup':
            p.vault.put(db,'saas-session','fictional_session',{'user_id':'owner'},expires=p.now+100)
        elif change=='terminal_revocation':
            p.vault.put(db,'google-revocation','revoked',{'user_id':'deleting'},expires=p.now+100)
        elif change=='grant_tombstone':
            db.execute("INSERT INTO saas_deletion_tombstones VALUES('grant','unrelated_grant',?)",(p.now-1,))
        else:
            db.execute("INSERT INTO saas_deletion_tombstones VALUES('user','owner',?)",(p.now-1,))
    with sqlite3.connect(p.vault.path) as db: db.execute('PRAGMA journal_mode=DELETE')
    p.add('inventory','inventory',inventory.inspect_database(p.vault.path,p.keys))
    p.refresh_rehearsal();p.seal()
    original=p.objects['inventory']['state_root']
    p.prepare();assert p.apply()['status']=='one_binding_insert_verified'
    with api().database(p.vault.path,p.keys) as snapshot:
        assert api().transaction_root(snapshot.db,snapshot.schema,snapshot.identity,exclude_binding=True).state_sha256==original


def test_review_r2_deployed_source_actual_restore_acquires_only_known_copy_schema(tmp_path,monkeypatch):
    from socialctl.saas import billing_inventory as inventory
    p=Package(tmp_path,monkeypatch)
    with p.vault.transaction() as db:
        db.execute('DROP TRIGGER records_meta_webhook_grant_delete')
        db.execute('DROP INDEX saas_record_links_grant')
        p.vault.put(db,'saas-meta-webhook-event','fictional_linked_event',
                    {'user_id':'owner','grant_id':'unrelated_grant'},expires=p.now+100)
        db.execute("INSERT INTO saas_deletion_tombstones VALUES('grant','unrelated_grant',?)",(p.now-1,))
    with sqlite3.connect(p.vault.path) as db: db.execute('PRAGMA journal_mode=DELETE')
    source=inventory.inspect_database(p.vault.path,p.keys)
    assert source['schema_variant']=='schema10_deployed_rc15'
    p.add('inventory','inventory',source)
    p.refresh_rehearsal();p.seal()
    assert p.objects['rehearsal']['facts']['prepared_copy']['baseline']['schema_variant']=='schema10_candidate_receiver'
    p.prepare();assert p.apply()['status']=='one_binding_insert_verified'
    with api().database(p.vault.path,p.keys) as snapshot:
        assert snapshot.root.schema_variant=='schema10_deployed_rc15'
        assert api().transaction_root(snapshot.db,snapshot.schema,snapshot.identity,exclude_binding=True).state_sha256==source['state_root']


@pytest.mark.parametrize('ddl',[
    'CREATE INDEX unreviewed_copy_index ON rates(bucket)',
    'CREATE TRIGGER unreviewed_copy_trigger AFTER INSERT ON rates BEGIN SELECT 1; END',
])
def test_review_r2_resealed_unknown_copy_schema_is_refused(tmp_path,monkeypatch,ddl):
    from socialctl.saas import billing_inventory as inventory
    p=Package(tmp_path,monkeypatch)
    rehearsal=p.objects['rehearsal']['facts']; copy=rehearsal['prepared_copy']; delta=rehearsal['enrollment_delta']
    copy_path=Path(copy['target']['database_path'])
    # Even actual, consistently encoded copy roots cannot authorize extra DDL.
    # Bypass collection's schema gate only to model re-approved false assertions.
    with sqlite3.connect(copy_path) as db:
        db.execute(ddl)
        schema=db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()
        for exclude in (True,False):
            tables,schema_root,data_root=inventory._logical_state(db,schema,exclude_binding=exclude)
            root=api().root_from_inventory(dict(tables=tables,schema_root=schema_root,
                state_root=inventory._state_root(schema_root,data_root,copy['identity']),
                schema_variant='schema10_candidate_receiver')).model_dump()
            if exclude:
                copy['baseline']=root;delta['post_excluding_binding']=root
            else:
                delta['post']=root
    with pytest.raises(ValueError): inventory.inspect_database(copy_path,p.keys)
    rehearsal['baseline_sha256']=copy['baseline']['state_sha256']
    rehearsal['exact_delta_sha256']=api().sha256(api().encode(p.s.RehearsalDelta.model_validate(delta)))
    p.objects['compatibility']['facts'].update(before=delta['post'],after=delta['post'])
    bind_rehearsal_compatibility(p)
    with pytest.raises(ValueError): p.prepare()


def enrollment_cli(p, monkeypatch, command, destination):
    """Actual script/CLI boundary with independently generated fictional custody."""
    import runpy
    import sys
    monkeypatch.setattr(sys, 'argv', ['enroll_legacy_test_billing.py', command])
    monkeypatch.setenv('SOCIALCLI_BILLING_ENROLLMENT_POLICY', str(p.policy_path))
    monkeypatch.setenv('SOCIALCLI_BILLING_ENROLLMENT_IMAGE_DIGEST', p.image_digest)
    monkeypatch.setenv('SOCIALCLI_BILLING_ENROLLMENT_DESTINATION', str(destination))
    monkeypatch.delenv('SOCIALCLI_ENCRYPTION_KEYS_FILE', raising=False)
    monkeypatch.setenv('SOCIALCLI_ENCRYPTION_KEYS', ','.join(key.decode() for key in p.keys))
    with pytest.raises(SystemExit) as caught:
        runpy.run_path(str(api().source_paths()['enroll_legacy_test_billing.py']), run_name='__main__')
    return caught.value.code


def test_cli_receipt_failure_retains_uncertainty_exit_two_and_readonly_recovery(tmp_path, monkeypatch, capsys):
    p = Package(tmp_path, monkeypatch); p.prepare()
    write = api().write_private
    def refuse(*args, **kwargs):
        raise OSError('fictional-secret-never-export')
    monkeypatch.setattr(api(), 'write_private', refuse)
    assert enrollment_cli(p, monkeypatch, 'apply', p.directory / 'receipt.json') == 2
    output = capsys.readouterr()
    assert output.err == ''
    assert json.loads(output.out) == {'status': 'uncertain', 'diagnostic': {
        'stage': 'apply_receipt', 'error_code': 'completion_unverified'}}
    monkeypatch.setattr(api(), 'write_private', write)
    before = frozen(p.vault.path)
    assert enrollment_cli(p, monkeypatch, 'recover', p.directory / 'recovery.json') == 2
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert output.err == ''
    assert result['status'] == 'uncertain' and result['delta'] == 'exact_delta_verified'
    assert result['diagnostic'] == {'stage': 'recover', 'error_code': 'receipt_missing'}
    assert result['rows_added'] == 1
    assert not (p.directory / 'recovery.json').exists()
    assert frozen(p.vault.path) == before
    assert enrollment_cli(p, monkeypatch, 'apply', p.directory / 'receipt.json') == 1
    output = capsys.readouterr()
    assert json.loads(output.out) == {'status': 'failed', 'error': api().ERROR,
        'diagnostic': {'stage': 'apply_precommit', 'error_code': 'precommit_rejected'}}
    assert output.err == '' and frozen(p.vault.path) == before


@pytest.mark.parametrize(('command', 'function', 'stage', 'code'), [
    ('apply', 'load_runtime', 'runtime_load', 'runtime_load_failed'),
    ('apply', 'runtime_keys', 'cli_preparation', 'operation_preparation_failed'),
    ('prepare', 'prepare', 'prepare', 'preparation_failed'),
    ('verify', 'verify', 'verify', 'verification_failed'),
    ('recover', 'recover', 'recover', 'recovery_failed'),
])
def test_cli_failed_boundaries_are_fixed_and_preserve_database(tmp_path, monkeypatch, capsys,
                                                              command, function, stage, code):
    p = Package(tmp_path, monkeypatch); p.prepare()
    before = frozen(p.vault.path)
    def refuse(*args, **kwargs):
        raise ValueError('fictional-secret-never-export')
    monkeypatch.setattr(api(), function, refuse)
    assert enrollment_cli(p, monkeypatch, command, p.directory / 'unused.json') == 1
    output = capsys.readouterr()
    assert output.err == ''
    assert json.loads(output.out) == {'status': 'failed', 'error': api().ERROR,
        'diagnostic': {'stage': stage, 'error_code': code}}
    assert frozen(p.vault.path) == before
    assert not (p.directory / 'unused.json').exists()


def test_cli_recovery_baseline_mismatch_is_not_reclassified_as_apply_failure(tmp_path, monkeypatch, capsys):
    p = Package(tmp_path, monkeypatch); p.prepare(); p.apply()
    with sqlite3.connect(p.vault.path) as db:
        db.execute('UPDATE rates SET count=count+1')
    before = frozen(p.vault.path)
    assert enrollment_cli(p, monkeypatch, 'recover', p.directory / 'recovery.json') == 1
    output = capsys.readouterr()
    assert output.err == ''
    assert json.loads(output.out) == {'status': 'baseline_mismatch', 'diagnostic': {
        'stage': 'recover', 'error_code': 'baseline_mismatch'}}
    assert frozen(p.vault.path) == before
    assert not (p.directory / 'recovery.json').exists()


def test_wal_apply_keeps_committed_writer_alive_through_independent_readback(tmp_path, monkeypatch):
    """Last-holder lifecycle regression, not historical incident attribution."""
    p = Package(tmp_path, monkeypatch); p.prepare()
    connect = sqlite3.connect
    keeper = connect(p.vault.path)
    assert keeper.execute('PRAGMA journal_mode=WAL').fetchone() == ('wal',)
    keeper.execute('SELECT count(*) FROM records').fetchone()
    events = []
    class Connection:
        def __init__(self, connection, mode):
            self.connection = connection; self.mode = mode
        def __getattr__(self, name): return getattr(self.connection, name)
        def execute(self, sql, *args, **kwargs):
            result = self.connection.execute(sql, *args, **kwargs)
            if sql == 'COMMIT':
                events.append('commit')
                keeper.close()  # Only the enrollment writer now keeps WAL alive.
            if sql == 'BEGIN' and self.mode == 'ro':
                assert self.connection.execute('PRAGMA query_only').fetchone() == (1,)
                events.append('readback')
            return result
        def close(self):
            events.append('close_' + self.mode)
            return self.connection.close()
    def wrapped(path, *args, **kwargs):
        return Connection(connect(path, *args, **kwargs), 'ro' if 'mode=ro' in str(path) else 'rw')
    monkeypatch.setattr(api().sqlite3, 'connect', wrapped)
    try:
        result = p.apply()
    finally:
        keeper.close()
        monkeypatch.setattr(api().sqlite3, 'connect', connect)
    assert result['status'] == 'one_binding_insert_verified'
    assert 'diagnostic' not in result
    assert events == ['commit', 'readback', 'close_ro', 'close_rw']
    p.register_receipt()
    receipt = p.s.parse_artifact((p.directory / 'receipt.json').read_bytes(), p.s.Receipt)
    assert receipt.committed_at == p.now
    assert receipt.post_excluding_binding == receipt.baseline
    assert not Path(str(p.vault.path) + '-wal').exists()
    assert not Path(str(p.vault.path) + '-shm').exists()
    before = frozen(p.vault.path)
    # The existing no-sidecar-creation custody guard is unchanged for later reads.
    with pytest.raises(api().EnrollmentError):
        api().verify(p.runtime(), p.keys, p.directory / 'verification.json')
    assert frozen(p.vault.path) == before
    # Ordinary SQLite ownership supplies sidecars for a subsequent verification.
    observer = connect(p.vault.path)
    try:
        observer.execute('SELECT count(*) FROM records').fetchone()
        verified = api().verify(p.runtime(), p.keys, p.directory / 'verification.json')
        assert verified['status'] == 'exact_delta_verified'
    finally:
        observer.close()


@pytest.mark.parametrize('sidecar', ['-wal', '-shm'])
@pytest.mark.parametrize('fault', ['symlink', 'hardlink', 'mode', 'missing'])
def test_wal_unsafe_or_single_missing_sidecar_still_rejects_before_commit(tmp_path, monkeypatch,
                                                                       sidecar, fault):
    p = Package(tmp_path, monkeypatch); p.prepare()
    keeper = sqlite3.connect(p.vault.path)
    try:
        keeper.execute('PRAGMA journal_mode=WAL')
        keeper.execute('SELECT count(*) FROM records').fetchone()
        path = Path(str(p.vault.path) + sidecar)
        if fault == 'symlink':
            original = path.with_name(path.name + '.original')
            path.rename(original); path.symlink_to(original)
        elif fault == 'hardlink':
            os.link(path, path.with_name(path.name + '.alias'))
        elif fault == 'mode':
            path.chmod(0o640)
        else:
            path.unlink()
        before = frozen(p.vault.path)
        with pytest.raises(api().EnrollmentError) as caught:
            p.apply()
        assert caught.value.stage == 'apply_precommit'
        assert frozen(p.vault.path) == before
        assert keeper.execute("SELECT count(*) FROM records WHERE kind='saas-billing-environment'").fetchone() == (0,)
        assert not (p.directory / 'receipt.json').exists()
    finally:
        keeper.close()


@pytest.mark.parametrize('mode', ['ro', 'rw'])
def test_connection_cleanup_failure_after_receipt_stays_uncertain(tmp_path, monkeypatch, mode):
    p = Package(tmp_path, monkeypatch); p.prepare()
    connect = sqlite3.connect
    class Connection:
        def __init__(self, connection, selected):
            self.connection = connection; self.selected = selected
        def __getattr__(self, name): return getattr(self.connection, name)
        def close(self):
            self.connection.close()
            if self.selected:
                raise OSError('fictional-private-cleanup-message')
    def wrapped(path, *args, **kwargs):
        selected = ('mode=ro' in str(path)) == (mode == 'ro')
        return Connection(connect(path, *args, **kwargs), selected)
    monkeypatch.setattr(api().sqlite3, 'connect', wrapped)
    assert p.apply() == {'status': 'uncertain', 'diagnostic': {
        'stage': 'apply_cleanup', 'error_code': 'completion_unverified'}}
    monkeypatch.setattr(api().sqlite3, 'connect', connect)
    p.register_receipt()
    assert api().verify(p.runtime(), p.keys, p.directory / 'verification.json')['status'] == 'exact_delta_verified'


def test_postcommit_baseline_mismatch_is_uncertain_not_precommit_rejection(tmp_path, monkeypatch):
    p = Package(tmp_path, monkeypatch); p.prepare()
    validate = api().validate_manifest
    calls = []
    def mismatch(*args, **kwargs):
        calls.append(1)
        if len(calls) == 3:  # Independent readback, following pre/post-insert validation.
            raise api().BaselineMismatch('fictional-private-mismatch-message')
        return validate(*args, **kwargs)
    monkeypatch.setattr(api(), 'validate_manifest', mismatch)
    assert p.apply() == {'status': 'uncertain', 'diagnostic': {
        'stage': 'apply_readback', 'error_code': 'completion_unverified'}}
    monkeypatch.setattr(api(), 'validate_manifest', validate)
    recovered = api().recover(p.runtime(), p.keys, p.directory / 'recovery.json')
    assert recovered['status'] == 'uncertain' and recovered['delta'] == 'exact_delta_verified'
    assert not (p.directory / 'receipt.json').exists()
