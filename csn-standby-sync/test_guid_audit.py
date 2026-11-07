import contextlib
import io
import json
import os
import signal
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

try:
    import guid_audit as audit
except ImportError:
    audit = None


class AggregateTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(audit, 'guid_audit implementation is missing')

    def test_aggregate_redacts_identifiers_and_is_order_independent(self):
        rows = [
            ('secret-customer', 'a' * 32),
            ('secret-customer', 'a' * 32),
            ('INBOX', 'b' * 32),
        ]
        report = audit.aggregate(rows)
        self.assertEqual(report['messages'], 3)
        self.assertEqual(report['mailboxes'], 2)
        self.assertEqual(report['duplicate_groups'], 1)
        self.assertEqual(report['duplicate_excess'], 1)
        self.assertEqual(report['distribution_sha256'], audit.aggregate(reversed(rows))['distribution_sha256'])
        encoded = audit.encode_public(report)
        self.assertNotIn('secret-customer', encoded)
        self.assertNotIn('a' * 32, encoded)

    def test_parser_requires_one_header_and_valid_rows(self):
        opaque = '1712345678.M123P456.host,S=789:2,S'
        data = ('mailbox\tguid\nINBOX\t' + opaque + '\n').encode()
        self.assertEqual(list(audit.parse_rows(io.BytesIO(data))), [('INBOX', opaque)])
        bad = [
            b'',
            b'mailbox\tguid\n',
            b'mailbox\tguid\nINBOX\n',
            b'mailbox\tguid\nINBOX\tbad\x00guid\n',
            b'mailbox\tguid\nIN\x00BOX\t' + b'a' * 32 + b'\n',
            b'mailbox\tguid\nINBOX\t' + b'a' * 32 + b'\textra\n',
            b'mailbox\tguid\nmailbox\tguid\n',
            b'mailbox\tguid\n' + b'x' * 8193 + b'\t' + b'a' * 32 + b'\n',
        ]
        for value in bad:
            with self.subTest(value=value[:40]):
                with self.assertRaises(audit.AuditError):
                    list(audit.parse_rows(io.BytesIO(value)))

    def test_public_encoding_has_exact_schema(self):
        report = audit.aggregate([('INBOX', 'c' * 32)])
        decoded = json.loads(audit.encode_public(report))
        self.assertEqual(set(decoded), {
            'messages', 'mailboxes', 'duplicate_groups', 'duplicate_excess',
            'distribution_sha256'})
        self.assertRegex(decoded['distribution_sha256'], r'^[0-9a-f]{64}$')

    def test_aggregate_accepts_variable_length_opaque_guids(self):
        rows=[('INBOX','12345678'),('INBOX','opaque,guid.with:punctuation'),
              ('INBOX','opaque,guid.with:punctuation')]
        report=audit.aggregate(rows)
        self.assertEqual(report['messages'],3)
        self.assertEqual(report['duplicate_groups'],1)
        self.assertEqual(report['duplicate_excess'],1)


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(audit, 'guid_audit implementation is missing')

    def test_fixed_old_command_and_argument_identity_rejection(self):
        self.assertEqual(audit.OLD_COMMAND, [
            '/usr/bin/doveadm', '-c', '/etc/csn-standby-source.conf',
            '-f', 'tab', 'fetch', 'mailbox guid', 'ALL'])
        with self.assertRaises(audit.AuditError):
            audit.audit_main(audit.OLD_COMMAND, 1001, ['extra'], geteuid=lambda: 1001)
        with self.assertRaises(audit.AuditError):
            audit.audit_main(audit.OLD_COMMAND, 1001, [], geteuid=lambda: 0)

    def test_nonzero_or_stderr_is_failure_and_private(self):
        class Process:
            pid = 123
            returncode = 0
            def wait(self, timeout=None):
                return self.returncode
        def fake_popen(command, **kwargs):
            kwargs['stdout'].write(b'mailbox\tguid\nINBOX\t' + b'd' * 32 + b'\n')
            kwargs['stdout'].flush()
            kwargs['stderr'].write(b'private warning address@example.invalid')
            kwargs['stderr'].flush()
            return Process()
        with self.assertRaises(audit.AuditError) as caught:
            audit.run_audit(audit.OLD_COMMAND, popen=fake_popen)
        self.assertNotIn('private', str(caught.exception))
        self.assertNotIn('example', str(caught.exception))

    def test_doveadm_receives_only_fixed_account_environment(self):
        seen=[]
        class Process:
            pid=321
            def wait(self,timeout=None):return 0
        def fake_popen(command,**kwargs):
            seen.append(kwargs['env'])
            kwargs['stdout'].write(b'mailbox\tguid\nINBOX\topaque-guid\n')
            kwargs['stdout'].flush()
            return Process()
        audit.run_audit(audit.OLD_COMMAND,popen=fake_popen)
        audit.run_audit(audit.ISOLATED_COMMAND,popen=fake_popen)
        self.assertEqual(seen,[
            {'PATH':'/usr/bin:/bin','LC_ALL':'C','USER':'csn','HOME':'/home/csn'},
            {'PATH':'/usr/bin:/bin','LC_ALL':'C','USER':'mailadmin','HOME':'/home/mailadmin'}])

    def test_timeout_terminates_and_reaps_process_group(self):
        class Process:
            pid = 456
            returncode = None
            def __init__(self):
                self.calls = 0
            def wait(self, timeout=None):
                self.calls += 1
                if self.calls < 3:
                    raise subprocess.TimeoutExpired('fixed', timeout)
                self.returncode = -9
                return -9
        process = Process()
        with patch.object(os, 'killpg') as kill:
            with self.assertRaises(audit.AuditError):
                audit.run_audit(audit.OLD_COMMAND, popen=lambda *a, **k: process,
                                timeout=0.01, cleanup_timeout=0.01)
        self.assertEqual(kill.call_args_list[0].args, (456, signal.SIGTERM))
        self.assertEqual(kill.call_args_list[1].args, (456, signal.SIGKILL))
        self.assertEqual(process.calls, 3)

    def test_isolated_wrapper_has_fixed_command(self):
        import isolated_guid_audit as isolated
        self.assertEqual(isolated.ISOLATED_COMMAND, [
            '/usr/bin/doveadm', '-c',
            '/home/mailadmin/csn-migration/initial-sync-receiver/dovecot.conf',
            '-f', 'tab', 'fetch', 'mailbox guid', 'ALL'])


if __name__ == '__main__':
    unittest.main()
