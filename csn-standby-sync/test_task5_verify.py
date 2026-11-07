import io
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

try:
    import task5_verify as verify
except ImportError:
    verify = None


DIGEST_A = 'a' * 64
DIGEST_B = 'b' * 64


def audit_report(digest=DIGEST_A, messages=10):
    return {'messages': messages, 'mailboxes': 2, 'duplicate_groups': 1,
            'duplicate_excess': 1, 'distribution_sha256': digest}


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(verify, 'task5_verify implementation is missing')

    def test_fixed_commands_have_pinned_ssh_options(self):
        commands = verify.fixed_commands()
        self.assertEqual(set(commands), {'old_state','new_state','sync','old_audit','new_audit'})
        for name, command in commands.items():
            self.assertEqual(command[0], '/usr/bin/ssh')
            joined = '\0'.join(command)
            self.assertIn('BatchMode=yes', joined)
            self.assertIn('IdentitiesOnly=yes', joined)
            self.assertIn('StrictHostKeyChecking=yes', joined)
            self.assertIn('ConnectTimeout=10', joined)
        self.assertEqual(commands['old_audit'][-1],
                         'sudo -n /usr/local/sbin/csn-standby-guid-audit')
        self.assertEqual(commands['new_audit'][-1],
                         '/usr/bin/python3 -B /home/mailadmin/csn-migration/standby-e2e/isolated_guid_audit.py')
        self.assertEqual(commands['sync'][-1],
                         '/usr/bin/python3 -B /home/mailadmin/csn-migration/standby-e2e/isolated_e2e.py')
        self.assertIn('/bin/systemctl',commands['old_state'][-1])
        self.assertNotIn('/usr/bin/systemctl',commands['old_state'][-1])
        self.assertIn('/usr/bin/systemctl',commands['new_state'][-1])

    def test_audit_parser_rejects_malformed_duplicate_or_extra_data(self):
        good = json.dumps(audit_report(), separators=(',', ':')).encode() + b'\n'
        self.assertEqual(verify.parse_audit(good), audit_report())
        bad = [b'', good + b'{}\n',
               b'{"messages":1,"messages":1,"mailboxes":1,"duplicate_groups":0,"duplicate_excess":0,"distribution_sha256":"' + b'a'*64 + b'"}\n',
               json.dumps(dict(audit_report(), messages=True)).encode() + b'\n',
               json.dumps(dict(audit_report(), messages=-1)).encode() + b'\n',
               json.dumps(dict(audit_report(), distribution_sha256='A'*64)).encode() + b'\n',
               json.dumps(dict(audit_report(), extra=1)).encode() + b'\n']
        for value in bad:
            with self.subTest(value=value[:40]):
                with self.assertRaises(verify.VerifyError):
                    verify.parse_audit(value)

    def test_compare_redacts_private_digest(self):
        result = verify.compare(audit_report(), audit_report())
        self.assertTrue(result['distribution_equal'])
        encoded = json.dumps(result)
        self.assertNotIn('distribution_sha256', encoded)
        self.assertNotIn(DIGEST_A, encoded)
        mismatch = verify.compare(audit_report(DIGEST_A), audit_report(DIGEST_B))
        self.assertFalse(mismatch['distribution_equal'])

    def test_sync_result_rejects_nonfinite_or_trailing_data(self):
        for value in (b'{"elapsed_seconds":NaN,"exit_code":0}\n',
                      b'{"elapsed_seconds":1,"exit_code":0}\nextra'):
            with self.assertRaises(verify.VerifyError):
                verify._parse_sync(value)

    def test_local_transport_files_require_safe_owner_type_and_modes(self):
        regular=lambda mode:types.SimpleNamespace(st_mode=stat.S_IFREG|mode,st_uid=1000)
        safe={verify.OLD_KEY:regular(0o600),verify.NEW_KEY:regular(0o600),
              verify.OLD_HOSTS:regular(0o644),verify.NEW_HOSTS:regular(0o644)}
        self.assertTrue(verify.validate_local_transport(uid=1000,lstat=safe.__getitem__))
        unsafe=dict(safe);unsafe[verify.NEW_HOSTS]=regular(0o664)
        with self.assertRaises(verify.VerifyError):
            verify.validate_local_transport(uid=1000,lstat=unsafe.__getitem__)
        unsafe=dict(safe);unsafe[verify.OLD_KEY]=types.SimpleNamespace(
            st_mode=stat.S_IFLNK|0o777,st_uid=1000)
        with self.assertRaises(verify.VerifyError):
            verify.validate_local_transport(uid=1000,lstat=unsafe.__getitem__)
        unsafe=dict(safe);unsafe[verify.NEW_KEY]=types.SimpleNamespace(
            st_mode=stat.S_IFREG|0o600,st_uid=1001)
        with self.assertRaises(verify.VerifyError):
            verify.validate_local_transport(uid=1000,lstat=unsafe.__getitem__)


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(verify, 'task5_verify implementation is missing')

    def run_sequence(self, matches):
        calls=[]
        iterator=iter(matches)
        current=[True]
        def guard(): calls.append('guard')
        def sync(): calls.append('sync')
        def old():
            calls.append('old')
            current[0]=next(iterator)
            return audit_report(DIGEST_A)
        def new():
            calls.append('new')
            return audit_report(DIGEST_A if current[0] else DIGEST_B)
        records=[]
        result=verify.run_attempts(guard=guard,sync=sync,old_audit=old,
                                   new_audit=new,record=records.append)
        return result,calls,records

    def test_match_sequences_use_at_most_three_syncs(self):
        for sequence, expected_attempts, success in [
                ([True],1,True),([False,True],2,True),
                ([False,False,True],3,True),([False,False,False],3,False)]:
            with self.subTest(sequence=sequence):
                result,calls,records=self.run_sequence(sequence)
                self.assertEqual(result['attempts'],expected_attempts)
                self.assertEqual(result['success'],success)
                self.assertEqual(calls.count('sync'),expected_attempts)
                self.assertEqual(len(records),expected_attempts)
                self.assertNotIn('distribution_sha256',json.dumps(records))

    def test_guard_error_stops_before_sync(self):
        with self.assertRaises(verify.VerifyError):
            verify.run_attempts(guard=lambda:(_ for _ in ()).throw(verify.VerifyError('guard')),
                sync=lambda:self.fail('sync called'),old_audit=lambda:audit_report(),
                new_audit=lambda:audit_report(),record=lambda value:None)


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(verify, 'task5_verify implementation is missing')

    def test_nonzero_or_stderr_is_failure(self):
        class Process:
            pid=123
            def wait(self,timeout=None):return 0
        def popen(command,**kwargs):
            kwargs['stdout'].write(b'private output');kwargs['stdout'].flush()
            kwargs['stderr'].write(b'private warning');kwargs['stderr'].flush()
            return Process()
        with self.assertRaises(verify.VerifyError) as caught:
            verify.run_fixed(verify.fixed_commands()['old_audit'],popen=popen)
        self.assertNotIn('private',str(caught.exception))

    def test_timeout_kills_process_group(self):
        class Process:
            pid=456
            def __init__(self):self.calls=0
            def wait(self,timeout=None):
                self.calls+=1
                if self.calls<3:raise subprocess.TimeoutExpired('fixed',timeout)
                return -9
        process=Process()
        with patch.object(os,'killpg') as kill:
            with self.assertRaises(verify.VerifyError):
                verify.run_fixed(verify.fixed_commands()['old_audit'],
                    popen=lambda *a,**k:process,timeout=.01,cleanup_timeout=.01)
        self.assertEqual(kill.call_args_list[0].args,(456,signal.SIGTERM))
        self.assertEqual(kill.call_args_list[1].args,(456,signal.SIGKILL))


if __name__ == '__main__':
    unittest.main()
