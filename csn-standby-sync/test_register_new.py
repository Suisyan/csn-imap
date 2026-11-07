import base64
import contextlib
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
SERVICE = b'''[Unit]
Description=CSN old-to-new Dovecot standby synchronization
After=network-online.target dovecot.service
Wants=network-online.target
ConditionPathExists=/etc/csn-standby-sync/armed

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/csn-standby-sync
UMask=0077
PrivateTmp=yes
NoNewPrivileges=yes
TimeoutStartSec=25min
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7
'''
TIMER = b'''[Unit]
Description=Daily CSN old-to-new standby synchronization

[Timer]
OnCalendar=*-*-* 03:30:00 Asia/Tokyo
Persistent=true
AccuracySec=1min
Unit=csn-standby-sync.service

[Install]
WantedBy=timers.target
'''


def load_module():
    path = HERE / 'register_new.py'
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location('register_new', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M = load_module()


class FakeCommands:
    def __init__(self):
        self.calls = []
        self.fail_at = None
        self.fingerprint = 'SHA256:Do8bGq2Tf7JeXRncu8lLe6KjMLavohLWuWSN+A5+3/s'
        self.enabled, self.active = 'disabled', 'inactive'
        self.hook = None

    def __call__(self, argv, *, output=False):
        self.calls.append(list(argv))
        if self.hook:
            self.hook(argv)
        if self.fail_at == len(self.calls):
            raise subprocess.TimeoutExpired(argv, 30, output=b'SECRET VALIDATOR OUTPUT')
        if argv[:3] == ['/usr/bin/ssh-keygen', '-y', '-f']:
            return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, timeout=10, check=True).stdout
        if argv[0] == '/usr/bin/ssh-keygen':
            return ('256 ' + self.fingerprint + ' pinned (ED25519)\n').encode()
        if argv[1:2] == ['is-enabled']:
            return (self.enabled + '\n').encode()
        if argv[1:2] == ['is-active']:
            return (self.active + '\n').encode()
        return b''


class RegisterNewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = tempfile.TemporaryDirectory(prefix='csn-new-test-keys-')
        cls.key = Path(cls.keys.name) / 'generated'
        subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(cls.key)],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        cls.other = Path(cls.keys.name) / 'other'
        subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(cls.other)],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)

    @classmethod
    def tearDownClass(cls):
        cls.keys.cleanup()

    def setUp(self):
        self.assertIsNotNone(M, 'register_new.py installer is missing')
        self.temp = tempfile.TemporaryDirectory(prefix='csn-new-test-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'root'
        self.root.mkdir(mode=0o700)
        for p in ('etc/systemd/system', 'usr/local/sbin', 'var/lib', 'var/backups'):
            (self.root / p).mkdir(parents=True, mode=0o755)
        # pathlib creates intermediate parents with the process umask; fixtures must
        # explicitly model the root-owned, non-writable production ancestors.
        for path in self.root.rglob('*'):
            if path.is_dir():
                path.chmod(0o755)
        self.payload = self.base / 'payload'
        self.payload.mkdir(mode=0o700)
        for name in ('runner.py', 'csn-standby-sync.service', 'csn-standby-sync.timer'):
            shutil.copyfile(HERE / name, self.payload / name)
            (self.payload / name).chmod(0o600)
        self.inputs = self.base / 'inputs'
        self.inputs.mkdir(mode=0o700)
        self.private = self.inputs / 'private'
        self.public = self.inputs / 'public'
        shutil.copyfile(self.key, self.private)
        self.private.chmod(0o600)
        shutil.copyfile(str(self.key) + '.pub', self.public)
        wire = struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32) + bytes(range(32))
        self.host = self.inputs / 'known_hosts'
        self.host.write_bytes(b'118.27.32.100 ssh-ed25519 ' + base64.b64encode(wire) + b'\n')
        self.commands = FakeCommands()
        self.installer = M.Installer(root=str(self.root), payload_dir=str(self.payload),
                                     runner=self.commands, owner=(os.getuid(), os.getgid()))

    def install(self):
        return self.installer.install(str(self.private), str(self.public), str(self.host))

    def managed(self, path):
        return self.root / path.lstrip('/')

    def test_exact_units(self):
        self.assertEqual((HERE / 'csn-standby-sync.service').read_bytes(), SERVICE)
        self.assertEqual((HERE / 'csn-standby-sync.timer').read_bytes(), TIMER)

    def test_success_is_inactive_and_installs_exact_modes_and_payload(self):
        report = self.install()
        self.assertEqual(report['timer'], {'enabled': 'disabled', 'active': 'inactive'})
        self.assertEqual(report['armed'], 'absent')
        self.assertFalse(self.managed('/etc/csn-standby-sync/armed').exists())
        for path, mode in M.DIRECTORIES + [(p, m) for p, m, _ in M.FILES]:
            info = self.managed(path).stat()
            self.assertEqual(stat.S_IMODE(info.st_mode), mode, path)
            self.assertEqual((info.st_uid, info.st_gid), (os.getuid(), os.getgid()))
        self.assertEqual(self.managed('/usr/local/sbin/csn-standby-sync').read_bytes(),
                         (self.payload / 'runner.py').read_bytes())
        self.assertTrue(self.managed('/usr/local/sbin/csn-standby-sync').read_bytes().startswith(b'#!/usr/bin/python3\n'))
        systemctl = [c for c in self.commands.calls if c[0] == '/usr/bin/systemctl']
        self.assertEqual(systemctl, [
            ['/usr/bin/systemctl', 'daemon-reload'],
            ['/usr/bin/systemctl', 'disable', '--now', 'csn-standby-sync.timer'],
            ['/usr/bin/systemctl', 'is-enabled', 'csn-standby-sync.timer'],
            ['/usr/bin/systemctl', 'is-active', 'csn-standby-sync.timer']])
        verifies = [c for c in self.commands.calls if c[0] == '/usr/bin/systemd-analyze']
        self.assertEqual(len(verifies), 2)
        self.assertTrue(all(c[1:3] == ['verify', '--man=no'] for c in verifies))

    def test_staged_units_validate_after_runner_exists_before_unit_commit(self):
        verifies = []
        def observe(argv):
            if argv[0] == '/usr/bin/systemd-analyze':
                verifies.append(argv)
                self.assertTrue(self.managed('/usr/local/sbin/csn-standby-sync').exists())
                if len(verifies) == 1:
                    self.assertFalse(self.managed('/etc/systemd/system/csn-standby-sync.service').exists())
        self.commands.hook = observe
        self.install()
        self.assertEqual(len(verifies), 2)

    def test_key_mismatch_and_invalid_key_forms_are_rejected(self):
        cases = [Path(str(self.other) + '.pub').read_bytes(), b'ssh-rsa AAAA\n',
                 b'-----BEGIN OPENSSH PRIVATE KEY-----\n',
                 self.public.read_bytes() + self.public.read_bytes(),
                 self.public.read_bytes().rstrip() + b'\x00\n']
        for data in cases:
            with self.subTest(data_kind=cases.index(data)):
                self.public.write_bytes(data)
                with self.assertRaises(M.InstallError):
                    self.install()
                self.assertFalse(self.managed('/etc/csn-standby-sync').exists())

    def test_private_rsa_or_extra_lines_rejected(self):
        original = self.private.read_bytes()
        for data in (b'-----BEGIN RSA PRIVATE KEY-----\nAAAA\n-----END RSA PRIVATE KEY-----\n',
                     original + b'extra\n', original + b'\x00'):
            self.private.write_bytes(data)
            with self.assertRaises(M.InstallError):
                self.install()

    def test_known_hosts_only_exact_host_ed25519_and_pin(self):
        good = self.host.read_bytes()
        for data in (good + good, good.replace(b'118.27.32.100', b'127.0.0.1'),
                     good.replace(b'ssh-ed25519', b'ssh-rsa'),
                     b'@cert-authority ' + good, good.replace(b'118.27.32.100', b'[118.27.32.100]:22'),
                     good.rstrip() + b' extra\n'):
            with self.subTest(case=data[:25]):
                self.host.write_bytes(data)
                with self.assertRaises(M.InstallError):
                    self.install()
        self.host.write_bytes(good)
        self.commands.fingerprint = 'SHA256:wrong'
        with self.assertRaises(M.InstallError):
            self.install()

    def test_input_symlinks_nonregular_size_and_path_controls_rejected(self):
        target = self.inputs / 'symlink'
        target.symlink_to(self.private)
        for path in (str(target), str(self.inputs), str(self.private) + '\n'):
            with self.assertRaises(M.InstallError):
                self.installer.install(path, str(self.public), str(self.host))
        self.public.write_bytes(b'A' * 20000)
        with self.assertRaises(M.InstallError):
            self.install()

    def test_payload_symlinks_modes_and_identity_races_rejected(self):
        for name in ('runner.py', 'csn-standby-sync.service', 'csn-standby-sync.timer'):
            path = self.payload / name
            data = path.read_bytes()
            path.unlink()
            path.symlink_to(HERE / name)
            with self.assertRaises(M.InstallError):
                self.install()
            path.unlink()
            path.write_bytes(data)
            path.chmod(0o600)
        (self.payload / 'runner.py').chmod(0o666)
        with self.assertRaises(M.InstallError):
            self.install()
        (self.payload / 'runner.py').chmod(0o600)
        def mutate(argv):
            if argv[0] == '/usr/bin/systemd-analyze':
                path = self.payload / 'runner.py'
                original = path.read_bytes()
                path.unlink()
                path.write_bytes(original)
                path.chmod(0o600)
        self.commands.hook = mutate
        with self.assertRaises(M.InstallError):
            self.install()
        self.assertFalse(self.managed('/usr/local/sbin/csn-standby-sync').exists())

    def test_unsafe_existing_directories_and_files_preserved(self):
        config = self.managed('/etc/csn-standby-sync')
        config.mkdir(mode=0o755)
        unrelated = config / 'unrelated'
        unrelated.write_bytes(b'keep')
        with self.assertRaises(M.InstallError):
            self.install()
        self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o755)
        self.assertEqual(unrelated.read_bytes(), b'keep')
        config.chmod(0o700)
        key = config / 'id_ed25519'
        key.symlink_to(self.private)
        with self.assertRaises(M.InstallError):
            self.install()
        self.assertTrue(key.is_symlink())

    def test_preexisting_armed_refused_and_never_removed(self):
        config = self.managed('/etc/csn-standby-sync')
        config.mkdir(mode=0o700)
        (config / 'armed').write_bytes(b'operator')
        with self.assertRaises(M.InstallError):
            self.install()
        self.assertEqual((config / 'armed').read_bytes(), b'operator')

    def test_rollback_at_each_file_commit_preserves_existing_files(self):
        self.install()
        originals = {path: self.managed(path).read_bytes() for path, _, _ in M.FILES}
        unrelated = self.managed('/etc/csn-standby-sync/unrelated')
        unrelated.write_bytes(b'unrelated')
        for n in range(1, len(M.FILES) + 1):
            with self.subTest(commit=n):
                count = [0]
                def fail(phase, path):
                    if phase == 'committed':
                        count[0] += 1
                        if count[0] == n:
                            raise M.InstallError('injected commit failure')
                self.installer = M.Installer(root=str(self.root), payload_dir=str(self.payload),
                                             runner=FakeCommands(), owner=(os.getuid(), os.getgid()),
                                             checkpoint=fail)
                with self.assertRaises(M.InstallError):
                    self.install()
                for path, data in originals.items():
                    self.assertEqual(self.managed(path).read_bytes(), data, path)
                self.assertEqual(unrelated.read_bytes(), b'unrelated')
                self.assertEqual(list(self.root.rglob('.csn-standby-*')), [])

    def test_rollback_after_each_commit_removes_new_files_directories_and_stages(self):
        for stop in range(1, len(M.FILES) + 1):
            with self.subTest(commit=stop):
                count = [0]
                def fail(phase, path):
                    if phase == 'committed':
                        count[0] += 1
                        if count[0] == stop:
                            raise M.InstallError('commit failure')
                self.installer = M.Installer(root=str(self.root), payload_dir=str(self.payload),
                                             runner=FakeCommands(), owner=(os.getuid(), os.getgid()),
                                             checkpoint=fail)
                with self.assertRaises(M.InstallError):
                    self.install()
                for path, _, _ in M.FILES:
                    self.assertFalse(self.managed(path).exists(), path)
                for path, _ in M.DIRECTORIES:
                    self.assertFalse(self.managed(path).exists(), path)
                self.assertEqual(list(self.root.rglob('.csn-standby-*')), [])

    def test_cleanup_failure_can_restore_already_removed_previous_stages(self):
        self.install()
        originals = {p: self.managed(p).read_bytes() for p, _, _ in M.FILES}
        self.private.write_bytes(self.other.read_bytes())
        self.public.write_bytes(Path(str(self.other) + '.pub').read_bytes())
        original = self.installer._remove_stage
        calls = [0]
        def fail(entry):
            calls[0] += 1
            if calls[0] == 2:
                raise M.InstallError('cleanup failure')
            return original(entry)
        with patch.object(self.installer, '_remove_stage', side_effect=fail):
            with self.assertRaises(M.InstallError):
                self.install()
        for path, before in originals.items():
            self.assertEqual(self.managed(path).read_bytes(), before, path)
        self.assertEqual(list(self.root.rglob('.csn-standby-*')), [])

    def test_validator_and_systemctl_failures_roll_back_new_install(self):
        # Three input validators, staged+installed verification, and four systemctl calls.
        for command in range(1, 10):
            with self.subTest(command=command):
                self.commands = FakeCommands()
                self.commands.fail_at = command
                self.installer = M.Installer(root=str(self.root), payload_dir=str(self.payload),
                                             runner=self.commands, owner=(os.getuid(), os.getgid()))
                with self.assertRaises(M.InstallError) as error:
                    self.install()
                self.assertNotIn('SECRET', str(error.exception))
                for path, _, _ in M.FILES:
                    self.assertFalse(self.managed(path).exists(), path)
                for path, _ in reversed(M.DIRECTORIES):
                    self.assertFalse(self.managed(path).exists(), path)
                self.assertEqual(list(self.root.rglob('.csn-standby-*')), [])

    def test_each_stage_fsync_failure_removes_unregistered_file_and_new_directories(self):
        original_fsync = os.fsync
        for index, (stage_number, point) in enumerate(
                (number, point) for number in range(1, len(M.FILES) + 1) for point in ('file', 'parent')):
            with self.subTest(stage=stage_number, point=point):
                if index:
                    self.setUp()
                original_write = self.installer._write
                seen = [0]
                injected = []
                def write(parent, name, data, mode):
                    if not name.startswith('.csn-standby-'):
                        return original_write(parent, name, data, mode)
                    seen[0] += 1
                    if seen[0] != stage_number:
                        return original_write(parent, name, data, mode)
                    def fail(fd):
                        is_parent = fd == parent
                        if not injected and is_parent == (point == 'parent'):
                            injected.append(True)
                            raise OSError('PRIVATE KEY DIAGNOSTIC must not escape')
                        return original_fsync(fd)
                    with patch.object(M.os, 'fsync', side_effect=fail):
                        return original_write(parent, name, data, mode)
                with patch.object(self.installer, '_write', side_effect=write):
                    with self.assertRaises(M.InstallError) as error:
                        self.install()
                self.assertEqual(injected, [True])
                self.assertEqual(list(self.root.rglob('.csn-standby-*')), [])
                for path, _ in M.DIRECTORIES:
                    self.assertFalse(self.managed(path).exists(), path)
                self.assertIn('rollback completed', str(error.exception))
                self.assertNotIn('concurrent', str(error.exception))
                self.assertNotIn('PRIVATE', str(error.exception))

    def test_each_stage_readback_failure_is_registered_before_it_can_fail(self):
        for index, stage_number in enumerate(range(1, len(M.FILES) + 1)):
            with self.subTest(stage=stage_number):
                if index:
                    self.setUp()
                original_snapshot = self.installer._snapshot
                seen, injected = [0], []
                def snapshot(parent, name, mode):
                    if name.startswith('.csn-standby-') and not injected:
                        seen[0] += 1
                        if seen[0] == stage_number:
                            injected.append(True)
                            raise OSError('PRIVATE readback diagnostic')
                    return original_snapshot(parent, name, mode)
                with patch.object(self.installer, '_snapshot', side_effect=snapshot):
                    with self.assertRaises(M.InstallError) as error:
                        self.install()
                self.assertEqual(injected, [True])
                self.assertEqual(list(self.root.rglob('.csn-standby-*')), [])
                for path, _ in M.DIRECTORIES:
                    self.assertFalse(self.managed(path).exists(), path)
                self.assertIn('rollback completed', str(error.exception))
                self.assertNotIn('PRIVATE', str(error.exception))

    def test_successful_reload_is_repeated_after_later_failure_and_disk_rollback(self):
        cases = [(existing, failure) for existing in (False, True)
                 for failure in ('disable', 'is-enabled', 'is-active', 'final-validation')]
        for index, (existing, failure) in enumerate(cases):
            with self.subTest(existing=existing, failure=failure):
                if index:
                    self.setUp()
                unit_paths = ['/etc/systemd/system/' + name for name in M.PAYLOADS[1:]]
                before = {}
                for path in unit_paths:
                    before[path] = b'[Unit]\nDescription=Previously installed unit\n' if existing else None
                    if existing:
                        self.managed(path).write_bytes(before[path])
                        self.managed(path).chmod(0o644)
                cache, reloads = {}, []
                def observe(argv):
                    if argv == ['/usr/bin/systemctl', 'daemon-reload']:
                        cache.clear()
                        cache.update({path: self.managed(path).read_bytes()
                                      if self.managed(path).exists() else None for path in unit_paths})
                        reloads.append(dict(cache))
                    elif (len(reloads) == 1 and argv[0] == '/usr/bin/systemctl'
                          and argv[1] == failure):
                        raise M.InstallError('injected manager failure')
                self.commands.hook = observe
                final_check = self.installer._validate_final_installation
                if failure == 'final-validation':
                    final_check = lambda: (_ for _ in ()).throw(M.InstallError('injected final validation failure'))
                with patch.object(self.installer, '_validate_final_installation', side_effect=final_check):
                    with self.assertRaises(M.InstallError):
                        self.install()
                self.assertEqual(len(reloads), 2)
                self.assertEqual(cache, before)
                for path in unit_paths:
                    self.assertEqual(self.managed(path).read_bytes() if self.managed(path).exists() else None,
                                     before[path])
                mutations = [argv for argv in self.commands.calls if argv[0] == '/usr/bin/systemctl'
                             and argv[1] not in ('is-enabled', 'is-active')]
                self.assertEqual(mutations, [
                    ['/usr/bin/systemctl', 'daemon-reload'],
                    ['/usr/bin/systemctl', 'disable', '--now', M.UNIT],
                    ['/usr/bin/systemctl', 'daemon-reload']])

    def test_failed_recovery_reload_has_distinct_sanitized_error_and_preserves_backups(self):
        reloads = []
        def fail(argv):
            if argv == ['/usr/bin/systemctl', 'daemon-reload']:
                reloads.append(True)
                if len(reloads) == 2:
                    raise subprocess.TimeoutExpired(argv, 30, output=b'PRIVATE RECOVERY OUTPUT')
            if argv[:2] == ['/usr/bin/systemctl', 'disable']:
                raise M.InstallError('PRIVATE disable output')
        self.commands.hook = fail
        with self.assertRaises(M.InstallError) as error:
            self.install()
        self.assertEqual(reloads, [True, True])
        self.assertIn('recovery daemon-reload failed', str(error.exception))
        self.assertNotIn('PRIVATE', str(error.exception))
        self.assertIsNotNone(self.installer.backup_dir)
        self.assertTrue(self.managed(self.installer.backup_dir).is_dir())
        for path, _, _ in M.FILES:
            self.assertFalse(self.managed(path).exists(), path)

    def test_reload_timeout_after_manager_updates_cache_recovers_restored_disk(self):
        for index, existing in enumerate((False, True)):
            with self.subTest(existing=existing):
                if index:
                    self.setUp()
                unit_paths = ['/etc/systemd/system/' + name for name in M.PAYLOADS[1:]]
                before = {}
                for path in unit_paths:
                    before[path] = b'[Unit]\nDescription=Previous unit\n' if existing else None
                    if existing:
                        self.managed(path).write_bytes(before[path])
                        self.managed(path).chmod(0o644)
                cache, reloads = {}, []
                def uncertain_reload(argv):
                    if argv != ['/usr/bin/systemctl', 'daemon-reload']:
                        return
                    cache.clear()
                    cache.update({path: self.managed(path).read_bytes()
                                  if self.managed(path).exists() else None for path in unit_paths})
                    reloads.append(dict(cache))
                    if len(reloads) == 1:
                        raise subprocess.TimeoutExpired(argv, 30, output=b'PRIVATE TIMEOUT OUTPUT')
                self.commands.hook = uncertain_reload
                with self.assertRaises(M.InstallError) as error:
                    self.install()
                self.assertEqual(len(reloads), 2)
                self.assertEqual(cache, before)
                self.assertNotEqual(reloads[0], before)
                self.assertNotIn('PRIVATE', str(error.exception))
                self.assertIn('rollback completed', str(error.exception))
                for path in unit_paths:
                    actual = self.managed(path).read_bytes() if self.managed(path).exists() else None
                    self.assertEqual(actual, before[path])
                self.assertEqual([argv for argv in self.commands.calls if argv[0] == '/usr/bin/systemctl'],
                                 [['/usr/bin/systemctl', 'daemon-reload'],
                                  ['/usr/bin/systemctl', 'daemon-reload']])

    def test_concurrent_file_edits_and_new_unrelated_files_preserved_on_rollback(self):
        touched = self.managed('/usr/local/sbin/csn-standby-sync')
        def edit(phase, path):
            if phase == 'committed' and path == '/usr/local/sbin/csn-standby-sync':
                touched.write_bytes(b'CONCURRENT EDIT')
                self.managed('/etc/csn-standby-sync/concurrent').write_bytes(b'KEEP')
                raise M.InstallError('injected')
        self.installer.checkpoint = edit
        with self.assertRaises(M.InstallError):
            self.install()
        self.assertEqual(touched.read_bytes(), b'CONCURRENT EDIT')
        self.assertEqual(self.managed('/etc/csn-standby-sync/concurrent').read_bytes(), b'KEEP')

    def test_concurrent_precommit_destination_and_directory_changes_rejected(self):
        victim = self.managed('/usr/local/sbin/csn-standby-sync')
        def edit(phase, path):
            if phase == 'before_commit' and path == '/usr/local/sbin/csn-standby-sync':
                victim.write_bytes(b'CONCURRENT NEW FILE')
                victim.chmod(0o700)
        self.installer.checkpoint = edit
        with self.assertRaises(M.InstallError):
            self.install()
        self.assertEqual(victim.read_bytes(), b'CONCURRENT NEW FILE')

    def test_concurrent_parent_replacement_is_detected_and_preserved(self):
        parent = self.managed('/usr/local/sbin')
        moved = self.managed('/usr/local/sbin.moved')
        def replace_parent(phase, path):
            if phase == 'before_commit' and path == '/usr/local/sbin/csn-standby-sync':
                parent.rename(moved)
                parent.mkdir(mode=0o755)
                (parent / 'concurrent').write_bytes(b'KEEP NEW DIRECTORY')
        self.installer.checkpoint = replace_parent
        with self.assertRaises(M.InstallError):
            self.install()
        self.assertEqual((parent / 'concurrent').read_bytes(), b'KEEP NEW DIRECTORY')
        self.assertFalse((parent / 'csn-standby-sync').exists())
        self.assertEqual(list(moved.iterdir()), [])

    def test_concurrent_created_directory_mode_change_is_preserved(self):
        config = self.managed('/etc/csn-standby-sync')
        def change_mode(argv):
            if argv[0] == '/usr/bin/systemd-analyze':
                config.chmod(0o750)
                raise M.InstallError('operator changed directory mode')
        self.commands.hook = change_mode
        with self.assertRaises(M.InstallError):
            self.install()
        self.assertTrue(config.is_dir())
        self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o750)

    def test_secure_directory_lock_rejects_second_installer_without_mutation(self):
        fd = os.open(self.managed('/var/lib'), os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(M.InstallError):
                self.install()
        finally:
            os.close(fd)
        self.assertFalse(self.managed('/etc/csn-standby-sync').exists())

    def test_late_operator_armed_file_preserved_and_install_rolled_back(self):
        def arm(argv):
            if argv[:2] == ['/usr/bin/systemctl', 'is-active']:
                self.managed('/etc/csn-standby-sync/armed').write_bytes(b'OPERATOR')
        self.commands.hook = arm
        with self.assertRaises(M.InstallError):
            self.install()
        self.assertEqual(self.managed('/etc/csn-standby-sync/armed').read_bytes(), b'OPERATOR')
        self.assertFalse(self.managed('/usr/local/sbin/csn-standby-sync').exists())

    def test_final_state_must_be_disabled_inactive(self):
        for enabled, active in (('enabled', 'inactive'), ('disabled', 'active'), ('masked', 'inactive')):
            self.commands.enabled, self.commands.active = enabled, active
            with self.assertRaises(M.InstallError):
                self.install()
            self.assertFalse(self.managed('/usr/local/sbin/csn-standby-sync').exists())

    def test_final_state_queries_detect_every_directory_mode_or_identity_change(self):
        for index, (path, mutation) in enumerate(
                (p, m) for p, _ in M.DIRECTORIES for m in ('mode', 'identity')):
            with self.subTest(directory=path, mutation=mutation):
                if index:
                    self.setUp()
                target = self.managed(path)
                moved = target.with_name(target.name + '.operator-moved')
                observed = []
                def change(argv):
                    if argv[:2] != ['/usr/bin/systemctl', 'is-active']:
                        return
                    observed.append(True)
                    if mutation == 'mode':
                        target.chmod(0o750)
                    else:
                        target.rename(moved)
                        target.mkdir(mode=0o700)
                        (target / 'operator').write_bytes(b'PRESERVE')
                self.commands.hook = change
                with self.assertRaises(M.InstallError):
                    self.install()
                self.assertEqual(observed, [True])
                self.assertTrue(target.is_dir())
                if mutation == 'mode':
                    self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o750)
                else:
                    self.assertEqual((target / 'operator').read_bytes(), b'PRESERVE')
                    self.assertTrue(moved.is_dir())
                for managed, _, _ in M.FILES:
                    self.assertFalse(self.managed(managed).exists(), managed)

    def test_report_redacts_all_material(self):
        report = json.dumps(self.install())
        for material in (self.private.read_text().splitlines()[1],
                         self.public.read_text().split()[1], self.host.read_text().split()[2],
                         'PRIVATE KEY', 'ssh-ed25519'):
            self.assertNotIn(material, report)
        self.assertIn('sha256', report)
        self.assertIn('backup_directory', report)

    def test_command_timeout_output_sanitized_and_allowlist(self):
        with patch.object(M.subprocess, 'run', side_effect=subprocess.TimeoutExpired(
                ['secret'], 30, output=b'SECRET', stderr=b'KEY')) as run:
            with self.assertRaises(M.InstallError) as error:
                M.run_command(['/usr/bin/systemctl', 'daemon-reload'])
            self.assertNotIn('SECRET', str(error.exception))
            self.assertNotIn('KEY', str(error.exception))
            self.assertEqual(run.call_args.kwargs['timeout'], M.COMMAND_TIMEOUT)
        for argv in (['/bin/sh', '-c', 'true'], ['/usr/bin/systemctl', 'enable', 'csn-standby-sync.timer'],
                     ['/usr/bin/systemctl', 'start', 'csn-standby-sync.service'],
                     ['/usr/bin/systemctl', 'restart', 'dovecot'],
                     ['/usr/bin/systemctl', 'stop', 'fetchmail-csn.service']):
            with self.assertRaises(M.InstallError):
                M.run_command(argv)

    def test_required_validator_stdout_read_is_bounded_and_oversize_is_sanitized(self):
        limit = getattr(M, 'MAX_OUTPUT', 65536)
        def huge_stdout(argv, **kwargs):
            destination = kwargs.get('stdout')
            if hasattr(destination, 'write'):
                destination.write(b'PRIVATE KEY OUTPUT' * (limit // 4))
                destination.flush()
                return subprocess.CompletedProcess(argv, 0)
            return subprocess.CompletedProcess(argv, 0, stdout=b'PRIVATE KEY OUTPUT' * (limit // 4))
        with patch.object(M.subprocess, 'run', side_effect=huge_stdout):
            with self.assertRaises(M.InstallError) as error:
                M.run_command(['/usr/bin/ssh-keygen', '-y', '-f', '/generated-test-only'])
        self.assertNotIn('PRIVATE', str(error.exception))

        class BoundedCapture:
            def __init__(self):
                self.closed = False
                self.reads = []
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.closed = True
            def seek(self, offset):
                self.assert_offset = offset
            def read(self, count):
                self.reads.append(count)
                return b'ssh-ed25519 test\n'
        capture = BoundedCapture()
        with patch.object(M.tempfile, 'TemporaryFile', return_value=capture), patch.object(
                M.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout=b'ssh-ed25519 test\n')) as run:
            M.run_command(['/usr/bin/ssh-keygen', '-y', '-f', '/generated-test-only'])
        self.assertEqual(capture.reads, [limit + 1])
        self.assertTrue(capture.closed)
        self.assertIs(run.call_args.kwargs['stdout'], capture)
        self.assertEqual(run.call_args.kwargs['stderr'], subprocess.DEVNULL)

    def test_unused_output_and_all_stderr_are_discarded(self):
        calls = []
        def verbose(argv, **kwargs):
            calls.append(kwargs)
            destination = kwargs.get('stdout')
            if hasattr(destination, 'write'):
                destination.write(b'KEY\n')
            # A validator may be arbitrarily verbose on stderr; no Python buffer
            # may collect it. The actual subprocess test below emits 8 MiB.
            return subprocess.CompletedProcess(argv, 0, stdout=b'', stderr=b'')
        with patch.object(M.subprocess, 'run', side_effect=verbose):
            M.run_command(['/usr/bin/systemctl', 'daemon-reload'])
            M.run_command(['/usr/bin/ssh-keygen', '-y', '-f', '/generated-test-only'])
        self.assertEqual(calls[0]['stdout'], subprocess.DEVNULL)
        self.assertTrue(all(call['stderr'] == subprocess.DEVNULL for call in calls))
        actual_run = subprocess.run
        def generated_verbose_child(argv, **kwargs):
            return actual_run(['/usr/bin/python3', '-c',
                               'import os; os.write(2, b"V" * (8 * 1024 * 1024)); os.write(1, b"OK")'],
                              **kwargs)
        with patch.object(M.subprocess, 'run', side_effect=generated_verbose_child):
            result = M.run_command(['/usr/bin/ssh-keygen', '-y', '-f', '/generated-test-only'])
        self.assertEqual(result, b'OK')

    def test_cli_root_and_exact_options(self):
        with contextlib.redirect_stderr(io.StringIO()), patch.object(M.os, 'geteuid', return_value=1000):
            self.assertEqual(M.main(['--private-key', str(self.private), '--public-key', str(self.public),
                                     '--known-hosts', str(self.host)]), 1)
        for argv in ([], ['--private-key', 'a', '--public-key', 'b', '--known-hosts', 'c', '--extra'],
                     ['--private-key', 'a', '--private-key', 'b', '--public-key', 'c', '--known-hosts', 'd']):
            with contextlib.redirect_stderr(io.StringIO()), patch.object(M.os, 'geteuid', return_value=0):
                self.assertEqual(M.main(argv), 1)


if __name__ == '__main__':
    unittest.main()
