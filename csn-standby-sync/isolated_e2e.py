#!/usr/bin/python3
"""Fixed isolated reverse-backup proof. Never print captured diagnostics."""
import csv
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import pwd
import re
import signal
import stat
import subprocess
import sys
import time
import uuid

BASE = '/home/mailadmin/csn-migration'
TARGET = BASE + '/initial-sync-receiver/Maildir'
CONFIG = BASE + '/initial-sync-receiver/dovecot.conf'
REPORT_DIR = BASE + '/standby-e2e'
STAGING = BASE + '/standby-sync-staging'
PRIVATE_KEY = STAGING + '/id_ed25519'
KNOWN_HOSTS = STAGING + '/known_hosts'
ARMED_COMMAND = ['/usr/bin/sudo','-n','/usr/local/sbin/csn-standby-armed-status']


def build_command():
    return ['/usr/bin/doveadm','-c',CONFIG,'backup','-R','-l','60','-T','600',
            '/usr/bin/ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes',
            '-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+KNOWN_HOSTS,
            '-i',PRIVATE_KEY,'umeoka@118.27.32.100']


def validate_target(path=TARGET, *, uid=None, lstat=os.lstat):
    if path != TARGET:
        raise RuntimeError('target_rejected')
    uid = pwd.getpwnam('mailadmin').pw_uid if uid is None else uid
    # Check every mailadmin-owned ancestor too, so a parent symlink cannot escape.
    for part in [BASE.rsplit('/',1)[0], BASE, BASE+'/initial-sync-receiver', TARGET]:
        try:
            info=lstat(part)
        except OSError:
            raise RuntimeError('target_unavailable') from None
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022:
            raise RuntimeError('target_unsafe')
    try: info=lstat(CONFIG)
    except OSError: raise RuntimeError('config_unavailable') from None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022:
        raise RuntimeError('config_unsafe')


def validate_transport_files(*, uid=None, lstat=os.lstat):
    uid = pwd.getpwnam('mailadmin').pw_uid if uid is None else uid
    for path in (BASE.rsplit('/', 1)[0], BASE, STAGING):
        try: info = lstat(path)
        except OSError: raise RuntimeError('transport_directory_unavailable') from None
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022:
            raise RuntimeError('transport_directory_unsafe')
    for path, forbidden in ((PRIVATE_KEY, 0o077), (KNOWN_HOSTS, 0o022)):
        try: info = lstat(path)
        except OSError: raise RuntimeError('transport_file_unavailable') from None
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_mode & forbidden:
            raise RuntimeError('transport_file_unsafe')
    return True


def validate_no_symlinks(root=TARGET):
    def traversal_error(_error):
        raise RuntimeError('target_tree_unavailable') from None
    for current, directories, files in os.walk(
            root, topdown=True, followlinks=False, onerror=traversal_error):
        for name in directories + files:
            try: info = os.lstat(os.path.join(current, name))
            except OSError: raise RuntimeError('target_tree_unavailable') from None
            if stat.S_ISLNK(info.st_mode):
                raise RuntimeError('target_tree_symlink_rejected')
    return True


def acquire_lock(path=REPORT_DIR+'/run.lock', *, uid=None):
    uid = pwd.getpwnam('mailadmin').pw_uid if uid is None else uid
    info=os.lstat(os.path.dirname(path))
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077:
        raise RuntimeError('report_directory_unsafe')
    fd=-1
    try:
        fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077 or info.st_nlink != 1:
            raise RuntimeError('lock_unsafe')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return fd
    except (OSError,RuntimeError):
        if fd>=0:os.close(fd)
        raise RuntimeError('lock_unavailable') from None


def validate_config(text):
    root=BASE+'/initial-sync-receiver'
    if any(value in text for value in ('!include','/home/csn','~','%','$','\\')):
        raise RuntimeError('config_expansion_rejected')
    allowed={'protocols','ssl','log_path','info_log_path','mail_home','mail_location','mail_uid','mail_gid','mail_plugins','first_valid_uid','stats_writer_socket_path','inbox','separator','prefix'}
    values={}
    for raw in text.splitlines():
        line=raw.split('#',1)[0].strip()
        if not line or line in ('namespace inbox {','}'):continue
        if '=' not in line:raise RuntimeError('config_syntax_rejected')
        key,value=(part.strip() for part in line.split('=',1))
        if key not in allowed or key in values:raise RuntimeError('config_setting_rejected')
        values[key]=value
    home=values.get('mail_home','')
    location=values.get('mail_location','')
    if not home.startswith(root+'/') and home!=root:raise RuntimeError('config_home_rejected')
    if '..' in Path(home).parts or os.path.normpath(home)!=home:raise RuntimeError('config_home_rejected')
    if location != 'maildir:'+TARGET:raise RuntimeError('config_mail_location_rejected')
    for key in ('log_path','info_log_path'):
        if values.get(key) not in (None,'/dev/stderr'):raise RuntimeError('config_log_rejected')
    if values.get('mail_plugins','') or values.get('stats_writer_socket_path',''):raise RuntimeError('config_side_effect_rejected')
    return True


def config_snapshot():
    text=Path(CONFIG).read_text()
    validate_config(text)
    home=re.search(r'^mail_home\s*=\s*(\S+)\s*$',text,re.M).group(1)
    for path in (home,TARGET):
        if os.path.realpath(path)!=path:raise RuntimeError('config_path_symlink_rejected')
        info=os.lstat(path)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid():raise RuntimeError('config_path_owner_rejected')
    return hashlib.sha256(text.encode()).hexdigest()


def unit_state(unit):
    active=unit.endswith('.active')
    if active:unit=unit[:-7]
    action='is-enabled' if unit.endswith('.timer') and not active else 'is-active'
    result=subprocess.run(['/usr/bin/systemctl',action,unit],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15)
    return result.stdout.decode('ascii',errors='replace').strip()


def armed_state(*, run=subprocess.run):
    try:
        result=run(ARMED_COMMAND,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                   stderr=subprocess.PIPE,timeout=15)
    except (OSError,subprocess.SubprocessError):
        raise RuntimeError('armed_status_failed') from None
    if result.returncode or result.stderr or result.stdout != b'absent\n':
        raise RuntimeError('standby_armed_or_unverified')
    return 'absent'


def check_services(*, lookup=unit_state, armed_lookup=armed_state):
    expected={'fetchmail-csn.service':'inactive','fetchmail-csn.timer':'disabled',
              'csn-standby-sync.service':'inactive','csn-standby-sync.timer':'disabled','csn-standby-sync.timer.active':'inactive'}
    actual={unit:lookup(unit) for unit in expected}
    if actual != expected:raise RuntimeError('new_service_state_rejected')
    if armed_lookup() != 'absent':
        raise RuntimeError('standby_armed_rejected')
    actual['armed_check']='absent'
    return actual


def mailbox_status(*, run=subprocess.run):
    result=run(['/usr/bin/doveadm','-c',CONFIG,'-f','tab','mailbox','status','messages','*'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120)
    if result.returncode or result.stderr:raise RuntimeError('isolated_status_failed')
    try:
        rows=list(csv.DictReader(io.StringIO(result.stdout.decode()),delimiter='\t'))
        if not rows:raise ValueError()
        return {'messages':sum(int(row['messages']) for row in rows),'mailboxes':len(rows)}
    except (KeyError,ValueError,UnicodeError):raise RuntimeError('isolated_status_invalid') from None


def make_report(exit_code, elapsed, stderr):
    return {'exit_code':exit_code,'elapsed_seconds':round(elapsed,3),'stderr_bytes':len(stderr),
            'stderr_sha256':hashlib.sha256(stderr).hexdigest(),
            'complete':exit_code==0 and not stderr}


def public_result(report):
    return {key: report[key] for key in ('exit_code', 'elapsed_seconds')}


def run_backup(stderr, *, popen=subprocess.Popen, timeout=1800, cleanup_timeout=10, progress=None):
    process=popen(build_command(),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=stderr,start_new_session=True)
    start=time.monotonic()
    deadline=start+timeout
    while True:
        remaining=deadline-time.monotonic()
        try:
            return process.wait(timeout=max(.001,min(60,remaining)))
        except subprocess.TimeoutExpired:
            if time.monotonic()<deadline:
                if progress:progress({'elapsed_seconds':round(time.monotonic()-start),'stderr_bytes':os.fstat(stderr.fileno()).st_size})
                continue
            break
    try:os.killpg(process.pid,signal.SIGTERM)
    except ProcessLookupError:pass
    try:process.wait(timeout=cleanup_timeout)
    except subprocess.TimeoutExpired:pass
    # Also kill surviving descendants after the leader has exited.
    try:os.killpg(process.pid,signal.SIGKILL)
    except ProcessLookupError:pass
    process.wait()
    return 124


def _private_file(path):
    return os.fdopen(os.open(path,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'w+b')


def run_sync():
    if os.geteuid()!=pwd.getpwnam('mailadmin').pw_uid:raise RuntimeError('mailadmin_required')
    os.umask(0o077)
    validate_target()
    validate_transport_files()
    validate_no_symlinks()
    lock=acquire_lock()
    start=time.monotonic()
    stamp=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+uuid.uuid4().hex
    prefix=REPORT_DIR+'/'+stamp
    try:
        before_services=check_services()
        before_config=config_snapshot()
        before_count=mailbox_status()
        with _private_file(prefix+'.stderr') as stderr:
            rc=run_backup(stderr)
            stderr.flush();stderr.seek(0);diagnostics=stderr.read()
        report=make_report(rc,time.monotonic()-start,diagnostics)
        report['before']=before_count
        report['new_services_before']=before_services
        report['isolation_guards_verified']=True
        report['production_services_unchanged']=False
        try:
            report['new_services_after']=check_services()
            report['production_services_unchanged']=before_services==report['new_services_after']
            report['isolated_config_unchanged']=before_config==config_snapshot()
            validate_target()
            validate_transport_files()
            validate_no_symlinks()
            report['after']=mailbox_status()
            report['complete']=report['complete'] and report['production_services_unchanged'] and report['isolated_config_unchanged']
        except (RuntimeError,OSError,subprocess.SubprocessError,ValueError):
            report['complete']=False
            report['postcheck_failed']=True
        with _private_file(prefix+'.json') as output:output.write((json.dumps(report,sort_keys=True)+'\n').encode())
        print(json.dumps(public_result(report),sort_keys=True),flush=True)
        return 0 if report['complete'] else 1
    finally:os.close(lock)


def main(argv=None):
    argv=sys.argv[1:] if argv is None else argv
    if argv:return 64
    try:return run_sync()
    except (RuntimeError,OSError,subprocess.SubprocessError,ValueError):
        print(json.dumps({'elapsed_seconds':0.0,'exit_code':1},sort_keys=True),flush=True)
        return 1

if __name__=='__main__':sys.exit(main())
