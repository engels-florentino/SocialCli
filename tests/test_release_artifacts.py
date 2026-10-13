"""Both archive types must exclude private work even when VCS ignores are absent."""
import io
import tarfile
import zipfile

import pytest
from scripts.release.check_artifacts import check

VERSION='0.3.0rc1'


def artifacts(tmp_path,extra=None,link=False):
    with zipfile.ZipFile(tmp_path/f'socialcli-{VERSION}-py3-none-any.whl','w') as archive:
        archive.writestr('socialctl/ui/assets/agents.md','Candidate guide')
    with tarfile.open(tmp_path/f'socialcli-{VERSION}.tar.gz','w:gz') as archive:
        for name in ['pyproject.toml','socialctl/__init__.py']+([extra] if extra else []):
            member=tarfile.TarInfo(f'socialcli-{VERSION}/'+name)
            if link and name==extra:
                member.type=tarfile.SYMTYPE;member.linkname='/private/secret';archive.addfile(member)
            else:
                value=b'fictional';member.size=len(value);archive.addfile(member,io.BytesIO(value))
    (tmp_path/'.gitignore').write_text('*\n')  # uv's output-directory marker


def test_expected_archives_produce_only_checksums(tmp_path):
    artifacts(tmp_path)
    result=check(tmp_path,VERSION)
    assert len(result)==2 and all(len(value)==64 for value in result.values())


@pytest.mark.parametrize('name',['.superpowers/private.env','other-workspace/accounts.yml',
    'socialctl/.env','socialctl/private.env','socialctl/key.pem','socialctl/data.sqlite3-wal'])
def test_source_archive_private_paths_are_rejected(tmp_path,name):
    artifacts(tmp_path,name)
    with pytest.raises(ValueError):check(tmp_path,VERSION)


def test_archive_symlink_is_rejected(tmp_path):
    artifacts(tmp_path,'socialctl/link',link=True)
    with pytest.raises(ValueError):check(tmp_path,VERSION)
