"""Reject unintended files in both release formats before any upload."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile
import zipfile


def safe(name):
    path=PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts:return False
    if any(p in {'.superpowers','.secrets','.venv','.worktrees','__pycache__'} for p in path.parts):return False
    if any(p=='.env' or p.startswith('.env.') for p in path.parts):return False
    if path.name.endswith('.env') or '.sqlite3' in path.name:return False
    if path.suffix in {'.pyc','.key','.pem','.sqlite3','.mp4','.mov','.wav','.mp3'}:return False
    return True


def check(directory,version):
    directory=Path(directory)
    wheel=directory/f'socialcli-{version}-py3-none-any.whl'
    source=directory/f'socialcli-{version}.tar.gz'
    if {p for p in directory.iterdir() if p.name!='.gitignore'}!={wheel,source}:raise ValueError('Only the exact wheel and sdist may be released.')
    with zipfile.ZipFile(wheel) as archive:
        names=archive.namelist()
        if len(names)!=len(set(names)):raise ValueError('Duplicate archive paths.')
        for info in archive.infolist():
            if (not safe(info.filename) or PurePosixPath(info.filename).parts[0] not in
                {'socialctl',f'socialcli-{version}.dist-info'} or (info.external_attr>>16)&0o170000==0o120000):
                raise ValueError('Wheel contains an unexpected path or symlink.')
        if 'socialctl/ui/assets/agents.md' not in names:raise ValueError('Agent instructions missing from wheel.')
    with tarfile.open(source) as archive:
        names=archive.getnames()
        if len(names)!=len(set(names)):raise ValueError('Duplicate archive paths.')
        for member in archive.getmembers():
            path=PurePosixPath(member.name)
            parts=path.parts
            if (not safe(member.name) or not member.isfile() or len(parts)<2 or
                parts[0]!=f'socialcli-{version}' or parts[1] not in
                {'socialctl','pyproject.toml','README.md','LICENSE','PKG-INFO','.gitignore'}):
                raise ValueError('Source distribution contains an unexpected file or link.')
    return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [wheel,source]}


if __name__=='__main__':
    try:print(json.dumps(check(sys.argv[1],sys.argv[2]),sort_keys=True))
    except (ValueError,OSError,tarfile.TarError,zipfile.BadZipFile):
        raise SystemExit('Release artifact verification failed; do not publish.') from None
