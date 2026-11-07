#!/usr/bin/python3
"""Install only the fixed old-server GUID audit endpoint."""
from __future__ import print_function

import datetime
import fcntl
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import uuid


HELPER = '/usr/local/sbin/csn-standby-guid-audit'
SUDOERS_PATH = '/etc/sudoers.d/csn-standby-guid-audit'
LOCK_PATH = '/var/lib/csn-standby-guid-audit.install.lock'
SUDOERS = (b'umeoka ALL=(csn) NOPASSWD: '
           b'/usr/local/sbin/csn-standby-guid-audit\n')


class InstallError(Exception):
    """Sanitized installation failure."""


def run_command(argv):
    allowed = ((len(argv) == 4 and argv[:2] == ['/usr/bin/python3', '-m']
                and argv[2] == 'py_compile' and os.path.isabs(argv[3])),
               (len(argv) == 3 and argv[:2] == ['/usr/sbin/visudo', '-cf']
                and os.path.isabs(argv[2])))
    if not any(allowed):
        raise InstallError('validation command rejected')
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.SubprocessError):
        raise InstallError('validation command failed')
    if result.returncode:
        raise InstallError('validation command failed')
    return b''


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class Installer(object):
    def __init__(self, root='/', payload_dir=None, owner=(0, 0),
                 runner=run_command, checkpoint=None, helper=HELPER,
                 sudoers_path=SUDOERS_PATH, lock_path=LOCK_PATH,
                 sudoers=SUDOERS, payload_name='guid_audit.py',
                 backup_prefix='csn-standby-audit-before-'):
        self.root = os.path.abspath(root)
        self.payload_dir = os.path.abspath(
            payload_dir or os.path.dirname(os.path.abspath(__file__)))
        self.owner = owner
        self.runner = runner
        self.checkpoint = checkpoint or (lambda phase, path: None)
        self.helper = helper
        self.sudoers_path = sudoers_path
        self.lock_path = lock_path
        self.sudoers = sudoers
        self.payload_name = payload_name
        self.backup_prefix = backup_prefix
        self.backup_logical = None
        self.backup_path = None

    def path(self, logical):
        if (not logical.startswith('/') or '..' in logical.split('/')
                or '\x00' in logical):
            raise InstallError('invalid path')
        return os.path.join(self.root, logical.lstrip('/'))

    def _safe_directory(self, path, exact=None):
        try:
            info = os.lstat(path)
        except OSError:
            raise InstallError('required directory unavailable')
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or (info.st_uid, info.st_gid) != self.owner
                or info.st_mode & 0o022
                or (exact is not None and stat.S_IMODE(info.st_mode) != exact)):
            raise InstallError('unsafe directory')
        return info

    def _safe_managed_parent(self, logical):
        parent_logical = os.path.dirname(logical)
        current = self.root
        self._safe_directory(current)
        for component in parent_logical.strip('/').split('/'):
            if not component:
                continue
            current = os.path.join(current, component)
            self._safe_directory(current)
        return current

    def _read_regular(self, path, expected_owner=None, limit=1048576):
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            raise InstallError('unsafe regular file')
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_size > limit
                    or (expected_owner is not None
                        and (info.st_uid, info.st_gid) != expected_owner)
                    or info.st_mode & 0o022):
                raise InstallError('unsafe regular file')
            data = stream.read(limit + 1)
            if len(data) > limit or _signature(info) != _signature(os.fstat(stream.fileno())):
                raise InstallError('file changed while reading')
            return data, _signature(info)

    def _payload(self):
        self._safe_directory(self.payload_dir, 0o700)
        path = os.path.join(self.payload_dir, self.payload_name)
        data, signature = self._read_regular(path, self.owner)
        if not data.startswith(b'#!/usr/bin/python3\n'):
            raise InstallError('invalid helper payload')
        return path, data, signature

    def _existing(self, logical):
        path = self.path(logical)
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return None
        if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or info.st_nlink != 1 or (info.st_uid, info.st_gid) != self.owner
                or info.st_mode & 0o022):
            raise InstallError('unsafe managed file')
        data, unused = self._read_regular(path, self.owner)
        return (data, stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid)

    def _write_atomic(self, logical, data, mode, owner=None):
        owner = self.owner if owner is None else owner
        parent = self._safe_managed_parent(logical)
        target = self.path(logical)
        fd, temporary = tempfile.mkstemp(prefix='.csn-audit-', dir=parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fchown(stream.fileno(), owner[0], owner[1])
                os.fchmod(stream.fileno(), mode)
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def _restore(self, logical, snapshot):
        path = self.path(logical)
        if snapshot is None:
            try:
                info = os.lstat(path)
                if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                    raise InstallError('rollback target unsafe')
                os.unlink(path)
            except FileNotFoundError:
                pass
            return
        self._write_atomic(logical, snapshot[0], snapshot[1],
                           (snapshot[2], snapshot[3]))

    def _make_backup(self, snapshots):
        base = self.path('/var/backups')
        self._safe_directory(base)
        stamp = datetime.datetime.now(
            datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        name = self.backup_prefix + stamp + '-' + uuid.uuid4().hex[:8]
        self.backup_path = os.path.join(base, name)
        self.backup_logical = '/var/backups/' + name
        os.mkdir(self.backup_path, 0o700)
        os.chown(self.backup_path, self.owner[0], self.owner[1])
        os.chmod(self.backup_path, 0o700)
        for logical, snapshot in snapshots.items():
            if snapshot is None:
                continue
            filename = logical.strip('/').replace('/', '__')
            target = os.path.join(self.backup_path, filename)
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(snapshot[0])
                stream.flush()
                os.fchown(stream.fileno(), self.owner[0], self.owner[1])
                os.fchmod(stream.fileno(), 0o600)
                os.fsync(stream.fileno())

    def _validate_sudoers(self):
        parent = self._safe_managed_parent(self.sudoers_path)
        fd, temporary = tempfile.mkstemp(prefix='.csn-sudoers-', dir=parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(self.sudoers)
                stream.flush()
                os.fchmod(stream.fileno(), 0o440)
                os.fsync(stream.fileno())
            self.runner(['/usr/sbin/visudo', '-cf', temporary])
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def _lock(self):
        path = self.path(self.lock_path)
        self._safe_managed_parent(self.lock_path)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or (info.st_uid, info.st_gid) != self.owner
                or stat.S_IMODE(info.st_mode) != 0o600):
            os.close(fd)
            raise InstallError('unsafe install lock')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise InstallError('installer already running')
        return fd

    def install(self):
        lock = self._lock()
        snapshots = {}
        try:
            payload_path, payload, signature = self._payload()
            self.runner(['/usr/bin/python3', '-m', 'py_compile', payload_path])
            self._validate_sudoers()
            self.checkpoint('validated', payload_path)
            current, current_signature = self._read_regular(payload_path, self.owner)
            if current != payload or current_signature != signature:
                raise InstallError('payload changed during validation')
            snapshots = {self.helper: self._existing(self.helper),
                         self.sudoers_path: self._existing(self.sudoers_path)}
            self._make_backup(snapshots)
            try:
                self._write_atomic(self.helper, payload, 0o700)
                self.checkpoint('installed', self.helper)
                self._write_atomic(self.sudoers_path, self.sudoers, 0o440)
                self.checkpoint('installed', self.sudoers_path)
                self.runner(['/usr/sbin/visudo', '-cf', self.path(self.sudoers_path)])
            except BaseException:
                self._restore(self.sudoers_path, snapshots[self.sudoers_path])
                self._restore(self.helper, snapshots[self.helper])
                raise
            files = []
            for logical in (self.helper, self.sudoers_path):
                path = self.path(logical)
                data, unused = self._read_regular(path, self.owner)
                info = os.lstat(path)
                files.append({'path': logical,
                              'mode': format(stat.S_IMODE(info.st_mode), '04o'),
                              'sha256': hashlib.sha256(data).hexdigest()})
            return {'backup_directory': self.backup_logical,
                    'files': files, 'validation': 'passed'}
        except InstallError:
            raise
        except BaseException:
            raise InstallError('installation failed')
        finally:
            os.close(lock)


def main(argv=None, geteuid=os.geteuid):
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        return 64
    if geteuid() != 0:
        return 1
    try:
        report = Installer().install()
        sys.stdout.write(json.dumps(report, sort_keys=True) + '\n')
        return 0
    except InstallError:
        sys.stderr.write('register_old_audit: installation failed\n')
        return 1


if __name__ == '__main__':
    sys.exit(main())
