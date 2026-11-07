import contextlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import types
import unittest

try:
    import armed_status
    import register_new_verification as module
except ImportError:
    armed_status = None
    module = None


class ArmedStatusTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(armed_status, 'armed_status implementation is missing')

    def test_absent_and_regular_present(self):
        def missing(path):
            raise FileNotFoundError()
        self.assertEqual(armed_status.check(lstat=missing), 'absent')
        regular = types.SimpleNamespace(st_mode=stat.S_IFREG | 0o600)
        self.assertEqual(armed_status.check(lstat=lambda path: regular), 'present')

    def test_nonregular_marker_and_traversal_error_fail(self):
        for mode in (stat.S_IFLNK | 0o777, stat.S_IFDIR | 0o700,
                     stat.S_IFCHR | 0o600):
            with self.assertRaises(armed_status.StatusError):
                armed_status.check(lstat=lambda path, mode=mode:
                                   types.SimpleNamespace(st_mode=mode))
        with self.assertRaises(armed_status.StatusError):
            armed_status.check(lstat=lambda path: (_ for _ in ()).throw(PermissionError()))

    def test_cli_requires_root_no_arguments_and_exact_output(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(armed_status.main([], geteuid=lambda: 0,
                                               checker=lambda: 'absent'), 0)
        self.assertEqual(output.getvalue(), 'absent\n')
        self.assertEqual(armed_status.main(['extra'], geteuid=lambda: 0), 64)
        self.assertEqual(armed_status.main([], geteuid=lambda: 1000), 1)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(module, 'register_new_verification implementation is missing')
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'root'
        self.payload = Path(self.temp.name) / 'payload'
        for path in ('usr/local/sbin', 'etc/sudoers.d', 'etc/csn-standby-sync',
                     'var/backups', 'var/lib'):
            (self.root / path).mkdir(parents=True, mode=0o755)
        self.root.chmod(0o755)
        for directory in (path for path in self.root.rglob('*') if path.is_dir()):
            directory.chmod(0o755)
        (self.root / 'etc/csn-standby-sync').chmod(0o700)
        self.payload.mkdir(mode=0o700)
        source = Path(__file__).with_name('armed_status.py').read_bytes()
        (self.payload / 'armed_status.py').write_bytes(source)
        (self.payload / 'armed_status.py').chmod(0o600)
        self.owner = (os.getuid(), os.getgid())
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def runner(self, argv):
        self.calls.append(list(argv))
        if argv[0] == '/usr/bin/systemctl':
            raise AssertionError('systemctl must never be called')
        return b''

    def installer(self, **kwargs):
        return module.Installer(root=str(self.root), payload_dir=str(self.payload),
                                owner=self.owner, runner=self.runner, **kwargs)

    def managed(self, logical):
        return self.root / logical.lstrip('/')

    def test_installs_only_exact_helper_and_sudoers(self):
        before = list((self.root / 'etc/csn-standby-sync').iterdir())
        report = self.installer().install()
        helper = self.managed('/usr/local/sbin/csn-standby-armed-status')
        sudoers = self.managed('/etc/sudoers.d/csn-standby-armed-status')
        self.assertEqual(stat.S_IMODE(helper.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(sudoers.stat().st_mode), 0o440)
        self.assertEqual(sudoers.read_bytes(), module.SUDOERS)
        self.assertEqual(before, list((self.root / 'etc/csn-standby-sync').iterdir()))
        self.assertEqual(report['validation'], 'passed')
        self.assertFalse(any(call[0] == '/usr/bin/systemctl' for call in self.calls))

    def test_armed_marker_is_never_modified(self):
        marker = self.managed('/etc/csn-standby-sync/armed')
        marker.write_bytes(b'operator')
        marker.chmod(0o600)
        before = (marker.read_bytes(), marker.stat().st_mode, marker.stat().st_mtime_ns)
        self.installer().install()
        after = (marker.read_bytes(), marker.stat().st_mode, marker.stat().st_mtime_ns)
        self.assertEqual(before, after)

    def test_unsafe_payload_and_existing_target_are_rejected(self):
        payload_file = self.payload / 'armed_status.py'
        payload_file.unlink()
        payload_file.symlink_to('/etc/passwd')
        with self.assertRaises(module.InstallError):
            self.installer().install()
        payload_file.unlink()
        source = Path(__file__).with_name('test_register_new_verification.py').read_bytes()
        payload_file.write_bytes(source)
        payload_file.chmod(0o600)
        target = self.managed('/usr/local/sbin/csn-standby-armed-status')
        target.symlink_to('/etc/passwd')
        with self.assertRaises(module.InstallError):
            self.installer().install()

    def test_failure_rolls_back_prior_files(self):
        helper = self.managed('/usr/local/sbin/csn-standby-armed-status')
        sudoers = self.managed('/etc/sudoers.d/csn-standby-armed-status')
        helper.write_bytes(b'old helper\n'); helper.chmod(0o700)
        sudoers.write_bytes(b'old sudoers\n'); sudoers.chmod(0o440)
        def checkpoint(phase, path):
            if phase == 'installed' and path.endswith('csn-standby-armed-status'):
                raise module.InstallError('injected')
        with self.assertRaises(module.InstallError):
            self.installer(checkpoint=checkpoint).install()
        self.assertEqual(helper.read_bytes(), b'old helper\n')
        self.assertEqual(sudoers.read_bytes(), b'old sudoers\n')

    def test_cli_requires_root_and_no_arguments(self):
        self.assertEqual(module.main(['extra'], geteuid=lambda: 0), 64)
        self.assertEqual(module.main([], geteuid=lambda: 1000), 1)


if __name__ == '__main__':
    unittest.main()
