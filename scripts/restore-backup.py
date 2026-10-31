#!/usr/bin/env python3
"""Verify and unpack an encrypted backup into a new review directory.

This never writes a Docker volume or restores a live database. An operator
reviews the snapshot and schema compatibility before a maintenance restore.
"""
import argparse
import hmac
import os
from pathlib import Path
import subprocess
import tarfile
import shutil
from backup_auth import backup_tag
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('archive', type=Path)
parser.add_argument('destination', type=Path)
args = parser.parse_args()
key = os.environ.get('BACKUP_ENCRYPTION_KEY', '')
if not key:
    parser.error('set BACKUP_ENCRYPTION_KEY without putting its value in the command line')
tag = backup_tag(args.archive, key)
if not hmac.compare_digest(tag, args.archive.with_suffix(args.archive.suffix + '.hmac').read_text().strip()):
    parser.error('backup authentication failed; wrong key or modified archive')
os.umask(0o077)
args.destination.mkdir(mode=0o700)  # Refuse an existing directory.
with tempfile.TemporaryDirectory(dir=args.destination) as scratch:
    tarpath = Path(scratch) / 'backup.tar.gz'
    subprocess.run(['openssl', 'enc', '-d', '-aes-256-cbc', '-pbkdf2', '-in', str(args.archive), '-out', str(tarpath),
                    '-pass', 'env:BACKUP_ENCRYPTION_KEY'], check=True)
    with tarfile.open(tarpath) as tar:
        for member in tar.getmembers():
            target = (args.destination / member.name).resolve()
            if (not target.is_relative_to(args.destination.resolve()) or member.issym() or member.islnk()
                    or not (member.isfile() or member.isdir())):
                raise ValueError('invalid backup archive member')
        # Extract only verified regular files/directories, never permissions or links.
        for member in tar.getmembers():
            target = args.destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
            else:
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with tar.extractfile(member) as source, target.open('xb') as dest:
                    shutil.copyfileobj(source, dest)
print('Snapshot unpacked for review. Production volumes were not changed.')
