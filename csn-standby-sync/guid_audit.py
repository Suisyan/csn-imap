#!/usr/bin/python3
"""Fixed, metadata-only Dovecot GUID distribution audit."""
from __future__ import print_function

import collections
import hashlib
import json
import os
import pwd
import signal
import struct
import subprocess
import sys
import tempfile


OLD_COMMAND = [
    '/usr/bin/doveadm', '-c', '/etc/csn-standby-source.conf',
    '-f', 'tab', 'fetch', 'mailbox guid', 'ALL'
]
ISOLATED_COMMAND = [
    '/usr/bin/doveadm', '-c',
    '/home/mailadmin/csn-migration/initial-sync-receiver/dovecot.conf',
    '-f', 'tab', 'fetch', 'mailbox guid', 'ALL'
]
MAX_LINE = 8192
TIMEOUT = 600
OUTPUT_LIMIT = 67108864


class AuditError(Exception):
    """A sanitized audit failure."""


def _valid_opaque(value):
    return (isinstance(value, str) and bool(value)
            and len(value.encode('utf-8')) <= MAX_LINE
            and not any(ord(character) < 32 or ord(character) == 127
                        for character in value))


def parse_rows(stream):
    header = stream.readline(MAX_LINE + 1)
    if header != b'mailbox\tguid\n':
        raise AuditError('invalid audit output')
    count = 0
    while True:
        line = stream.readline(MAX_LINE + 1)
        if not line:
            break
        if len(line) > MAX_LINE or not line.endswith(b'\n'):
            raise AuditError('invalid audit output')
        fields = line[:-1].split(b'\t')
        if len(fields) != 2 or not fields[0] or not fields[1]:
            raise AuditError('invalid audit output')
        if fields == [b'mailbox', b'guid']:
            raise AuditError('duplicate audit header')
        try:
            mailbox = fields[0].decode('utf-8')
            guid = fields[1].decode('utf-8')
        except (UnicodeDecodeError, UnicodeEncodeError):
            raise AuditError('invalid audit output')
        if not _valid_opaque(mailbox) or not _valid_opaque(guid):
            raise AuditError('invalid audit output')
        count += 1
        yield mailbox, guid
    if count == 0:
        raise AuditError('empty audit output')


def _framed(value):
    data = value.encode('utf-8')
    return struct.pack('>I', len(data)) + data


def aggregate(rows):
    mailboxes = collections.defaultdict(collections.Counter)
    messages = 0
    for mailbox, guid in rows:
        if not _valid_opaque(mailbox) or not _valid_opaque(guid):
            raise AuditError('invalid audit row')
        mailboxes[mailbox][guid] += 1
        messages += 1
    if not messages:
        raise AuditError('empty audit output')
    digest = hashlib.sha256()
    duplicate_groups = 0
    duplicate_excess = 0
    for mailbox in sorted(mailboxes):
        for guid in sorted(mailboxes[mailbox]):
            multiplicity = mailboxes[mailbox][guid]
            digest.update(_framed(mailbox))
            digest.update(_framed(guid))
            digest.update(struct.pack('>Q', multiplicity))
            if multiplicity > 1:
                duplicate_groups += 1
                duplicate_excess += multiplicity - 1
    return {
        'messages': messages,
        'mailboxes': len(mailboxes),
        'duplicate_groups': duplicate_groups,
        'duplicate_excess': duplicate_excess,
        'distribution_sha256': digest.hexdigest(),
    }


def encode_public(report):
    return json.dumps(report, sort_keys=True, separators=(',', ':'))


def _terminate(process, cleanup_timeout):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except OSError:
        pass
    try:
        process.wait(timeout=cleanup_timeout)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        process.wait()
    except OSError:
        pass


def run_audit(command, popen=subprocess.Popen, timeout=TIMEOUT,
              cleanup_timeout=10):
    if command not in (OLD_COMMAND, ISOLATED_COMMAND):
        raise AuditError('audit command rejected')
    if command == OLD_COMMAND:
        environment = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C',
                       'USER': 'csn', 'HOME': '/home/csn'}
    else:
        environment = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C',
                       'USER': 'mailadmin', 'HOME': '/home/mailadmin'}
    with tempfile.TemporaryFile(mode='w+b') as output:
        with tempfile.TemporaryFile(mode='w+b') as errors:
            try:
                process = popen(command, stdin=subprocess.DEVNULL,
                                stdout=output, stderr=errors,
                                start_new_session=True,
                                env=environment)
                try:
                    code = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    _terminate(process, cleanup_timeout)
                    raise AuditError('audit timed out')
                errors.seek(0, os.SEEK_END)
                error_size = errors.tell()
                output.seek(0, os.SEEK_END)
                output_size = output.tell()
                if code != 0 or error_size or output_size > OUTPUT_LIMIT:
                    raise AuditError('audit command failed')
                output.seek(0)
                return aggregate(parse_rows(output))
            except AuditError:
                raise
            except (OSError, subprocess.SubprocessError, ValueError):
                raise AuditError('audit command failed')


def audit_main(command, expected_uid, argv, geteuid=os.geteuid,
               popen=subprocess.Popen):
    if argv or geteuid() != expected_uid:
        raise AuditError('fixed account and no arguments required')
    return run_audit(command, popen=popen)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        uid = pwd.getpwnam('csn').pw_uid
        report = audit_main(OLD_COMMAND, uid, argv)
        sys.stdout.write(encode_public(report) + '\n')
        return 0
    except (AuditError, KeyError):
        sys.stderr.write('csn-standby-guid-audit: audit failed\n')
        return 1


if __name__ == '__main__':
    sys.exit(main())
