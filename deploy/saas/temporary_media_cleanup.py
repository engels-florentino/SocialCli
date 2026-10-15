#!/usr/bin/env python3
"""Independent host cleanup. No secrets, database access or container required."""
import argparse
import os
from pathlib import Path
import re
import stat
import time


def cleanup(root, now=None):
    root=Path(root)
    if root.is_symlink() or not root.is_dir() or stat.S_IMODE(root.stat().st_mode)&0o077:
        raise ValueError('Temporary media directory must be private and real')
    now=time.time() if now is None else now
    removed=0
    for path in root.iterdir():
        match=re.fullmatch(r'([0-9]+)-[A-Za-z0-9_-]{32}\.(part|data)',path.name)
        if match and int(match[1])<=now:
            path.unlink(missing_ok=True);removed+=1
    return removed


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    args=parser.parse_args()
    try:print('Expired temporary files removed:',cleanup(args.directory))
    except (OSError,ValueError):raise SystemExit('Temporary media cleanup failed; inspect storage permissions.')
