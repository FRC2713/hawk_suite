"""Authenticate encrypted snapshots without loading them into memory."""
import hashlib
import hmac


def backup_tag(path, key):
    auth_key = hashlib.sha256(b'hawk-backup-auth\0' + key.encode()).digest()
    digest = hmac.new(auth_key, digestmod=hashlib.sha256)
    with path.open('rb') as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()
