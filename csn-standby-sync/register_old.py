#!/usr/bin/python3
"""Install only the restricted old-server endpoint (Python 3.5 or newer)."""
import base64
import binascii
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import pwd
import stat
import struct
import subprocess
import sys
import tempfile
import uuid


BEGIN = b'# BEGIN CSN STANDBY SOURCE MANAGED KEY'
END = b'# END CSN STANDBY SOURCE MANAGED KEY'
HELPER = '/usr/local/sbin/csn-standby-dsync-source'
CONFIG = '/etc/csn-standby-source.conf'
SUDOERS_PATH = '/etc/sudoers.d/csn-standby-source'
AUTHORIZED = '/home/umeoka/.ssh/authorized_keys'
INDEX = '/home/csn/.csn-standby-index'
LOCK_PATH = '/var/lib/csn-standby-source.install.lock'
VALIDATION_TIMEOUT = 30
SUDOERS = b'umeoka ALL=(root) NOPASSWD: /usr/local/sbin/csn-standby-dsync-source\n'


class InstallError(Exception):
    pass


class RollbackError(InstallError):
    pass


def run_command(argv):
    """Capture validation output, which must never reach reports or stdout."""
    try:
        subprocess.run(argv, check=True, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                       timeout=VALIDATION_TIMEOUT)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise InstallError('validation failed: ' + os.path.basename(argv[0]))


def uid_lookup(name):
    try:
        entry = pwd.getpwnam(name)
    except KeyError:
        raise InstallError('required account missing')
    return entry.pw_uid, entry.pw_gid


def read_public_key(path, runner=run_command):
    """Read a no-follow snapshot and verify both the SSH wire format and tool parser."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise InstallError('public key must be a regular non-symlink file')
            data = stream.read(16385)
    except OSError:
        raise InstallError('cannot read public key file safely')
    line = data[:-1] if data.endswith(b'\n') else data
    if (not line or len(data) > 16384 or any(c < 32 or c > 126 for c in line)
            or b'PRIVATE KEY' in line or BEGIN in line or END in line):
        raise InstallError('invalid ED25519 public key')
    fields = line.split(b' ')
    if len(fields) < 2 or fields[0] != b'ssh-ed25519' or not fields[1]:
        raise InstallError('exactly one ED25519 public key required')
    try:
        wire = base64.b64decode(fields[1], validate=True)
    except (ValueError, binascii.Error):
        raise InstallError('invalid public key encoding')
    if base64.b64encode(wire) != fields[1]:
        raise InstallError('noncanonical public key encoding')
    prefix = struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32)
    if len(wire) != len(prefix) + 32 or not wire.startswith(prefix):
        raise InstallError('invalid ED25519 public key structure')
    fd, temporary = tempfile.mkstemp(prefix='csn-public-key-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(line + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        runner(['/usr/bin/ssh-keygen', '-l', '-E', 'sha256', '-f', temporary])
    finally:
        os.unlink(temporary)
    return line


def render_authorized_keys(existing, key):
    block = (BEGIN + b'\nrestrict,command="sudo -n ' + HELPER.encode('ascii')
             + b'" ' + key + b'\n' + END + b'\n')
    begins = existing.count(BEGIN)
    ends = existing.count(END)
    if not begins and not ends:
        separator = b'' if not existing or existing.endswith(b'\n') else b'\n'
        return existing + separator + block
    if begins != 1 or ends != 1:
        raise InstallError('ambiguous managed authorized_keys markers')
    start = existing.index(BEGIN)
    end = existing.index(END)
    after = end + len(END)
    if (end < start or (start and existing[start - 1:start] != b'\n')
            or existing[start + len(BEGIN):start + len(BEGIN) + 1] != b'\n'
            or existing[end - 1:end] != b'\n'
            or (after < len(existing) and existing[after:after + 1] != b'\n')):
        raise InstallError('malformed managed authorized_keys markers')
    if after < len(existing):
        after += 1
    return existing[:start] + block + existing[after:]


class Installer:
    """All filesystem operations stay inside root; commands/identities are injectable.

    Directory descriptors pin each validated parent against symlink/rename races.
    The production CLI always uses root='/' and the real account database.
    """
    def __init__(self, root='/', runner=run_command, lookup=uid_lookup):
        self.root = os.path.abspath(root)
        self.runner = runner
        self.accounts = dict((name, lookup(name)) for name in ('root', 'csn', 'umeoka'))
        self.backup_dir = None
        self.stages = []
        self.parents = {}
        self.new_index = None

    def path(self, logical):
        if not logical.startswith('/') or '..' in logical.split('/'):
            raise InstallError('invalid installation path')
        return os.path.join(self.root, logical.lstrip('/'))

    def directory_owner(self, logical):
        for name in ('csn', 'umeoka'):
            home = '/home/' + name
            if logical == home or logical.startswith(home + '/'):
                return self.accounts[name]
        return self.accounts['root']

    def check_directory(self, fd, logical):
        info = os.fstat(fd)
        permitted = (self.accounts['root'][0], self.directory_owner(logical)[0])
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in permitted
                or info.st_mode & 0o022):
            raise InstallError('unsafe directory: ' + logical)

    def open_directory(self, logical, create=False):
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            fd = os.open(self.root, flags)
        except OSError:
            raise InstallError('unsafe installation root')
        try:
            self.check_directory(fd, '/')
            current = ''
            for part in logical.strip('/').split('/') if logical != '/' else []:
                if part in ('', '.', '..'):
                    raise InstallError('invalid directory path')
                current += '/' + part
                try:
                    next_fd = os.open(part, flags, dir_fd=fd)
                except FileNotFoundError:
                    if not create:
                        raise
                    owner = self.directory_owner(current)
                    mode = 0o700 if current.startswith('/home/') else 0o755
                    os.mkdir(part, mode, dir_fd=fd)
                    next_fd = os.open(part, flags, dir_fd=fd)
                    os.fchown(next_fd, owner[0], owner[1])
                    os.fchmod(next_fd, mode)
                    os.fsync(fd)
                try:
                    self.check_directory(next_fd, current)
                except Exception:
                    os.close(next_fd)
                    raise
                os.close(fd)
                fd = next_fd
            return fd
        except OSError:
            os.close(fd)
            raise InstallError('unsafe directory: ' + logical)
        except Exception:
            os.close(fd)
            raise

    def existing_file(self, parent_fd, name, owner):
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        except FileNotFoundError:
            return None, None
        except OSError:
            raise InstallError('unsafe managed file')
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid not in (self.accounts['root'][0], owner[0])
                    or info.st_mode & 0o022):
                raise InstallError('unsafe managed file')
            return stream.read(), info

    def write_atomic(self, parent_fd, data, mode, owner, name, validator=None):
        temporary = '.csn-standby-' + uuid.uuid4().hex
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent_fd)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fchown(stream.fileno(), owner[0], owner[1])
                os.fchmod(stream.fileno(), mode)
                os.fsync(stream.fileno())
            if validator:
                # A pinned parent descriptor avoids following a replaced user directory.
                validator('/proc/{0}/fd/{1}/{2}'.format(os.getpid(), parent_fd, temporary))
            os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.fsync(parent_fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except FileNotFoundError:
                pass

    def backup(self, logical, data):
        if self.backup_dir is None:
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
            logical_dir = '/var/backups/csn-standby-source-before-' + stamp + '-' + uuid.uuid4().hex[:8]
            parent_fd = self.open_directory('/var/backups', create=True)
            try:
                os.mkdir(os.path.basename(logical_dir), 0o700, dir_fd=parent_fd)
                fd = os.open(os.path.basename(logical_dir), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
                try:
                    os.fchown(fd, *self.accounts['root'])
                    os.fchmod(fd, 0o700)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
            self.backup_dir = self.path(logical_dir)
        backup_logical = '/' + os.path.relpath(self.backup_dir, self.root) + logical
        parent = os.path.dirname(backup_logical)
        fd = self.open_directory(parent, create=True)
        try:
            name = os.path.basename(backup_logical)
            previous, _ = self.existing_file(fd, name, self.accounts['root'])
            if previous is None:
                self.write_atomic(fd, data, 0o600, self.accounts['root'], name)
        finally:
            os.close(fd)

    def atomic_install(self, logical, data, mode, owner, validator=None):
        fd = self.open_directory(os.path.dirname(logical), create=True)
        try:
            name = os.path.basename(logical)
            previous, info = self.existing_file(fd, name, owner)
            if (previous == data and stat.S_IMODE(info.st_mode) == mode
                    and (info.st_uid, info.st_gid) == owner):
                if validator:
                    validator('/proc/{0}/fd/{1}/{2}'.format(os.getpid(), fd, name))
                return
            if previous is not None:
                self.backup(logical, previous)
            self.write_atomic(fd, data, mode, owner, name, validator)
        finally:
            os.close(fd)

    @contextlib.contextmanager
    def install_lock(self):
        """Keep a persistent root-only inode locked; never replace or unlink it."""
        parent = self.open_directory(os.path.dirname(LOCK_PATH), create=True)
        fd = None
        try:
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
            try:
                fd = os.open(os.path.basename(LOCK_PATH), flags | os.O_CREAT | os.O_EXCL,
                             0o600, dir_fd=parent)
                os.fchown(fd, *self.accounts['root'])
                os.fchmod(fd, 0o600)
                os.fsync(fd)
                os.fsync(parent)
            except FileExistsError:
                fd = os.open(os.path.basename(LOCK_PATH), flags, dir_fd=parent)
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or (info.st_uid, info.st_gid) != self.accounts['root']
                    or stat.S_IMODE(info.st_mode) != 0o600):
                raise InstallError('unsafe installer lock')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise InstallError('installer already running or lock unavailable')
            yield
        except OSError:
            raise InstallError('installer lock unavailable')
        finally:
            if fd is not None:
                os.close(fd)
            os.close(parent)

    def parent_for(self, logical):
        parent = os.path.dirname(logical)
        if parent not in self.parents:
            self.parents[parent] = self.open_directory(parent, create=True)
        return self.parents[parent]

    def snapshot(self, logical, owner):
        data, info = self.existing_file(self.parent_for(logical), os.path.basename(logical), owner)
        if info is None:
            return None
        return {'data': data, 'owner': (info.st_uid, info.st_gid),
                'mode': stat.S_IMODE(info.st_mode), 'identity': self.identity(info)}

    @staticmethod
    def identity(info):
        return (info.st_dev, info.st_ino, info.st_uid, info.st_gid,
                info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def prepare_stage(self, logical, data, mode, owner, before):
        parent = self.parent_for(logical)
        temporary = '.csn-standby-' + uuid.uuid4().hex
        stage = {'logical': logical, 'data': data, 'mode': mode, 'owner': owner,
                 'before': before, 'parent': parent, 'temporary': temporary,
                 'committed': False}
        # Register before creation so failed writes/ownership changes are cleaned.
        self.stages.append(stage)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fchown(stream.fileno(), *owner)
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        return stage

    @staticmethod
    def staged_path(stage):
        return '/proc/{0}/fd/{1}/{2}'.format(os.getpid(), stage['parent'], stage['temporary'])

    def commit_file(self, stage):
        before = stage['before']
        if (before is not None and before['data'] == stage['data']
                and before['mode'] == stage['mode'] and before['owner'] == stage['owner']):
            if self.snapshot(stage['logical'], stage['owner']) != before:
                raise InstallError('managed file changed during installation')
            return
        if before is not None:
            self.backup(stage['logical'], before['data'])
        # Read identity, metadata and bytes again immediately before replacement.
        # The persistent lock serializes cooperating installers; parent FDs stay pinned.
        if self.snapshot(stage['logical'], stage['owner']) != before:
            raise InstallError('managed file changed during installation')
        os.replace(stage['temporary'], os.path.basename(stage['logical']),
                   src_dir_fd=stage['parent'], dst_dir_fd=stage['parent'])
        stage['committed'] = True
        os.fsync(stage['parent'])

    def inspect_index(self):
        parent = self.parent_for(INDEX)
        try:
            fd = os.open(os.path.basename(INDEX), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                         dir_fd=parent)
        except FileNotFoundError:
            return None
        except OSError:
            raise InstallError('unsafe index directory')
        try:
            self.check_directory(fd, INDEX)
            info = os.fstat(fd)
            if ((info.st_uid, info.st_gid) != self.accounts['csn']
                    or stat.S_IMODE(info.st_mode) != 0o700):
                raise InstallError('index must be owned by csn:csn with mode 0700')
            return info
        finally:
            os.close(fd)

    def create_index(self):
        """Leave existing directory ownership, permissions and timestamps untouched."""
        existing = self.inspect_index()
        if existing is not None:
            return stat.S_IMODE(existing.st_mode)
        parent = self.parent_for(INDEX)
        name = os.path.basename(INDEX)
        try:
            os.mkdir(name, 0o700, dir_fd=parent)
        except FileExistsError:
            existing = self.inspect_index()
            if existing is None:
                raise InstallError('index changed during installation')
            return stat.S_IMODE(existing.st_mode)
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        self.new_index = {'fd': fd, 'parent': parent, 'name': name,
                          'identity': self.identity(os.fstat(fd))}
        try:
            os.fchown(fd, *self.accounts['csn'])
            os.fchmod(fd, 0o700)
            os.fsync(fd)
            os.fsync(parent)
        finally:
            self.new_index['identity'] = self.identity(os.fstat(fd))
        return 0o700

    def rollback(self):
        failures = []
        for stage in reversed(self.stages):
            if not stage['committed']:
                continue
            try:
                current = self.snapshot(stage['logical'], stage['owner'])
                if (stage['logical'] == AUTHORIZED and (current is None
                        or current['data'] != stage['data'] or current['mode'] != stage['mode']
                        or current['owner'] != stage['owner'])):
                    raise InstallError('authorized_keys changed after publication')
                before = stage['before']
                name = os.path.basename(stage['logical'])
                if before is None:
                    os.unlink(name, dir_fd=stage['parent'])
                    os.fsync(stage['parent'])
                else:
                    self.write_atomic(stage['parent'], before['data'], before['mode'],
                                      before['owner'], name)
            except (OSError, InstallError):
                failures.append(stage['logical'])
        if self.new_index is not None:
            index = self.new_index
            try:
                current = os.stat(index['name'], dir_fd=index['parent'], follow_symlinks=False)
                if (self.identity(current) == index['identity']
                        and not os.listdir(index['fd'])):
                    os.rmdir(index['name'], dir_fd=index['parent'])
                    os.fsync(index['parent'])
            except FileNotFoundError:
                pass
            except OSError:
                failures.append(INDEX)
        if failures:
            raise RollbackError('installation failed; rollback incomplete')

    def cleanup_transaction(self):
        for stage in self.stages:
            try:
                os.unlink(stage['temporary'], dir_fd=stage['parent'])
            except FileNotFoundError:
                pass
        if self.new_index is not None:
            os.close(self.new_index['fd'])
        for descriptor in self.parents.values():
            os.close(descriptor)
        self.parents = {}

    def install_locked(self, key):
        self.stages = []
        self.parents = {}
        self.new_index = None
        self.backup_dir = None
        root = self.accounts['root']
        owner_by_path = {HELPER: root, CONFIG: root, SUDOERS_PATH: root,
                         AUTHORIZED: self.accounts['umeoka']}
        try:
            # Capture all four managed file states before any managed mutation.
            initial = dict((logical, self.snapshot(logical, owner))
                           for logical, owner in owner_by_path.items())
            self.inspect_index()
            payload_dir = os.path.dirname(os.path.abspath(__file__))
            for filename, logical, mode in [('old_source.py', HELPER, 0o700), ('old_source.conf', CONFIG, 0o644)]:
                with open(os.path.join(payload_dir, filename), 'rb') as stream:
                    self.prepare_stage(logical, stream.read(), mode, root, initial[logical])
            sudoers = self.prepare_stage(SUDOERS_PATH, SUDOERS, 0o440, root, initial[SUDOERS_PATH])
            self.runner(['/usr/sbin/visudo', '-cf', self.staged_path(sudoers)])
            configuration = next(stage for stage in self.stages if stage['logical'] == CONFIG)
            self.runner(['/usr/bin/doveconf', '-c', self.staged_path(configuration), '-n'])
            index_mode = self.create_index()
            for stage in self.stages:
                self.commit_file(stage)
            self.runner(['/usr/sbin/visudo', '-cf', self.path('/etc/sudoers')])
            self.runner(['/usr/bin/doveconf', '-c', self.path(CONFIG), '-n'])
            # Fresh snapshot preserves unrelated changes made during validation.
            current = self.snapshot(AUTHORIZED, self.accounts['umeoka'])
            authorized = render_authorized_keys(current['data'] if current else b'', key)
            stage = self.prepare_stage(AUTHORIZED, authorized, 0o600,
                                       self.accounts['umeoka'], current)
            self.commit_file(stage)
            reports = [{'path': item['logical'], 'mode': '{:04o}'.format(item['mode']),
                        'sha256': hashlib.sha256(item['data']).hexdigest()} for item in self.stages]
            reports.append({'path': INDEX, 'mode': '{:04o}'.format(index_mode)})
            return {'installed': reports, 'backup_directory': self.backup_dir,
                    'validation': {'public_key': 'passed', 'sudoers_fragment': 'passed',
                                   'sudoers': 'passed', 'dovecot_config': 'passed'}}
        except Exception:
            self.rollback()
            raise
        finally:
            self.cleanup_transaction()

    def install(self, key_file):
        if self.root == '/' and self.accounts['csn'] != (1001, 1001):
            raise InstallError('csn uid/gid must match fixed Dovecot configuration')
        with self.install_lock():
            key = read_public_key(key_file, self.runner)
            return self.install_locked(key)


def main(argv=None, geteuid=os.geteuid):
    if argv is None:
        argv = sys.argv[1:]
    if geteuid() != 0:
        sys.stderr.write('register_old: root required\n')
        return 1
    if len(argv) != 2 or argv[0] != '--public-key-file' or argv[1].startswith('--'):
        sys.stderr.write('usage: register_old.py --public-key-file PATH\n')
        return 1
    try:
        report = Installer().install(argv[1])
    except RollbackError:
        sys.stderr.write('register_old: installation failed; rollback incomplete\n')
        return 1
    except (InstallError, OSError):
        sys.stderr.write('register_old: installation or validation failed\n')
        return 1
    sys.stdout.write(json.dumps(report, sort_keys=True) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
