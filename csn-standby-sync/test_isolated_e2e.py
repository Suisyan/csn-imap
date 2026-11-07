import contextlib
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
    import isolated_e2e as m
except ImportError:
    m = None

class IsolatedTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(m, 'isolated_e2e implementation is missing')

    def test_fixed_command(self):
        self.assertEqual(m.build_command(), ['/usr/bin/doveadm','-c','/home/mailadmin/csn-migration/initial-sync-receiver/dovecot.conf','backup','-R','-l','60','-T','600','/usr/bin/ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile=/home/mailadmin/csn-migration/standby-sync-staging/known_hosts','-i','/home/mailadmin/csn-migration/standby-sync-staging/id_ed25519','umeoka@118.27.32.100'])

    def test_target_rejects_other_missing_symlink_owner(self):
        good=types.SimpleNamespace(st_mode=stat.S_IFDIR|0o700,st_uid=1000)
        with self.assertRaises(RuntimeError): m.validate_target('/home/csn/Maildir', uid=1000, lstat=lambda p:good)
        def missing(p): raise FileNotFoundError()
        with self.assertRaises(RuntimeError): m.validate_target(uid=1000,lstat=missing)
        for entry in [types.SimpleNamespace(st_mode=stat.S_IFLNK|0o700,st_uid=1000),types.SimpleNamespace(st_mode=stat.S_IFDIR|0o700,st_uid=1001)]:
            with self.assertRaises(RuntimeError): m.validate_target(uid=1000,lstat=lambda p:entry)
        m.validate_target(uid=1000,lstat=lambda p:types.SimpleNamespace(st_mode=(stat.S_IFREG|0o600) if p==m.CONFIG else good.st_mode,st_uid=1000))

    def test_transport_files_require_safe_owned_regular_files(self):
        directory = types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=1000)
        key = types.SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=1000)
        known_hosts = types.SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_uid=1000)
        def safe(path):
            if path == m.PRIVATE_KEY:
                return key
            if path == m.KNOWN_HOSTS:
                return known_hosts
            return directory
        self.assertTrue(m.validate_transport_files(uid=1000, lstat=safe))
        with self.assertRaises(RuntimeError):
            m.validate_transport_files(uid=1000, lstat=lambda path: types.SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=1000))
        with self.assertRaises(RuntimeError):
            m.validate_transport_files(uid=1000, lstat=lambda path: types.SimpleNamespace(st_mode=stat.S_IFREG | 0o640, st_uid=1000) if path == m.PRIVATE_KEY else safe(path))
        with self.assertRaises(RuntimeError):
            m.validate_transport_files(uid=1000, lstat=lambda path: types.SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=1001) if path == m.PRIVATE_KEY else safe(path))

    def test_target_tree_rejects_any_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'cur').mkdir()
            (root / 'new').mkdir()
            (root / 'tmp').mkdir()
            self.assertTrue(m.validate_no_symlinks(str(root)))
            (root / 'cur' / 'escape').symlink_to('/tmp')
            with self.assertRaises(RuntimeError):
                m.validate_no_symlinks(str(root))

    def test_target_tree_traversal_error_fails_closed(self):
        def failed_walk(root, *, topdown, followlinks, onerror):
            onerror(PermissionError('private path'))
            return iter(())
        with patch.object(m.os, 'walk', side_effect=failed_walk):
            with self.assertRaises(RuntimeError):
                m.validate_no_symlinks('/fixed')

    def test_report_never_contains_stderr(self):
        secret=b'Subject: PRIVATE body address@example.invalid protocol GUID'
        report=m.make_report(2,3.5,secret)
        data=json.dumps(report)
        self.assertNotIn('PRIVATE',data)
        self.assertNotIn('address',data)
        self.assertEqual(report['stderr_bytes'],len(secret))
        self.assertFalse(report['complete'])
        self.assertTrue(m.make_report(0,1,b'')['complete'])
        self.assertFalse(m.make_report(0,1,b'warning')['complete'])
        self.assertFalse(m.make_report(124,1,b'')['complete'])

    def test_public_result_contains_only_exit_and_elapsed(self):
        report = {'exit_code': 0, 'elapsed_seconds': 1.25, 'before': {'messages': 99},
                  'new_services_before': {'secret': 'state'}, 'stderr_sha256': 'private'}
        self.assertEqual(m.public_result(report), {'exit_code': 0, 'elapsed_seconds': 1.25})

    def test_timeout_terminates_kills_and_reaps_group(self):
        class Process:
            pid=456
            returncode=None
            def __init__(self): self.calls=0
            def wait(self,timeout=None):
                self.calls+=1
                if self.calls<3: raise subprocess.TimeoutExpired('fixed',timeout)
                self.returncode=-9
                return -9
        p=Process()
        with tempfile.TemporaryFile() as stderr, patch.object(m.os,'killpg') as kill, patch.object(m.time,'monotonic',side_effect=[0,0,1]):
            rc=m.run_backup(stderr,popen=lambda *a,**k:p,timeout=.01,cleanup_timeout=.01,progress=lambda x:None)
        self.assertEqual(rc,124)
        self.assertEqual(kill.call_args_list[0].args,(456,signal.SIGTERM))
        self.assertEqual(kill.call_args_list[1].args,(456,signal.SIGKILL))
        self.assertEqual(p.calls,3)

    def test_service_states_enforced(self):
        good={'fetchmail-csn.service':'inactive','fetchmail-csn.timer':'disabled','csn-standby-sync.service':'inactive','csn-standby-sync.timer':'disabled','csn-standby-sync.timer.active':'inactive'}
        self.assertEqual(m.check_services(lookup=good.__getitem__, armed_lookup=lambda:'absent')['armed_check'],'absent')
        for key in good:
            bad=dict(good);bad[key]='active'
            with self.assertRaises(RuntimeError):m.check_services(lookup=bad.__getitem__, armed_lookup=lambda:'absent')

    def test_armed_status_uses_only_fixed_sudo_command(self):
        self.assertEqual(m.ARMED_COMMAND,
                         ['/usr/bin/sudo','-n','/usr/local/sbin/csn-standby-armed-status'])
        calls=[]
        def run(command, **kwargs):
            calls.append((command,kwargs))
            return types.SimpleNamespace(returncode=0,stdout=b'absent\n',stderr=b'')
        self.assertEqual(m.armed_state(run=run),'absent')
        self.assertEqual(calls[0][0],m.ARMED_COMMAND)
        self.assertNotIn('shell',calls[0][1])

    def test_armed_status_fails_closed(self):
        cases=[
            types.SimpleNamespace(returncode=1,stdout=b'',stderr=b''),
            types.SimpleNamespace(returncode=0,stdout=b'present\n',stderr=b''),
            types.SimpleNamespace(returncode=0,stdout=b'absent\nextra\n',stderr=b''),
            types.SimpleNamespace(returncode=0,stdout=b'absent\n',stderr=b'warning'),
            types.SimpleNamespace(returncode=0,stdout=b'\xff',stderr=b''),
        ]
        for result in cases:
            with self.subTest(result=result):
                with self.assertRaises(RuntimeError):
                    m.armed_state(run=lambda *a, result=result, **k:result)
        with self.assertRaises(RuntimeError):
            m.armed_state(run=lambda *a,**k:(_ for _ in ()).throw(subprocess.TimeoutExpired('fixed',15)))

    def test_lock_private_nofollow_nonblocking(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'run.lock'
            fd=m.acquire_lock(str(path),uid=os.getuid())
            try:
                self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
                with self.assertRaises(RuntimeError):m.acquire_lock(str(path),uid=os.getuid())
            finally:os.close(fd)
            path.unlink();path.symlink_to(Path(temp)/'elsewhere')
            with self.assertRaises(RuntimeError):m.acquire_lock(str(path),uid=os.getuid())

    def test_status_sums_without_revealing_mailboxes(self):
        result=types.SimpleNamespace(returncode=0,stdout=b'mailbox\tmessages\nsecret-folder\t3\nINBOX\t4\n',stderr=b'')
        calls=[]
        def run(cmd,**kwargs):calls.append(cmd);return result
        self.assertEqual(m.mailbox_status(run=run),{'messages':7,'mailboxes':2})
        self.assertNotIn('-u',calls[0])
        result.stderr=b'warning private'
        with self.assertRaises(RuntimeError):m.mailbox_status(run=run)

    def test_cli_rejects_arguments_without_running(self):
        with patch.object(m,'run_sync') as sync,contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(m.main(['anything']),64)
        sync.assert_not_called()

class ContainmentTests(unittest.TestCase):
    def test_config_contains_only_isolated_paths(self):
        safe='mail_home = /home/mailadmin/csn-migration/initial-sync-receiver\nmail_location = maildir:~/Maildir\n'
        with self.assertRaises(RuntimeError):m.validate_config(safe)
        safe='mail_home = /home/mailadmin/csn-migration/initial-sync-receiver\nmail_location = maildir:/home/mailadmin/csn-migration/initial-sync-receiver/Maildir\n'
        self.assertTrue(m.validate_config(safe))
        for extra in ['!include /etc/dovecot.conf','mail_home = /home/csn','mail_location = maildir:%h/Maildir','mail_location = maildir:/home/mailadmin/csn-migration/initial-sync-receiver/../bad']:
            with self.assertRaises(RuntimeError):m.validate_config(safe+extra+'\n')

    def test_armed_marker_rejected(self):
        good={'fetchmail-csn.service':'inactive','fetchmail-csn.timer':'disabled','csn-standby-sync.service':'inactive','csn-standby-sync.timer':'disabled','csn-standby-sync.timer.active':'inactive'}
        with self.assertRaises(RuntimeError):m.check_services(lookup=good.__getitem__,armed_lookup=lambda:'present')

class PermissionLimitedTests(unittest.TestCase):
    def test_permission_limited_marker_cannot_pass(self):
        good={'fetchmail-csn.service':'inactive','fetchmail-csn.timer':'disabled','csn-standby-sync.service':'inactive','csn-standby-sync.timer':'disabled','csn-standby-sync.timer.active':'inactive'}
        def inaccessible():raise RuntimeError('unavailable')
        with self.assertRaises(RuntimeError):
            m.check_services(lookup=good.__getitem__,armed_lookup=inaccessible)

if __name__=='__main__':
    unittest.main()
