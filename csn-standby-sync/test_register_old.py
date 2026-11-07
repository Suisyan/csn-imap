import base64
import importlib.util
import io
import os
import stat
import struct
import subprocess
import tempfile
import unittest
from unittest import mock


HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    path = os.path.join(HERE, name + '.py')
    if not os.path.exists(path):
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SOURCE = load('old_source')
REGISTER = load('register_old')
WIRE = struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32) + bytes(range(32))
KEY = b'ssh-ed25519 ' + base64.b64encode(WIRE) + b' test-only\n'
COMMAND = 'sudo -n /usr/local/sbin/csn-standby-dsync-source'
MANAGED = ['/usr/local/sbin/csn-standby-dsync-source', '/etc/csn-standby-source.conf', '/etc/sudoers.d/csn-standby-source', '/home/umeoka/.ssh/authorized_keys']
EXPECTED_CONFIG = b'''protocols =
ssl = no
log_path = /dev/stderr
info_log_path = /dev/stderr
mail_home = /home/csn
mail_location = maildir:/home/csn/Maildir:INDEX=/home/csn/.csn-standby-index
mail_uid = 1001
mail_gid = 1001
mail_plugins =
first_valid_uid = 1
namespace inbox {
  inbox = yes
  separator = /
  prefix =
}
'''


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(SOURCE, 'old_source implementation is missing')

    def test_rejects_arguments_and_non_root_without_stdout(self):
        for args, uid in [(['bad'], 0), ([], 1000)]:
            with mock.patch('sys.stdout', new_callable=io.StringIO) as out:
                with mock.patch('sys.stderr', new_callable=io.StringIO):
                    self.assertEqual(SOURCE.main(args, geteuid=lambda: uid), 1)
                self.assertEqual(out.getvalue(), '')

    def test_exact_fixed_exec(self):
        with mock.patch.object(SOURCE.os, 'execv') as execute:
            SOURCE.main([], geteuid=lambda: 0)
        execute.assert_called_once_with('/sbin/runuser', ['/sbin/runuser', '-u', 'csn', '--', '/usr/bin/doveadm', '-c', '/etc/csn-standby-source.conf', 'dsync-server'])


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(REGISTER, 'register_old implementation is missing')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        self.calls = []
        self.lookup = lambda name: (os.getuid(), os.getgid())
        self.installer = REGISTER.Installer(self.root, self.run_command, self.lookup)

    def run_command(self, argv):
        self.calls.append(list(argv))

    def path(self, name):
        return self.root + name

    def write(self, name, data, mode=0o600):
        path = self.path(name)
        parent = os.path.dirname(path)
        if not os.path.isdir(parent):
            os.makedirs(parent, 0o700)
            current = parent
            while current != self.root:
                os.chmod(current, 0o700)
                current = os.path.dirname(current)
        if os.path.isfile(path):
            os.chmod(path, 0o600)
        with open(path, 'wb') as stream:
            stream.write(data)
        os.chmod(path, mode)
        return path

    def key_file(self, data=KEY):
        return self.write('/public.key', data)

    def test_public_key_validation(self):
        self.assertEqual(REGISTER.read_public_key(self.key_file(), self.run_command), KEY.rstrip(b'\n'))
        self.assertEqual(self.calls[-1][:3], ['/usr/bin/ssh-keygen', '-l', '-E'])
        bad = [b'', b'\n', KEY + KEY, b'\n' + KEY, KEY.replace(b'ssh-ed25519', b'ssh-rsa', 1), b'-----BEGIN OPENSSH PRIVATE KEY-----\n', b'ssh-ed25519 !notbase64\n', KEY.replace(b'test-only', b'bad\tcomment'), KEY.replace(b'test-only', b'bad\rcomment'), b'ssh-ed25519 ' + base64.b64encode(b'bad-wire') + b'\n', KEY.replace(b' test-only', b'= test-only')]
        for data in bad:
            with self.subTest(data=data):
                with self.assertRaises(REGISTER.InstallError):
                    REGISTER.read_public_key(self.key_file(data), self.run_command)

    def test_key_file_symlink_and_directory_rejected(self):
        source = self.key_file()
        os.symlink(source, self.path('/link'))
        for path in [self.path('/link'), self.root]:
            with self.assertRaises(REGISTER.InstallError):
                REGISTER.read_public_key(path, self.run_command)

    def test_managed_block_is_exact_and_preserves_unrelated_bytes(self):
        original = b'# existing\r\nssh-rsa unrelated\nlast-without-newline'
        rendered = REGISTER.render_authorized_keys(original, KEY.rstrip(b'\n'))
        self.assertTrue(rendered.startswith(original))
        expected = b'restrict,command="' + COMMAND.encode('ascii') + b'" ' + KEY
        self.assertIn(expected, rendered)
        self.assertEqual(REGISTER.render_authorized_keys(rendered, KEY.rstrip(b'\n')), rendered)
        self.assertEqual(rendered.count(REGISTER.BEGIN), 1)
        self.assertEqual(rendered.count(REGISTER.END), 1)
        with self.assertRaises(REGISTER.InstallError):
            REGISTER.render_authorized_keys(REGISTER.BEGIN + b'\n', KEY.rstrip(b'\n'))

    def test_sudoers_payload_exact(self):
        self.assertEqual(REGISTER.SUDOERS, b'umeoka ALL=(root) NOPASSWD: /usr/local/sbin/csn-standby-dsync-source\n')
        self.assertNotIn(b'*', REGISTER.SUDOERS)

    def test_exact_dovecot_2_2_configuration(self):
        with open(os.path.join(HERE, 'old_source.conf'), 'rb') as stream:
            self.assertEqual(stream.read(), EXPECTED_CONFIG)

    def test_install_modes_validation_idempotence_and_report_redaction(self):
        original = b'# preserve\r\nssh-rsa unrelated\n'
        self.write('/home/umeoka/.ssh/authorized_keys', original)
        report = self.installer.install(self.key_file())
        modes = {'/usr/local/sbin/csn-standby-dsync-source': 0o700, '/etc/csn-standby-source.conf': 0o644, '/etc/sudoers.d/csn-standby-source': 0o440, '/home/csn/.csn-standby-index': 0o700, '/home/umeoka/.ssh/authorized_keys': 0o600}
        for path, mode in modes.items():
            self.assertEqual(stat.S_IMODE(os.stat(self.path(path)).st_mode), mode)
        with open(self.path('/home/umeoka/.ssh/authorized_keys'), 'rb') as stream:
            first = stream.read()
        self.assertTrue(first.startswith(original))
        self.installer.install(self.key_file())
        with open(self.path('/home/umeoka/.ssh/authorized_keys'), 'rb') as stream:
            self.assertEqual(stream.read(), first)
        self.assertEqual(first.count(REGISTER.BEGIN), 1)
        self.assertNotIn(KEY.decode('ascii').strip(), str(report))
        self.assertNotIn(base64.b64encode(WIRE).decode('ascii'), str(report))
        self.assertTrue(any(call[:2] == ['/usr/sbin/visudo', '-cf'] for call in self.calls))
        self.assertIn(['/usr/sbin/visudo', '-cf', self.path('/etc/sudoers')], self.calls)
        self.assertIn(['/usr/bin/doveconf', '-c', self.path('/etc/csn-standby-source.conf'), '-n'], self.calls)
        for call in self.calls:
            self.assertIn(call[0], ['/usr/bin/ssh-keygen', '/usr/sbin/visudo', '/usr/bin/doveconf'])
            self.assertFalse(any(word in ('systemctl', 'service', 'dsync', 'backup', 'sync') for word in call))

    def test_atomic_backup_and_unchanged_rerun(self):
        path = '/etc/test-managed'
        self.write(path, b'old', 0o640)
        self.installer.atomic_install(path, b'new', 0o600, self.lookup('root'))
        with open(self.path(path), 'rb') as stream:
            self.assertEqual(stream.read(), b'new')
        backup = self.installer.backup_dir + path
        with open(backup, 'rb') as stream:
            self.assertEqual(stream.read(), b'old')
        self.assertEqual(stat.S_IMODE(os.stat(backup).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.installer.backup_dir).st_mode), 0o700)
        self.installer.atomic_install(path, b'new', 0o600, self.lookup('root'))
        self.assertEqual(os.listdir(self.path('/etc')), ['test-managed'])

    def test_rejects_symlink_and_unsafe_file_or_parent(self):
        self.write('/safe/target', b'x')
        os.symlink(self.path('/safe/target'), self.path('/safe/link'))
        os.symlink(self.path('/safe'), self.path('/linked-parent'))
        self.write('/unsafe/file', b'x', 0o666)
        self.write('/wide/file', b'x')
        os.chmod(self.path('/wide'), 0o777)
        for path in ['/safe/link', '/linked-parent/file', '/unsafe/file', '/wide/file']:
            with self.subTest(path=path):
                with self.assertRaises(REGISTER.InstallError):
                    self.installer.atomic_install(path, b'new', 0o600, self.lookup('root'))

    def test_prevalidation_failure_preserves_sudoers_destination(self):
        self.write('/etc/sudoers.d/csn-standby-source', b'prior\n', 0o440)
        def fail(argv):
            if argv[0] == '/usr/sbin/visudo':
                raise REGISTER.InstallError('validation failed')
        self.installer.runner = fail
        with self.assertRaises(REGISTER.InstallError):
            self.installer.install(self.key_file())
        with open(self.path('/etc/sudoers.d/csn-standby-source'), 'rb') as stream:
            self.assertEqual(stream.read(), b'prior\n')

    def test_validation_failure_does_not_publish_authorized_key(self):
        original = b'# existing only\n'
        self.write('/home/umeoka/.ssh/authorized_keys', original)
        def fail(argv):
            if argv[0] == '/usr/bin/doveconf':
                raise REGISTER.InstallError('configuration validation failed')
        self.installer.runner = fail
        with self.assertRaises(REGISTER.InstallError):
            self.installer.install(self.key_file())
        with open(self.path('/home/umeoka/.ssh/authorized_keys'), 'rb') as stream:
            self.assertEqual(stream.read(), original)

    def test_explicit_ownership_intent(self):
        root = self.lookup('root')
        accounts = {'root': root, 'csn': (1001, 1001), 'umeoka': (1002, 1002)}
        installer = REGISTER.Installer(self.root, self.run_command, accounts.__getitem__)
        observed = []
        def chown(fd, uid, gid):
            observed.append((os.readlink('/proc/self/fd/' + str(fd)), uid, gid))
        self.assertTrue(hasattr(installer, 'prepare_stage'), 'transaction staging is missing')
        with mock.patch.object(REGISTER.os, 'fchown', side_effect=chown):
            installer.install(self.key_file())
        intents = dict((stage['logical'], stage['owner']) for stage in installer.stages)
        self.assertEqual(intents[REGISTER.HELPER], root)
        self.assertEqual(intents[REGISTER.CONFIG], root)
        self.assertEqual(intents[REGISTER.SUDOERS_PATH], root)
        self.assertEqual(intents[REGISTER.AUTHORIZED], (1002, 1002))
        self.assertIn((self.path(REGISTER.INDEX), 1001, 1001), observed)

    def file_state(self, logical):
        try:
            info = os.lstat(self.path(logical))
        except FileNotFoundError:
            return None
        with open(self.path(logical), 'rb') as stream:
            return stream.read(), info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)

    def seed_managed(self):
        for index, logical in enumerate(MANAGED):
            self.write(logical, ('# prior %s\n' % index).encode('ascii'), 0o640)
        return dict((logical, self.file_state(logical)) for logical in MANAGED)

    def assert_restored(self, initial):
        for logical in MANAGED:
            self.assertEqual(self.file_state(logical), initial[logical], logical)

    def test_failure_after_every_commit_restores_files_and_new_index(self):
        self.assertTrue(hasattr(REGISTER.Installer, 'commit_file'), 'transaction commits are missing')
        for fail_after in range(1, 5):
            for existing in (True, False):
                with self.subTest(fail_after=fail_after, existing=existing):
                    for logical in MANAGED:
                        if os.path.exists(self.path(logical)):
                            os.unlink(self.path(logical))
                    initial = self.seed_managed() if existing else dict((path, None) for path in MANAGED)
                    class FailingInstaller(REGISTER.Installer):
                        def commit_file(inner, stage):
                            super(FailingInstaller, inner).commit_file(stage)
                            inner.counter += 1
                            if inner.counter == fail_after:
                                raise REGISTER.InstallError('injected post-commit failure')
                    installer = FailingInstaller(self.root, self.run_command, self.lookup)
                    installer.counter = 0
                    with self.assertRaises(REGISTER.InstallError):
                        installer.install(self.key_file())
                    self.assert_restored(initial)
                    self.assertFalse(os.path.exists(self.path(REGISTER.INDEX)))
                    for parent in ['/etc', '/etc/sudoers.d', '/usr/local/sbin', '/home/umeoka/.ssh']:
                        self.assertFalse(any(name.startswith('.csn-standby-') for name in os.listdir(self.path(parent))))

    def test_index_creation_failure_rolls_back_new_directory(self):
        self.assertTrue(hasattr(REGISTER.Installer, 'create_index'), 'transactional index creation is missing')
        initial = self.seed_managed()
        class FailingInstaller(REGISTER.Installer):
            def create_index(inner):
                super(FailingInstaller, inner).create_index()
                raise REGISTER.InstallError('failure after index creation')
        installer = FailingInstaller(self.root, self.run_command, self.lookup)
        with self.assertRaises(REGISTER.InstallError):
            installer.install(self.key_file())
        self.assert_restored(initial)
        self.assertFalse(os.path.exists(self.path(REGISTER.INDEX)))

    def test_existing_index_metadata_never_changes(self):
        self.write(REGISTER.INDEX + '/existing', b'index-data')
        os.chmod(self.path(REGISTER.INDEX), 0o700)
        before = os.stat(self.path(REGISTER.INDEX))
        self.installer.install(self.key_file())
        after = os.stat(self.path(REGISTER.INDEX))
        for field in ('st_ino', 'st_uid', 'st_gid', 'st_mode', 'st_mtime_ns', 'st_ctime_ns'):
            self.assertEqual(getattr(before, field), getattr(after, field), field)

    def test_existing_index_requires_exact_csn_owner_gid_and_0700_without_mutation(self):
        self.write(REGISTER.INDEX + '/existing', b'index-data')
        current_owner = self.lookup('root')
        cases = [('root-owned', 0o700, (current_owner[0] + 1, current_owner[1])),
                 ('wrong-gid', 0o700, (current_owner[0], current_owner[1] + 1))]
        cases.extend(('mode-%04o' % mode, mode, current_owner)
                     for mode in (0o750, 0o710, 0o770, 0o755, 0o711, 0o1700, 0o2700))
        for label, mode, csn_owner in cases:
            with self.subTest(case=label):
                initial = self.seed_managed()
                os.chmod(self.path(REGISTER.INDEX), mode)
                before = os.stat(self.path(REGISTER.INDEX))
                accounts = {'root': current_owner, 'csn': csn_owner, 'umeoka': current_owner}
                installer = REGISTER.Installer(self.root, self.run_command, accounts.__getitem__)
                self.calls = []
                with self.assertRaises(REGISTER.InstallError):
                    installer.install(self.key_file())
                self.assert_restored(initial)
                after = os.stat(self.path(REGISTER.INDEX))
                for field in ('st_dev', 'st_ino', 'st_uid', 'st_gid', 'st_mode', 'st_mtime_ns', 'st_ctime_ns'):
                    self.assertEqual(getattr(before, field), getattr(after, field), field)
                self.assertFalse(any(call[0] in ('/usr/sbin/visudo', '/usr/bin/doveconf') for call in self.calls))

    def test_staged_configurations_validate_before_first_commit(self):
        self.assertTrue(hasattr(REGISTER.Installer, 'commit_file'), 'transaction commits are missing')
        initial = self.seed_managed()
        validations = []
        def validate(argv):
            if argv[0] in ('/usr/sbin/visudo', '/usr/bin/doveconf'):
                candidate = argv[2]
                if candidate.startswith('/proc/'):
                    self.assert_restored(initial)
                    self.assertFalse(os.path.exists(self.path(REGISTER.INDEX)))
                    with open(candidate, 'rb') as stream:
                        content = stream.read()
                    validations.append((argv[0], content))
        self.installer.runner = validate
        self.installer.install(self.key_file())
        self.assertIn(('/usr/sbin/visudo', REGISTER.SUDOERS), validations)
        self.assertIn(('/usr/bin/doveconf', EXPECTED_CONFIG), validations)

    def test_installed_validation_failure_restores_all_files(self):
        for binary, candidate in [('/usr/sbin/visudo', '/etc/sudoers'), ('/usr/bin/doveconf', '/etc/csn-standby-source.conf')]:
            with self.subTest(binary=binary):
                initial = self.seed_managed()
                def validate(argv):
                    if argv[0] == binary and argv[2] == self.path(candidate):
                        raise REGISTER.InstallError('installed validation failed')
                self.installer.runner = validate
                with self.assertRaises(REGISTER.InstallError):
                    self.installer.install(self.key_file())
                self.assert_restored(initial)
                self.assertFalse(os.path.exists(self.path(REGISTER.INDEX)))

    def test_validation_edit_to_unrelated_authorized_key_is_preserved(self):
        original = b'# existing\n'
        edit = b'ssh-rsa unrelated-added-during-validation\r\n'
        self.write(REGISTER.AUTHORIZED, original)
        edited = []
        def validate(argv):
            if argv[0] == '/usr/bin/doveconf' and not edited:
                self.write(REGISTER.AUTHORIZED, original + edit)
                edited.append(True)
        self.installer.runner = validate
        self.installer.install(self.key_file())
        with open(self.path(REGISTER.AUTHORIZED), 'rb') as stream:
            installed = stream.read()
        self.assertTrue(installed.startswith(original + edit))
        self.assertEqual(installed.count(REGISTER.BEGIN), 1)

    def test_edit_after_render_aborts_and_preserves_unrelated_keys(self):
        self.assertTrue(hasattr(REGISTER.Installer, 'commit_file'), 'guarded publication is missing')
        initial = self.seed_managed()
        changed = b'# concurrent replacement after render\n'
        class RacingInstaller(REGISTER.Installer):
            def commit_file(inner, stage):
                if stage['logical'] == REGISTER.AUTHORIZED:
                    self.write(REGISTER.AUTHORIZED, changed)
                super(RacingInstaller, inner).commit_file(stage)
        installer = RacingInstaller(self.root, self.run_command, self.lookup)
        with self.assertRaises(REGISTER.InstallError):
            installer.install(self.key_file())
        initial[REGISTER.AUTHORIZED] = self.file_state(REGISTER.AUTHORIZED)
        self.assertEqual(initial[REGISTER.AUTHORIZED][0], changed)
        self.assert_restored(initial)
        self.assertFalse(os.path.exists(self.path(REGISTER.INDEX)))

    def test_nonblocking_secure_installer_lock(self):
        self.assertTrue(hasattr(self.installer, 'install_lock'), 'installer locking is missing')
        with self.installer.install_lock():
            another = REGISTER.Installer(self.root, self.run_command, self.lookup)
            with self.assertRaises(REGISTER.InstallError):
                another.install(self.key_file())
        lock_path = self.path(REGISTER.LOCK_PATH)
        self.assertEqual(stat.S_IMODE(os.stat(lock_path).st_mode), 0o600)
        os.unlink(lock_path)
        os.symlink(self.key_file(), lock_path)
        with self.assertRaises(REGISTER.InstallError):
            self.installer.install(self.key_file())

    def test_changed_or_populated_new_index_is_not_removed_on_failure(self):
        self.assertTrue(hasattr(REGISTER.Installer, 'commit_file'), 'transaction commits are missing')
        class FailingInstaller(REGISTER.Installer):
            def commit_file(inner, stage):
                super(FailingInstaller, inner).commit_file(stage)
                self.write(REGISTER.INDEX + '/new-data', b'preserve')
                raise REGISTER.InstallError('injected failure')
        installer = FailingInstaller(self.root, self.run_command, self.lookup)
        with self.assertRaises(REGISTER.InstallError):
            installer.install(self.key_file())
        self.assertTrue(os.path.exists(self.path(REGISTER.INDEX + '/new-data')))

    def test_empty_new_index_with_changed_metadata_is_preserved(self):
        class FailingInstaller(REGISTER.Installer):
            def commit_file(inner, stage):
                super(FailingInstaller, inner).commit_file(stage)
                os.chmod(self.path(REGISTER.INDEX), 0o750)
                raise REGISTER.InstallError('injected failure')
        installer = FailingInstaller(self.root, self.run_command, self.lookup)
        with self.assertRaises(REGISTER.InstallError):
            installer.install(self.key_file())
        self.assertEqual(stat.S_IMODE(os.stat(self.path(REGISTER.INDEX)).st_mode), 0o750)

    def test_timeout_from_installed_validation_rolls_back(self):
        initial = self.seed_managed()
        def validate(argv, **kwargs):
            if argv[0] == '/usr/bin/doveconf' and argv[2] == self.path(REGISTER.CONFIG):
                raise subprocess.TimeoutExpired(argv, 30, output=b'private-output')
            return subprocess.CompletedProcess(argv, 0, stdout=b'private-output', stderr=b'private-output')
        self.installer.runner = REGISTER.run_command
        with mock.patch.object(REGISTER.subprocess, 'run', side_effect=validate):
            with self.assertRaises(REGISTER.InstallError) as error:
                self.installer.install(self.key_file())
        self.assertNotIn('private-output', str(error.exception))
        self.assert_restored(initial)
        self.assertFalse(os.path.exists(self.path(REGISTER.INDEX)))

    def test_identical_content_with_new_identity_aborts_publication(self):
        initial = self.seed_managed()
        class RacingInstaller(REGISTER.Installer):
            def commit_file(inner, stage):
                if stage['logical'] == REGISTER.AUTHORIZED:
                    temporary = self.write('/home/umeoka/.ssh/concurrent-replacement', initial[REGISTER.AUTHORIZED][0], 0o640)
                    os.replace(temporary, self.path(REGISTER.AUTHORIZED))
                super(RacingInstaller, inner).commit_file(stage)
        installer = RacingInstaller(self.root, self.run_command, self.lookup)
        with self.assertRaises(REGISTER.InstallError):
            installer.install(self.key_file())
        self.assert_restored(initial)

    def test_unsafe_existing_lock_mode_is_rejected(self):
        self.write(REGISTER.LOCK_PATH, b'', 0o666)
        with self.assertRaises(REGISTER.InstallError):
            self.installer.install(self.key_file())

    def test_external_edit_after_publication_is_not_clobbered_by_rollback(self):
        initial = self.seed_managed()
        concurrent = b'# edit after publication\n'
        class RacingInstaller(REGISTER.Installer):
            def commit_file(inner, stage):
                super(RacingInstaller, inner).commit_file(stage)
                if stage['logical'] == REGISTER.AUTHORIZED:
                    self.write(REGISTER.AUTHORIZED, concurrent)
                    raise REGISTER.InstallError('failure after external edit')
        installer = RacingInstaller(self.root, self.run_command, self.lookup)
        with self.assertRaises(REGISTER.RollbackError):
            installer.install(self.key_file())
        initial[REGISTER.AUTHORIZED] = self.file_state(REGISTER.AUTHORIZED)
        self.assertEqual(initial[REGISTER.AUTHORIZED][0], concurrent)
        self.assert_restored(initial)

    def test_validator_timeout_success_failures_and_output_suppression(self):
        argv = ['/usr/bin/doveconf', '-c', '/redacted', '-n']
        secret = 'secret-validation-output'
        results = [None, subprocess.CalledProcessError(1, argv, output=secret, stderr=secret), OSError(secret), subprocess.TimeoutExpired(argv, 30, output=secret, stderr=secret)]
        for result in results:
            with self.subTest(result=type(result).__name__):
                with mock.patch.object(REGISTER.subprocess, 'run', side_effect=result) as execute:
                    with mock.patch('sys.stdout', new_callable=io.StringIO) as out, mock.patch('sys.stderr', new_callable=io.StringIO) as err:
                        if result is None:
                            REGISTER.run_command(argv)
                        else:
                            with self.assertRaises(REGISTER.InstallError) as error:
                                REGISTER.run_command(argv)
                            self.assertNotIn(secret, str(error.exception))
                    self.assertEqual(out.getvalue(), '')
                    self.assertEqual(err.getvalue(), '')
                    self.assertEqual(execute.call_args[1].get('timeout'), 30)
                    self.assertEqual(execute.call_args[1]['stdout'], subprocess.PIPE)
                    self.assertEqual(execute.call_args[1]['stderr'], subprocess.PIPE)

    def test_atomic_validation_failure_preserves_original_and_cleans_temp(self):
        self.write('/etc/managed', b'original')
        def fail(temporary):
            with open(temporary, 'rb') as stream:
                self.assertEqual(stream.read(), b'changed')
            raise REGISTER.InstallError('injected validation failure')
        with self.assertRaises(REGISTER.InstallError):
            self.installer.atomic_install('/etc/managed', b'changed', 0o600, self.lookup('root'), fail)
        with open(self.path('/etc/managed'), 'rb') as stream:
            self.assertEqual(stream.read(), b'original')
        self.assertEqual(os.listdir(self.path('/etc')), ['managed'])

    def test_unsafe_index_directory_and_unexpected_owner_rejected(self):
        self.write('/home/csn/.csn-standby-index/file', b'x')
        os.chmod(self.path(REGISTER.INDEX), 0o777)
        with self.assertRaises(REGISTER.InstallError):
            self.installer.install(self.key_file())
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, descriptor)
        fake = mock.Mock(st_uid=os.getuid() + 10, st_mode=stat.S_IFDIR | 0o700)
        with mock.patch.object(REGISTER.os, 'fstat', return_value=fake):
            with self.assertRaises(REGISTER.InstallError):
                self.installer.check_directory(descriptor, '/')

    def test_cli_rejects_extras_unknown_and_non_root(self):
        for args in [[], ['--public-key-file', 'x', 'extra'], ['--public-key-file', 'x', '--bad'], ['--public-key-file', 'x', '--public-key-file', 'y']]:
            with self.subTest(args=args), mock.patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(REGISTER.main(args, geteuid=lambda: 0), 1)
        with mock.patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(REGISTER.main(['--public-key-file', 'x'], geteuid=lambda: 1000), 1)


if __name__ == '__main__':
    unittest.main()
