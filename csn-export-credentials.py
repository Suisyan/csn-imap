#!/usr/bin/python3
"""Encrypt only the fixed legacy fetchmail configuration; no plaintext output."""
from __future__ import print_function
import hashlib
import json
import os
from pathlib import Path
import pwd
import resource
import stat
import subprocess
import sys
import tempfile

CERT_HASH = 'b2b15bc28a2ab679e51f581bfe467227468b7c75f84e757d15c8b96eba30dd4a'
SOURCE = Path('/home/csn/.fetchmailrc')
CERT = Path('/home/umeoka/.csn-migration-recipient.pem')
OUTPUT = Path('/home/umeoka/.csn-credentials-transfer.cms')

def main():
    if os.geteuid() != 0 or len(sys.argv) != 1:
        raise RuntimeError('Invalid invocation')
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.umask(0o077)
    if os.path.lexists(str(OUTPUT)):
        raise RuntimeError('Output already exists')
    metadata = SOURCE.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != pwd.getpwnam('csn').pw_uid:
        raise RuntimeError('Unexpected source')
    if metadata.st_size > 65536 or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise RuntimeError('Unexpected source size or permissions')
    certificate = CERT.read_bytes()
    if hashlib.sha256(certificate).hexdigest() != CERT_HASH:
        raise RuntimeError('Certificate mismatch')
    with tempfile.TemporaryDirectory(prefix='csn-export-', dir='/run') as directory:
        cert = Path(directory) / 'recipient.pem'
        cert.write_bytes(certificate)
        result = subprocess.run(['/usr/bin/openssl','cms','-encrypt','-binary','-aes256','-outform','DER',str(cert)],
                                input=SOURCE.read_bytes(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode or not result.stdout:
            raise RuntimeError('Encryption failed')
        with OUTPUT.open('xb') as output:
            output.write(result.stdout)
            output.flush()
            os.fsync(output.fileno())
        owner = pwd.getpwnam('umeoka')
        os.chown(str(OUTPUT), owner.pw_uid, owner.pw_gid)
        os.chmod(str(OUTPUT), 0o600)
        print(json.dumps({'encrypted_output':str(OUTPUT),'ciphertext_bytes':len(result.stdout),
                          'ciphertext_sha256':hashlib.sha256(result.stdout).hexdigest(),
                          'source_modified':False}))

if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Never include exception contents or subprocess output in diagnostics.
        print('Credential export stopped; no plaintext diagnostics emitted.', file=sys.stderr)
        sys.exit(1)
