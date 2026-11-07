#!/usr/bin/python3
"""Prepare an isolated CSN Maildir; never replace or stop the live mailbox."""
import collections
import datetime
import hashlib
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys

SOURCE = Path('/home/mailadmin/csn-migration/Maildir-refresh')
LIVE = Path('/home/csn/Maildir')
CANDIDATE = Path('/home/csn/Maildir.ready')
PRESERVED = '.Migration-Preserved'
EXPECTED_SOURCE_DIGEST = '351fa34b473f48a539a7e04d52a2986c2134371668c6a62ae43f394bb0294823'

def inventory(root):
    if not root.is_dir() or root.is_symlink():
        raise RuntimeError('Expected a real directory: ' + str(root))
    entries = []
    def fail(error):
        raise error
    for directory, dirs, files in os.walk(str(root), onerror=fail):
        for name in dirs + files:
            path = Path(directory) / name
            entries.append((str(path.relative_to(root)), path))
    digest = hashlib.sha256()
    files = {}
    directories = set()
    for relative, path in sorted(entries):
        mode = path.lstat().st_mode
        digest.update(os.fsencode(relative) + b'\0')
        if stat.S_ISDIR(mode):
            digest.update(b'D\0')
            directories.add(relative)
        elif stat.S_ISREG(mode):
            file_digest = hashlib.sha256()
            size = 0
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    file_digest.update(block)
                    size += len(block)
            digest.update(b'F' + str(size).encode() + b'\0' + file_digest.digest())
            files[relative] = (size, file_digest.hexdigest())
        else:
            raise RuntimeError('Symlinks and special files are not allowed')
    return digest.hexdigest(), files, directories

def mail_count(files):
    return sum(Path(name).parent.name in ('cur', 'new') for name in files)

def is_index(name):
    # Only exclude metadata outside message directories.
    path = Path(name)
    return path.parent.name not in ('cur', 'new', 'tmp') and (
        path.name.startswith('dovecot.index') or path.name.startswith('dovecot.list.index'))

def active_csn(uid):
    result = subprocess.run(['/usr/bin/pgrep', '-u', str(uid)], stdout=subprocess.DEVNULL)
    if result.returncode not in (0, 1):
        raise RuntimeError('Unable to check active CSN processes')
    return result.returncode == 0

def main():
    if len(sys.argv) != 1:
        raise RuntimeError('Arguments are not permitted')
    if os.geteuid() != 0:
        raise RuntimeError('Run as root after approval')
    os.umask(0o077)
    account = pwd.getpwnam('csn')
    if account.pw_uid != 1001 or account.pw_gid != 1001:
        raise RuntimeError('Unexpected CSN UID/GID')
    if CANDIDATE.exists() or CANDIDATE.is_symlink():
        raise RuntimeError('Candidate already exists; nothing overwritten')
    if active_csn(account.pw_uid):
        raise RuntimeError('CSN processes are active; stop and review, do not terminate them')
    source_digest, source_files, source_dirs = inventory(SOURCE)
    if source_digest != EXPECTED_SOURCE_DIGEST:
        raise RuntimeError('Source differs from the verified refreshed copy')
    if PRESERVED in source_dirs:
        raise RuntimeError('Preservation folder already exists in source')
    live_digest, live_files, live_dirs = inventory(LIVE)
    if mail_count(live_files) != 1 or live_dirs != {'cur', 'new', 'tmp'}:
        raise RuntimeError('Live Maildir has changed; review before preparation')
    if any(Path(name).parent.name == 'tmp' for name in live_files):
        raise RuntimeError('Live Maildir has temporary messages; review first')
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = Path('/var/backups') / ('csn-maildir-before-' + stamp)
    backup.mkdir(mode=0o700)
    subprocess.run(['/bin/cp', '-a', str(LIVE), str(backup / 'Maildir')], check=True)
    if inventory(backup / 'Maildir')[0] != live_digest or inventory(LIVE)[0] != live_digest:
        raise RuntimeError('Backup mismatch or live mailbox changed; backup retained')
    CANDIDATE.mkdir(mode=0o700)
    # Copy everything first; rebuildable indexes are removed only in the candidate.
    subprocess.run(['/usr/bin/rsync', '-a', '--chown=csn:csn', str(SOURCE) + '/', str(CANDIDATE) + '/'], check=True)
    for relative in source_files:
        if is_index(relative):
            (CANDIDATE / relative).unlink()
    recovery = CANDIDATE / PRESERVED
    recovery.mkdir(mode=0o700)
    for sub in ('cur', 'new', 'tmp'):
        (recovery / sub).mkdir(mode=0o700)
    expected_files = {name: value for name, value in source_files.items() if not is_index(name)}
    for relative, value in live_files.items():
        if Path(relative).parent.name in ('cur', 'new') or relative == 'dovecot-keywords':
            subprocess.run(['/bin/cp', '-p', str(backup / 'Maildir' / relative), str(recovery / relative)], check=True)
            expected_files[PRESERVED + '/' + relative] = value
    # No subscription or live configuration changes; this is an offline candidate.
    for directory, dirs, files in os.walk(str(CANDIDATE)):
        os.chown(directory, account.pw_uid, account.pw_gid)
        for name in files:
            os.chown(os.path.join(directory, name), account.pw_uid, account.pw_gid)
    os.chmod(str(CANDIDATE), 0o700)
    candidate_digest, candidate_files, candidate_dirs = inventory(CANDIDATE)
    expected_dirs = source_dirs | {PRESERVED} | {PRESERVED + '/' + sub for sub in ('cur', 'new', 'tmp')}
    if candidate_files != expected_files or candidate_dirs != expected_dirs:
        raise RuntimeError('Candidate validation failed; live Maildir unchanged')
    if inventory(LIVE)[0] != live_digest:
        raise RuntimeError('Live Maildir changed during preparation; review before activation')
    report = {
        'backup': str(backup), 'candidate': str(CANDIDATE),
        'source_verified': True, 'candidate_content_verified': True,
        'live_unchanged_at_verification': True,
        'source_mail_files': mail_count(source_files),
        'preserved_mail_files': mail_count(live_files),
        'candidate_mail_files': mail_count(candidate_files),
        'candidate_tree_sha256': candidate_digest,
        'excluded_candidate_index_files': sum(is_index(name) for name in source_files),
        'ownership': 'csn:csn', 'activated': False,
    }
    (backup / 'preparation-report.json').write_text(json.dumps(report, indent=2) + '\n')

    # Validate offline storage recognition without changing the live user location.
    location = 'maildir:' + str(CANDIDATE)
    result = subprocess.run(['/usr/bin/doveadm', '-o', 'mail_location=' + location,
        '-o', 'namespace/inbox/location=' + location, '-f', 'tab', 'mailbox', 'status',
        '-u', 'csn', 'messages', '*'], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('Dovecot offline recognition failed; live Maildir unchanged')
    import csv, io
    rows = list(csv.DictReader(io.StringIO(result.stdout), delimiter='\t'))
    total = sum(int(row['messages']) for row in rows)
    current_files = inventory(CANDIDATE)[1]
    expected_messages = {k: v for k, v in expected_files.items() if Path(k).parent.name in ('cur', 'new')}
    current_messages = {k: v for k, v in current_files.items() if Path(k).parent.name in ('cur', 'new')}
    if total != mail_count(expected_files) or current_messages != expected_messages or inventory(LIVE)[0] != live_digest:
        raise RuntimeError('Recognition verification mismatch; live mailbox not switched')
    report['dovecot_messages'] = total
    report['dovecot_mailboxes'] = len(rows)
    report['dovecot_warning_lines'] = len(result.stderr.splitlines())
    (backup / 'preparation-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Preparation stopped: ' + str(error), file=sys.stderr)
        sys.exit(1)
