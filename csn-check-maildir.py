#!/usr/bin/python3
"""Check the isolated CSN Maildir without switching the live mailbox."""
import collections
import csv
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys

CANDIDATE = Path('/home/csn/Maildir.migration')
LIVE = Path('/home/csn/Maildir')
REPORT = Path('/var/backups/csn-maildir-recognition.json')

def command(args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        # The fixed mailbox commands do not request message contents or secrets.
        print(result.stderr[:3000], file=sys.stderr)
        raise RuntimeError('Command failed: ' + args[0])
    return result.stdout, result.stderr

def mail_manifest(files):
    manifest = collections.Counter()
    counts = collections.Counter()
    for name, (size, digest) in files.items():
        path = Path(name)
        if path.parent.name not in ('cur', 'new'):
            continue
        folder = str(path.parent.parent)
        flags = path.name.split(':2,', 1)[1] if ':2,' in path.name else ''
        manifest[(folder, size, digest, ''.join(sorted(flags)))] += 1
        counts[folder] += 1
    return manifest, counts

def main():
    if os.geteuid() != 0 or len(sys.argv) != 1:
        raise RuntimeError('Run as root without arguments')
    os.umask(0o077)
    loader = importlib.machinery.SourceFileLoader('csn_prepare', '/usr/local/sbin/csn-prepare-maildir')
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    # Compile from the root-owned source without generating bytecode files.
    exec(compile(Path(loader.path).read_bytes(), loader.path, 'exec'), module.__dict__)
    if module.active_csn(1001):
        raise RuntimeError('CSN processes active; stop for review')
    config, unused = command(['/usr/bin/doveconf', '-n'])
    namespaces = re.findall(r'^namespace\s+(\S+)\s*\{', config, re.M)
    if namespaces != ['inbox']:
        raise RuntimeError('Unexpected namespaces; review before checking mailboxes')
    live_before = module.inventory(LIVE)[0]
    before_files = module.inventory(CANDIDATE)[1]
    before_manifest, expected_counts = mail_manifest(before_files)
    if sum(expected_counts.values()) != 50529 or expected_counts.get('.Migration-Preserved') != 1:
        raise RuntimeError('Candidate message counts differ from prepared data')
    location = 'maildir:' + str(CANDIDATE)
    base = ['/usr/bin/doveadm', '-o', 'mail_location=' + location,
            '-o', 'namespace/inbox/location=' + location]
    names, list_warnings = command(base + ['mailbox', 'list', '-u', 'csn'])
    output, status_warnings = command(base + ['-f', 'tab', 'mailbox', 'status', '-u', 'csn',
                                             'messages unseen uidvalidity uidnext', '*'])
    rows = list(csv.DictReader(io.StringIO(output), delimiter='\t'))
    if not rows or not all('mailbox' in row and 'messages' in row for row in rows):
        raise RuntimeError('Unexpected mailbox status output format')
    total = sum(int(row['messages']) for row in rows)
    recovered = [row for row in rows if row['mailbox'] == 'Migration-Preserved']
    if total != 50529 or len(recovered) != 1 or int(recovered[0]['messages']) != 1:
        raise RuntimeError('Dovecot message counts differ from expected counts')
    after_manifest, after_counts = mail_manifest(module.inventory(CANDIDATE)[1])
    live_after = module.inventory(LIVE)[0]
    report = {'candidate': str(CANDIDATE), 'dovecot_message_total': total,
              'listed_mailboxes': len(names.splitlines()), 'status_mailboxes': len(rows),
              'preserved_messages': int(recovered[0]['messages']),
              'message_content_and_flags_unchanged': before_manifest == after_manifest,
              'mailbox_message_counts_unchanged': expected_counts == after_counts,
              'live_maildir_unchanged': live_before == live_after,
              'warning_lines': len((list_warnings + status_warnings).splitlines()),
              'mailboxes': rows, 'activated': False}
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    summary = {key: value for key, value in report.items() if key != 'mailboxes'}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not all(report[key] for key in ('message_content_and_flags_unchanged',
               'mailbox_message_counts_unchanged', 'live_maildir_unchanged')):
        raise RuntimeError('Validation mismatch; inspect report before proceeding')

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Recognition check stopped: ' + str(error), file=sys.stderr)
        sys.exit(1)
