import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest

try:
    import register_old_audit as module
except ImportError:
    module = None


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(module, 'register_old_audit implementation is missing')
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'root'
        self.payload = Path(self.temp.name) / 'payload'
        for path in ('usr/local/sbin', 'etc/sudoers.d', 'var/backups', 'var/lib'):
            (self.root / path).mkdir(parents=True, mode=0o755)
        self.root.chmod(0o755)
        for directory in (path for path in self.root.rglob('*') if path.is_dir()):
            directory.chmod(0o755)
        self.payload.mkdir(mode=0o700)
        source = Path(__file__).with_name('guid_audit.py').read_bytes()
        (self.payload / 'guid_audit.py').write_bytes(source)
        (self.payload / 'guid_audit.py').chmod(0o600)
        self.owner = (os.getuid(), os.getgid())
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def runner(self, argv):
        self.calls.append(list(argv))
        return b''

    def installer(self, **kwargs):
        return module.Installer(root=str(self.root), payload_dir=str(self.payload),
                                owner=self.owner, runner=self.runner, **kwargs)

    def managed(self, logical):
        return self.root / logical.lstrip('/')

    def test_installs_exact_files_modes_and_sudoers(self):
        report = self.installer().install()
        helper = self.managed('/usr/local/sbin/csn-standby-guid-audit')
        sudoers = self.managed('/etc/sudoers.d/csn-standby-guid-audit')
        self.assertEqual(helper.read_bytes(), (self.payload / 'guid_audit.py').read_bytes())
        self.assertEqual(stat.S_IMODE(helper.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(sudoers.stat().st_mode), 0o440)
        self.assertEqual(sudoers.read_bytes(), module.SUDOERS)
        self.assertEqual(report['validation'], 'passed')
        self.assertEqual(set(report), {'backup_directory', 'files', 'validation'})
        flattened = json.dumps(report)
        self.assertNotIn('distribution_sha256', flattened)
        self.assertTrue(any(call[0:2] == ['/usr/sbin/visudo', '-cf'] for call in self.calls))

    def test_rejects_symlink_payload_and_writable_payload_directory(self):
        payload_file = self.payload / 'guid_audit.py'
        payload_file.unlink()
        payload_file.symlink_to('/etc/passwd')
        with self.assertRaises(module.InstallError):
            self.installer().install()
        payload_file.unlink()
        payload_file.write_bytes(b'#!/usr/bin/python3\n')
        self.payload.chmod(0o777)
        with self.assertRaises(module.InstallError):
            self.installer().install()

    def test_rejects_unsafe_existing_managed_file(self):
        target = self.managed('/usr/local/sbin/csn-standby-guid-audit')
        target.symlink_to('/etc/passwd')
        with self.assertRaises(module.InstallError):
            self.installer().install()

    def test_rejects_symlinked_managed_ancestor(self):
        outside = Path(self.temp.name) / 'outside'
        (outside / 'sbin').mkdir(parents=True)
        (outside / 'sbin').chmod(0o755)
        shutil.rmtree(self.root / 'usr/local')
        (self.root / 'usr/local').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(module.InstallError):
            self.installer().install()

    def test_validator_rejects_python_without_payload(self):
        with self.assertRaises(module.InstallError):
            module.run_command(['/usr/bin/python3','-m','py_compile'])

    def test_failure_after_first_replace_rolls_back_both_files(self):
        helper = self.managed('/usr/local/sbin/csn-standby-guid-audit')
        sudoers = self.managed('/etc/sudoers.d/csn-standby-guid-audit')
        helper.write_bytes(b'old helper\n')
        helper.chmod(0o700)
        sudoers.write_bytes(b'old sudoers\n')
        sudoers.chmod(0o440)
        def checkpoint(phase, path):
            if phase == 'installed' and path.endswith('csn-standby-guid-audit'):
                raise module.InstallError('injected')
        with self.assertRaises(module.InstallError):
            self.installer(checkpoint=checkpoint).install()
        self.assertEqual(helper.read_bytes(), b'old helper\n')
        self.assertEqual(sudoers.read_bytes(), b'old sudoers\n')
        self.assertEqual(stat.S_IMODE(helper.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(sudoers.stat().st_mode), 0o440)

    def test_payload_change_is_detected(self):
        def checkpoint(phase, path):
            if phase == 'validated':
                (self.payload / 'guid_audit.py').write_bytes(b'changed')
        with self.assertRaises(module.InstallError):
            self.installer(checkpoint=checkpoint).install()

    def test_cli_requires_root_and_no_arguments(self):
        self.assertEqual(module.main(['extra'], geteuid=lambda: 0), 64)
        self.assertEqual(module.main([], geteuid=lambda: 1000), 1)


if __name__ == '__main__':
    unittest.main()
