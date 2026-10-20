"""Run from outside the checkout using an installed wheel, with fictional data."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from importlib.metadata import version

from cryptography.fernet import Fernet


def main():
    import socialctl
    expected=sys.argv[1]
    assert version('socialcli')==expected
    executable=Path(sys.executable).parent/'socialcli'
    with tempfile.TemporaryDirectory(prefix='socialcli-install-smoke-') as temporary:
        root=Path(temporary).resolve();private=root/'credentials';private.mkdir(mode=0o700)
        key=private/'master.key';key.write_bytes(Fernet.generate_key());key.chmod(0o600)
        env={**os.environ,'SOCIALCLI_ROOT':str(root/'workspace')}
        def run(*args):
            result=subprocess.run([str(executable),*args],cwd=root,env=env,capture_output=True,text=True)
            assert result.returncode==0,(args,result.stderr)
            return result.stdout
        assert run('--version').strip()=='SocialCli '+expected
        run('--help');run('account','login','--help')
        for topic in ('setup','publish','schedule','inbox','errors'):
            assert 'SocialCli agent guide' in run('agent-guide',topic)
        run('brand','new','InstallSmoke')
        run('credentials','configure','--backend','encrypted-file','--store-file',str(private/'capabilities.json'),
            '--key-file',str(key),'--brand','InstallSmoke','--yes')
        from socialctl.brands import cargar_brand
        from socialctl.connections import keychain
        from socialctl.connections.account_cli import Slot
        brand=cargar_brand(root/'workspace','InstallSmoke')
        metadata={'version':1,'auth_mode':'saas-installation','service_url':'https://social.example','connection_id':'fixture-installation'}
        secret='fictional-installation-secret-not-a-provider-token'
        keychain.save(brand,Slot.ACCOUNT,metadata,secret)
        assert keychain.get(brand,Slot.ACCOUNT,metadata)==secret
        assert secret.encode() not in (private/'capabilities.json').read_bytes()
        keychain.delete(brand,Slot.ACCOUNT,metadata)
        from importlib.resources import files
        assert 'SocialCli for AI agents — ' + expected in files('socialctl.ui').joinpath('assets/agents.md').read_text()
        assert 'SocialCli' in files('socialctl.ui').joinpath('email/trial.html').read_text()
    print(json.dumps({'installed_version':expected,'platform':sys.platform,'help':True,
        'workspace':True,'encrypted_credentials':True,'packaged_instructions':True,'external_calls':0}))


if __name__=='__main__':main()
