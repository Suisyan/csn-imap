#!/usr/bin/python3
"""Install the new-server standby endpoint, leaving its timer disabled and inactive.

The CLI has no test-root, owner, command, or payload override. Those constructor
arguments support isolated filesystem tests; production always uses root-owned
payloads beside this script, fixed tools, and the real filesystem.
"""
import base64
import binascii
import contextlib
import ctypes
import fcntl
import hashlib
import json
import os
import stat
import struct
import subprocess
import sys
import tempfile
import uuid


COMMAND_TIMEOUT = 30
MAX_OUTPUT = 65536
PIN = 'SHA256:Do8bGq2Tf7JeXRncu8lLe6KjMLavohLWuWSN+A5+3/s'
HOST = b'118.27.32.100'
UNIT = 'csn-standby-sync.timer'
DIRECTORIES = [('/etc/csn-standby-sync', 0o700),
               ('/var/lib/csn-standby-sync', 0o700),
               ('/var/lib/csn-standby-sync/reports', 0o700)]
FILES = [('/etc/csn-standby-sync/id_ed25519', 0o600, 'private'),
         ('/etc/csn-standby-sync/id_ed25519.pub', 0o644, 'public'),
         ('/etc/csn-standby-sync/known_hosts', 0o644, 'hosts'),
         ('/usr/local/sbin/csn-standby-sync', 0o700, 'runner.py'),
         ('/etc/systemd/system/csn-standby-sync.service', 0o644, 'csn-standby-sync.service'),
         ('/etc/systemd/system/csn-standby-sync.timer', 0o644, 'csn-standby-sync.timer')]
PAYLOADS = ('runner.py', 'csn-standby-sync.service', 'csn-standby-sync.timer')
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


class InstallError(Exception):
    """A deliberately sanitized operational failure."""


def _allowed(argv):
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        return False
    if argv[0:1] == ['/usr/bin/systemctl']:
        return argv[1:] in (['daemon-reload'], ['disable', '--now', UNIT],
                            ['is-enabled', UNIT], ['is-active', UNIT])
    if argv[:3] == ['/usr/bin/ssh-keygen', '-y', '-f']:
        return len(argv) == 4 and os.path.isabs(argv[3])
    if argv[:5] == ['/usr/bin/ssh-keygen', '-l', '-E', 'sha256', '-f']:
        return len(argv) == 6 and os.path.isabs(argv[5])
    return (argv[:3] == ['/usr/bin/systemd-analyze', 'verify', '--man=no']
            and len(argv) == 5 and all(os.path.isabs(a) for a in argv[3:]))


def run_command(argv, *, output=None):
    """Run only the fixed validator/state commands; discard all diagnostic output."""
    if not _allowed(argv):
        raise InstallError('command rejected')
    try:
        state_query = argv[0] == '/usr/bin/systemctl' and argv[1] in ('is-enabled', 'is-active')
        if output is None:
            output = state_query or argv[0] == '/usr/bin/ssh-keygen'
        # TemporaryFile is private and unlinked, and closes even on timeout. Never
        # let subprocess.run collect validator output in Python memory. All stderr
        # and unused stdout go straight to DEVNULL.
        capture_context = tempfile.TemporaryFile(mode='w+b') if output else contextlib.nullcontext(None)
        with capture_context as capture:
            result = subprocess.run(argv, stdin=subprocess.DEVNULL,
                                    stdout=capture if output else subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=COMMAND_TIMEOUT,
                                    check=False, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
            if result.returncode != 0 and not (state_query and result.returncode in (1, 3)):
                raise InstallError('external validation or timer operation failed')
            if not output:
                return b''
            capture.seek(0)
            data = capture.read(MAX_OUTPUT + 1)
            if len(data) > MAX_OUTPUT:
                raise InstallError('external validation output limit exceeded')
            return data
    except (OSError, subprocess.SubprocessError):
        raise InstallError('external validation or timer operation failed') from None


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _identity(info):
    return info.st_dev, info.st_ino


def _safe_path(path):
    if (not isinstance(path, str) or not os.path.isabs(path) or len(os.fsencode(path)) > 4096
            or any(c < 32 or c == 127 for c in os.fsencode(path))
            or any(p in ('.', '..') for p in path.split('/'))):
        raise InstallError('invalid filesystem path')
    return path


def _absolute_dir(path):
    """Open every component without following symlinks, including input parents."""
    _safe_path(path)
    fd = os.open('/', DIR_FLAGS)
    try:
        for part in path.split('/'):
            if not part:
                continue
            child = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read_at(parent, name, limit, owner=None, mode=None):
    fd = os.open(name, READ_FLAGS, dir_fd=parent)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit
                or (owner is not None and (info.st_uid, info.st_gid) != owner)
                or (owner is not None and info.st_mode & 0o022)
                or (mode is not None and stat.S_IMODE(info.st_mode) != mode)):
            raise InstallError('unsafe regular file')
        data = stream.read(limit + 1)
        if len(data) > limit or _signature(info) != _signature(os.fstat(stream.fileno())):
            raise InstallError('file changed while reading')
        return {'data': data, 'signature': _signature(info), 'identity': _identity(info),
                'sha256': hashlib.sha256(data).hexdigest()}


def _read_path(path, limit):
    _safe_path(path)
    parent = _absolute_dir(os.path.dirname(path))
    try:
        return _read_at(parent, os.path.basename(path), limit)
    finally:
        os.close(parent)


def _public(data):
    line = data[:-1] if data.endswith(b'\n') else data
    if not line or any(c < 32 or c > 126 for c in line) or b'PRIVATE KEY' in line:
        raise InstallError('invalid ED25519 public key')
    parts = line.split(b' ')
    if len(parts) < 2 or parts[0] != b'ssh-ed25519' or any(not p for p in parts):
        raise InstallError('invalid ED25519 public key')
    try:
        wire = base64.b64decode(parts[1], validate=True)
    except (ValueError, binascii.Error):
        raise InstallError('invalid ED25519 public key') from None
    prefix = struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32)
    if len(wire) != len(prefix) + 32 or not wire.startswith(prefix) or base64.b64encode(wire) != parts[1]:
        raise InstallError('invalid ED25519 public key')
    return parts[0], parts[1]


def _private(data):
    lines = data.splitlines(keepends=True)
    if (not data.endswith(b'\n') or len(lines) < 3
            or lines[0] != b'-----BEGIN OPENSSH PRIVATE KEY-----\n'
            or lines[-1] != b'-----END OPENSSH PRIVATE KEY-----\n'
            or any(c < 32 and c != 10 or c > 126 for c in data)):
        raise InstallError('invalid private key file')
    try:
        decoded = base64.b64decode(b''.join(lines[1:-1]).replace(b'\n', b''), validate=True)
    except (ValueError, binascii.Error):
        raise InstallError('invalid private key file') from None
    if not decoded.startswith(b'openssh-key-v1\x00'):
        raise InstallError('invalid private key file')


def _rename(parent, source, destination, exchange=False):
    """Linux atomic rename with exchange/no-replace semantics; never clobber a race."""
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        rename = libc.renameat2
    except AttributeError:
        raise InstallError('atomic installation is unavailable') from None
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(parent, os.fsencode(source), parent, os.fsencode(destination), 2 if exchange else 1):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


class Installer:
    def __init__(self, root='/', payload_dir=None, runner=run_command, owner=(0, 0), checkpoint=None):
        self.root = _safe_path(os.path.abspath(root))
        self.payload_dir = _safe_path(payload_dir or os.path.dirname(os.path.abspath(__file__)))
        self.runner, self.owner = runner, owner
        self.checkpoint = checkpoint or (lambda phase, path: None)
        self.backup_dir = None

    def _command(self, argv, *, output=False):
        if not _allowed(argv):
            raise InstallError('command rejected')
        try:
            result = self.runner(argv, output=output)
            if not isinstance(result, bytes) or len(result) > MAX_OUTPUT:
                raise InstallError('external validation output rejected')
            return result
        except (OSError, subprocess.SubprocessError):
            raise InstallError('external validation or timer operation failed') from None

    def _dir_safe(self, fd, exact=None):
        info = os.fstat(fd)
        if (not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid) != self.owner
                or info.st_mode & 0o022
                or (exact is not None and stat.S_IMODE(info.st_mode) != exact)):
            raise InstallError('unsafe installation directory')
        return info

    def _directory(self, logical):
        fd = os.dup(self.rootfd)
        try:
            self._dir_safe(fd)
            current = ''
            for part in logical.split('/'):
                if not part:
                    continue
                child = os.open(part, DIR_FLAGS, dir_fd=fd)
                os.close(fd)
                fd = child
                current += '/' + part
                self._dir_safe(fd, dict(DIRECTORIES).get(current))
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _parent(self, logical):
        parent_path = os.path.dirname(logical)
        fresh = self._directory(parent_path)
        if parent_path in self.parents:
            pinned = self.parents[parent_path]
            same = _identity(os.fstat(fresh)) == _identity(os.fstat(pinned))
            os.close(fresh)
            if not same:
                raise InstallError('installation directory changed')
            return pinned
        self.parents[parent_path] = fresh
        return fresh

    def _ensure_dir(self, path, mode):
        parent = self._parent(path)
        name = os.path.basename(path)
        try:
            os.mkdir(name, mode, dir_fd=parent)
        except FileExistsError:
            fd = os.open(name, DIR_FLAGS, dir_fd=parent)
            try:
                self.directory_identities[path] = _identity(self._dir_safe(fd, mode))
            finally:
                os.close(fd)
            return
        # Record creation before subsequent operations, so any handled failure rolls it back.
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        creation = [parent, name, _identity(info), (info.st_uid, info.st_gid), stat.S_IMODE(info.st_mode)]
        self.created.append(creation)
        fd = os.open(name, DIR_FLAGS, dir_fd=parent)
        try:
            os.fchown(fd, *self.owner)
            creation[3] = self.owner
            os.fchmod(fd, mode)
            creation[4] = mode
            os.fsync(fd)
            self.directory_identities[path] = _identity(self._dir_safe(fd, mode))
        finally:
            os.close(fd)
        os.fsync(parent)

    def _armed_absent(self):
        parent = self._parent('/etc/csn-standby-sync/armed')
        try:
            os.stat('armed', dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise InstallError('armed path must be absent')

    def _load_payloads(self):
        self.payloadfd = _absolute_dir(self.payload_dir)
        self.payload_identity = _identity(self._dir_safe(self.payloadfd))
        result = {name: _read_at(self.payloadfd, name, 1048576, self.owner) for name in PAYLOADS}
        if not result['runner.py']['data'].startswith(b'#!/usr/bin/python3\n'):
            raise InstallError('runner payload lacks the required interpreter')
        return result

    def _recheck_payloads(self, payloads):
        fd = _absolute_dir(self.payload_dir)
        try:
            if _identity(self._dir_safe(fd)) != self.payload_identity:
                raise InstallError('payload directory changed')
            for name in PAYLOADS:
                if _read_at(fd, name, 1048576, self.owner) != payloads[name]:
                    raise InstallError('payload changed during installation')
        finally:
            os.close(fd)

    def _validate_inputs(self, private, public, hosts):
        items = {name: _read_path(path, 16384) for name, path in
                 (('private', private), ('public', public), ('hosts', hosts))}
        data = {name: item['data'] for name, item in items.items()}
        _private(data['private'])
        expected = _public(data['public'])
        line = data['hosts'][:-1] if data['hosts'].endswith(b'\n') else data['hosts']
        parts = line.split(b' ')
        if len(parts) != 3 or parts[0] != HOST:
            raise InstallError('exactly one pinned old-server host key required')
        _public(b' '.join(parts[1:]))
        with tempfile.TemporaryDirectory(prefix='csn-new-validate-') as temporary:
            paths = {}
            for name in ('private', 'public', 'hosts'):
                path = os.path.join(temporary, name)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data[name])
                paths[name] = path
            derived = self._command(['/usr/bin/ssh-keygen', '-y', '-f', paths['private']], output=True)
            if _public(derived) != expected:
                raise InstallError('private and public key do not match')
            self._command(['/usr/bin/ssh-keygen', '-l', '-E', 'sha256', '-f', paths['public']])
            fingerprint = self._command(['/usr/bin/ssh-keygen', '-l', '-E', 'sha256', '-f', paths['hosts']], output=True)
            fields = fingerprint.strip().split()
            if len(fingerprint.splitlines()) != 1 or len(fields) < 3 or fields[0] != b'256' or fields[1] != PIN.encode() or fields[-1] != b'(ED25519)':
                raise InstallError('old-server host-key fingerprint rejected')
        return data

    def _snapshot(self, parent, name, mode):
        try:
            return _read_at(parent, name, 1048576, self.owner, mode)
        except FileNotFoundError:
            return None

    def _write(self, parent, name, data, mode):
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=parent)
        try:
            stream = os.fdopen(fd, 'wb')
            fd = None
            with stream:
                stream.write(data)
                os.fchown(stream.fileno(), *self.owner)
                os.fchmod(stream.fileno(), mode)
                stream.flush()
                os.fsync(stream.fileno())
                info = os.fstat(stream.fileno())
                written = {'data': data, 'signature': _signature(info), 'identity': _identity(info),
                           'sha256': hashlib.sha256(data).hexdigest()}
            # A stage is not yet registered by the caller. Include parent fsync
            # and every other post-create operation in this cleanup guard.
            os.fsync(parent)
            return written
        except BaseException:
            os.unlink(name, dir_fd=parent)
            raise
        finally:
            if fd is not None:
                os.close(fd)

    def _backup(self):
        parent = self._parent('/var/backups/placeholder')
        name = 'csn-standby-sync-before-' + uuid.uuid4().hex
        os.mkdir(name, 0o700, dir_fd=parent)
        self.backup_dir = '/var/backups/' + name
        self.backupfd = os.open(name, DIR_FLAGS, dir_fd=parent)
        os.fchown(self.backupfd, *self.owner)
        os.fchmod(self.backupfd, 0o700)
        os.fsync(parent)

    def _verify(self, paths):
        self._command(['/usr/bin/systemd-analyze', 'verify', '--man=no'] + paths)

    def _fd_path(self, fd, name):
        # Directory descriptors are CLOEXEC; the child refers to the live parent.
        return '/proc/{}/fd/{}/{}'.format(os.getpid(), fd, name)

    def _prepare(self, data):
        for path, mode, source in FILES:
            parent, name = self._parent(path), os.path.basename(path)
            before = self._snapshot(parent, name, mode)
            if before is not None:
                self._write(self.backupfd, str(len(self.entries)) + '.before', before['data'], 0o600)
            stage = '.csn-standby-' + uuid.uuid4().hex
            installed = self._write(parent, stage, data[source], mode)
            entry = {'path': path, 'parent': parent, 'name': name, 'mode': mode,
                     'before': before, 'stage': stage, 'committed': False,
                     'installed': installed}
            # Register the identity returned by the writer before any readback
            # can fail, so rollback always knows about a completed private stage.
            self.entries.append(entry)
            if self._snapshot(parent, stage, mode) != installed:
                raise InstallError('staged file changed')
        units = []
        for name in PAYLOADS[1:]:
            self._write(self.backupfd, name, data[name], 0o644)
            units.append(self._fd_path(self.backupfd, name))
        return units

    def _commit(self, entry):
        self.checkpoint('before_commit', entry['path'])
        self._parent(entry['path'])
        parent, name, stage = entry['parent'], entry['name'], entry['stage']
        if self._snapshot(parent, name, entry['mode']) != entry['before']:
            raise InstallError('concurrent destination change detected')
        if self._snapshot(parent, stage, entry['mode']) != entry['installed']:
            raise InstallError('staged file changed')
        _rename(parent, stage, name, exchange=entry['before'] is not None)
        entry['committed'] = True
        # rename changes ctime; record the installed version and validate old identity/content.
        actual = self._snapshot(parent, name, entry['mode'])
        if actual is None or actual['identity'] != entry['installed']['identity'] or actual['sha256'] != entry['installed']['sha256']:
            raise InstallError('concurrent destination change detected')
        entry['installed'] = actual
        if entry['before'] is not None:
            old = self._snapshot(parent, stage, entry['mode'])
            if old is None or old['identity'] != entry['before']['identity'] or old['sha256'] != entry['before']['sha256']:
                raise InstallError('concurrent destination change detected')
        os.fsync(parent)
        self.checkpoint('committed', entry['path'])

    def _rollback(self):
        conflicts = False
        for entry in reversed(self.entries):
            parent, name, stage = entry['parent'], entry['name'], entry['stage']
            try:
                if entry['committed']:
                    current = self._snapshot(parent, name, entry['mode'])
                    if current != entry['installed']:
                        conflicts = True
                        continue
                    if entry['before'] is None:
                        # Move atomically first; verify moved data before deleting it.
                        _rename(parent, name, stage)
                        moved = self._snapshot(parent, stage, entry['mode'])
                        if moved is None or moved['identity'] != current['identity'] or moved['sha256'] != current['sha256']:
                            _rename(parent, stage, name)
                            conflicts = True
                            continue
                    else:
                        previous = self._snapshot(parent, stage, entry['mode'])
                        if previous is None:
                            # A later final-cleanup failure can occur after an earlier
                            # old stage was removed. Restore from the pinned snapshot.
                            self._write(parent, stage, entry['before']['data'], entry['mode'])
                        elif (previous['identity'] != entry['before']['identity']
                              or previous['sha256'] != entry['before']['sha256']):
                            conflicts = True
                            continue
                        _rename(parent, stage, name, exchange=True)
                        moved = self._snapshot(parent, stage, entry['mode'])
                        if moved is None or moved['identity'] != current['identity'] or moved['sha256'] != current['sha256']:
                            _rename(parent, stage, name, exchange=True)
                            conflicts = True
                            continue
                    entry['committed'] = False
                self._remove_stage(entry)
                os.fsync(parent)
            except (OSError, InstallError):
                conflicts = True
        for parent, name, identity, owner, mode in reversed(self.created):
            try:
                current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if (_identity(current) != identity or (current.st_uid, current.st_gid) != owner
                        or stat.S_IMODE(current.st_mode) != mode):
                    conflicts = True
                    continue
                os.rmdir(name, dir_fd=parent)
                os.fsync(parent)
            except OSError:
                # Nonempty directories may contain unrelated concurrent files; preserve them.
                conflicts = True
        return conflicts

    def _remove_stage(self, entry):
        parent, name = entry['parent'], entry['stage']
        try:
            current = self._snapshot(parent, name, entry['mode'])
        except FileNotFoundError:
            return
        if current is None:
            return
        expected = entry['before'] if entry['committed'] and entry['before'] is not None else entry['installed']
        if current['identity'] != expected['identity'] or current['sha256'] != expected['sha256']:
            raise InstallError('concurrent staging change preserved')
        os.unlink(name, dir_fd=parent)

    def _validate_final_installation(self):
        # Reports has no managed file beneath it, so checking only file parents
        # misses its identity and permissions. Re-open every managed directory.
        for path, mode in DIRECTORIES:
            fd = self._directory(path)
            try:
                current = self._dir_safe(fd, mode)
                if _identity(current) != self.directory_identities[path]:
                    raise InstallError('managed directory changed')
            finally:
                os.close(fd)
        for entry in self.entries:
            self._parent(entry['path'])
            if self._snapshot(entry['parent'], entry['name'], entry['mode']) != entry['installed']:
                raise InstallError('concurrent destination change detected')
        self._armed_absent()

    def install(self, private, public, hosts):
        self.parents, self.created, self.entries = {}, [], []
        self.directory_identities = {}
        self.rootfd = self.payloadfd = self.backupfd = self.lockfd = None
        self.backup_dir = None
        self.daemon_reload_attempted = False
        try:
            self.rootfd = _absolute_dir(self.root)
            self._dir_safe(self.rootfd)
            # Lock a pinned, safe existing directory: no lockfile can be replaced or left behind.
            self.lockfd = self._directory('/var/lib')
            fcntl.flock(self.lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            payloads = self._load_payloads()
            data = self._validate_inputs(private, public, hosts)
            data.update({name: value['data'] for name, value in payloads.items()})
            for path, mode in DIRECTORIES:
                self._ensure_dir(path, mode)
            self._armed_absent()
            self._backup()
            staged_units = self._prepare(data)
            self._recheck_payloads(payloads)
            self._armed_absent()
            for entry in self.entries:
                if entry['path'] == '/etc/systemd/system/csn-standby-sync.service':
                    # verify checks ExecStart executability. Commit the runner first
                    # so an initial install validates the exact, unchanged units.
                    self._verify(staged_units)
                    self._recheck_payloads(payloads)
                self._commit(entry)
            self._verify([self._fd_path(self._parent('/etc/systemd/system/' + name), name)
                          for name in PAYLOADS[1:]])
            self._recheck_payloads(payloads)
            self._armed_absent()
            # The manager may finish even if its client then fails or times out.
            self.daemon_reload_attempted = True
            self._command(['/usr/bin/systemctl', 'daemon-reload'])
            self._command(['/usr/bin/systemctl', 'disable', '--now', UNIT])
            enabled = self._command(['/usr/bin/systemctl', 'is-enabled', UNIT], output=True).strip()
            active = self._command(['/usr/bin/systemctl', 'is-active', UNIT], output=True).strip()
            if enabled != b'disabled' or active != b'inactive':
                raise InstallError('timer must be disabled and inactive')
            self._armed_absent()
            for entry in self.entries:
                self._parent(entry['path'])
                if self._snapshot(entry['parent'], entry['name'], entry['mode']) != entry['installed']:
                    raise InstallError('concurrent destination change detected')
            for entry in self.entries:
                self._remove_stage(entry)
            self._validate_final_installation()
            return {'files': [{'path': e['path'], 'mode': format(e['mode'], '04o'),
                               'sha256': e['installed']['sha256']} for e in self.entries],
                    'directories': [{'path': p, 'mode': format(m, '04o')} for p, m in DIRECTORIES],
                    'validation': 'passed', 'timer': {'enabled': 'disabled', 'active': 'inactive'},
                    'armed': 'absent', 'backup_directory': self.backup_dir}
        except (OSError, InstallError, subprocess.SubprocessError):
            conflicts = self._rollback()
            message = 'installation failed; rollback ' + ('preserved concurrent changes' if conflicts else 'completed')
            if self.daemon_reload_attempted:
                try:
                    # Disk rollback may have restored or removed loaded units.
                    # Reload only; never re-enable or reactivate the old timer.
                    self._command(['/usr/bin/systemctl', 'daemon-reload'])
                except InstallError:
                    message += '; recovery daemon-reload failed'
            if self.backup_dir:
                message += '; backup directory: ' + self.backup_dir
            raise InstallError(message) from None
        finally:
            for fd in set(list(self.parents.values()) + [self.backupfd, self.payloadfd, self.lockfd, self.rootfd]):
                if fd is not None:
                    os.close(fd)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if os.geteuid() != 0:
        print('root is required', file=sys.stderr)
        return 1
    flags = ('--private-key', '--public-key', '--known-hosts')
    if (len(argv) != 6 or set(argv[::2]) != set(flags) or any(a.startswith('--') for a in argv[1::2])):
        print('exactly --private-key PATH --public-key PATH --known-hosts PATH required', file=sys.stderr)
        return 1
    values = dict(zip(argv[::2], argv[1::2]))
    try:
        result = Installer().install(values[flags[0]], values[flags[1]], values[flags[2]])
        print(json.dumps(result, sort_keys=True))
        return 0
    except InstallError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
