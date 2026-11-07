#!/usr/bin/python3
"""Fixed local orchestrator for the corrected isolated Task 5 proof."""
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid


WORKSPACE='/home/suisyan/Documents/conoha-imap'
OLD_HOST='umeoka@118.27.32.100'
NEW_HOST='mailadmin@163.44.111.155'
MAX_ATTEMPTS=3
OUTPUT_LIMIT=1048576
REPORT_DIR=WORKSPACE+'/csn-standby-sync/task5-reports'
DIGEST_RE=re.compile(r'^[0-9a-f]{64}$')
OLD_KEY=WORKSPACE+'/.migration-ssh/csn-v2-migration'
OLD_HOSTS=WORKSPACE+'/.migration-ssh/csn-v2-known-hosts'
NEW_KEY=WORKSPACE+'/mail-vps3-key.pem'
NEW_HOSTS='/tmp/conoha-imap-hostkey'


class VerifyError(Exception):
    """A sanitized verification failure."""


def _ssh(host,key,known_hosts,remote):
    return ['/usr/bin/ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes',
            '-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10',
            '-o','UserKnownHostsFile='+known_hosts,'-i',key,host,remote]


def fixed_commands():
    return {
        'old_state':_ssh(OLD_HOST,OLD_KEY,OLD_HOSTS,
            '/bin/systemctl is-active fetchmail-csn.service; '
            '/bin/systemctl is-active dovecot.service'),
        'new_state':_ssh(NEW_HOST,NEW_KEY,NEW_HOSTS,
            '/usr/bin/systemctl is-active fetchmail-csn.service || true; '
            '/usr/bin/systemctl is-enabled fetchmail-csn.timer || true; '
            '/usr/bin/systemctl is-active csn-standby-sync.service || true; '
            '/usr/bin/systemctl is-enabled csn-standby-sync.timer || true; '
            '/usr/bin/systemctl is-active csn-standby-sync.timer || true'),
        'sync':_ssh(NEW_HOST,NEW_KEY,NEW_HOSTS,
            '/usr/bin/python3 -B /home/mailadmin/csn-migration/standby-e2e/isolated_e2e.py'),
        'old_audit':_ssh(OLD_HOST,OLD_KEY,OLD_HOSTS,
            'sudo -n /usr/local/sbin/csn-standby-guid-audit'),
        'new_audit':_ssh(NEW_HOST,NEW_KEY,NEW_HOSTS,
            '/usr/bin/python3 -B /home/mailadmin/csn-migration/standby-e2e/isolated_guid_audit.py'),
    }


def _allowed(command):
    return any(command == value for value in fixed_commands().values())


def validate_local_transport(*,uid=None,lstat=os.lstat):
    uid=os.geteuid() if uid is None else uid
    for path,is_private in ((OLD_KEY,True),(NEW_KEY,True),
                            (OLD_HOSTS,False),(NEW_HOSTS,False)):
        try:info=lstat(path)
        except OSError:raise VerifyError('transport file unavailable') from None
        mode=stat.S_IMODE(info.st_mode)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=uid
                or getattr(info,'st_nlink',1)!=1
                or (is_private and mode!=0o600)
                or (not is_private and mode & 0o022)):
            raise VerifyError('transport file unsafe')
    return True


def _terminate(process,cleanup_timeout):
    try:os.killpg(process.pid,signal.SIGTERM)
    except OSError:pass
    try:process.wait(timeout=cleanup_timeout)
    except subprocess.TimeoutExpired:pass
    try:os.killpg(process.pid,signal.SIGKILL)
    except OSError:pass
    try:process.wait()
    except OSError:pass


def run_fixed(command,*,popen=subprocess.Popen,timeout=900,cleanup_timeout=10):
    if not _allowed(command):raise VerifyError('command rejected')
    with tempfile.TemporaryFile(mode='w+b') as output:
        with tempfile.TemporaryFile(mode='w+b') as errors:
            try:
                process=popen(command,stdin=subprocess.DEVNULL,stdout=output,
                              stderr=errors,start_new_session=True,
                              env={'PATH':'/usr/bin:/bin','LC_ALL':'C'})
                try:code=process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    _terminate(process,cleanup_timeout)
                    raise VerifyError('command timed out')
                output.seek(0,os.SEEK_END);size=output.tell()
                errors.seek(0,os.SEEK_END);error_size=errors.tell()
                if code or error_size or size>OUTPUT_LIMIT:
                    raise VerifyError('fixed command failed')
                output.seek(0)
                return output.read()
            except VerifyError:raise
            except (OSError,subprocess.SubprocessError,ValueError):
                raise VerifyError('fixed command failed') from None


def _unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise VerifyError('duplicate audit key')
        result[key]=value
    return result


def parse_audit(data):
    if not isinstance(data,bytes) or not data.endswith(b'\n') or data.count(b'\n')!=1:
        raise VerifyError('invalid audit report')
    try:
        value=json.loads(data[:-1].decode('ascii'),object_pairs_hook=_unique_object)
    except VerifyError:raise
    except (UnicodeError,ValueError,TypeError):
        raise VerifyError('invalid audit report') from None
    expected={'messages','mailboxes','duplicate_groups','duplicate_excess','distribution_sha256'}
    if not isinstance(value,dict) or set(value)!=expected:
        raise VerifyError('invalid audit schema')
    for key in ('messages','mailboxes','duplicate_groups','duplicate_excess'):
        item=value[key]
        if type(item) is not int or item<0 or item>1000000000:
            raise VerifyError('invalid audit count')
    digest=value['distribution_sha256']
    if not isinstance(digest,str) or not DIGEST_RE.match(digest):
        raise VerifyError('invalid audit digest')
    if value['mailboxes']>value['messages'] or value['duplicate_excess']>value['messages']:
        raise VerifyError('inconsistent audit count')
    return value


def compare(old,new):
    count_keys=('messages','mailboxes','duplicate_groups','duplicate_excess')
    equal=(all(old[key]==new[key] for key in count_keys)
           and old['distribution_sha256']==new['distribution_sha256'])
    return {
        'distribution_equal':equal,
        'source':{key:old[key] for key in count_keys},
        'destination':{key:new[key] for key in count_keys},
    }


def run_attempts(*,guard,sync,old_audit,new_audit,record):
    for attempt in range(1,MAX_ATTEMPTS+1):
        try:
            guard()
            sync()
            result=compare(old_audit(),new_audit())
            guard()
        except VerifyError:raise
        except (OSError,subprocess.SubprocessError,ValueError,KeyError):
            raise VerifyError('attempt failed') from None
        result['attempt']=attempt
        record(result)
        if result['distribution_equal']:
            return {'attempts':attempt,'success':True}
    return {'attempts':MAX_ATTEMPTS,'success':False}


def _parse_sync(data):
    if not isinstance(data,bytes) or not data.endswith(b'\n') or data.count(b'\n')!=1:
        raise VerifyError('invalid sync result')
    def reject_constant(_value):
        raise VerifyError('invalid sync result')
    try:value=json.loads(data[:-1].decode('ascii'),parse_constant=reject_constant)
    except (UnicodeError,ValueError,TypeError):raise VerifyError('invalid sync result') from None
    if (not isinstance(value,dict) or set(value)!= {'elapsed_seconds','exit_code'}
            or type(value['exit_code']) is not int or value['exit_code']!=0
            or type(value['elapsed_seconds']) not in (int,float)
            or value['elapsed_seconds']<0):
        raise VerifyError('invalid sync result')


def _guard(commands):
    if run_fixed(commands['old_state']) != b'active\nactive\n':
        raise VerifyError('old service guard failed')
    expected=b'inactive\ndisabled\ninactive\ndisabled\ninactive\n'
    if run_fixed(commands['new_state']) != expected:
        raise VerifyError('new service guard failed')


def _write_report(records,result,elapsed):
    path=Path(REPORT_DIR)
    path.mkdir(mode=0o700,parents=True,exist_ok=True)
    if path.is_symlink() or stat_mode(path)!=0o700:
        raise VerifyError('report directory unsafe')
    payload={'attempts':records,'result':result,
             'elapsed_seconds':round(elapsed,3)}
    name=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+uuid.uuid4().hex+'.json'
    target=str(path/name)
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write((json.dumps(payload,sort_keys=True,separators=(',',':'))+'\n').encode())
        stream.flush();os.fsync(stream.fileno())
    return target


def stat_mode(path):
    return os.stat(str(path),follow_symlinks=False).st_mode & 0o7777


def execute():
    validate_local_transport()
    commands=fixed_commands();records=[];start=time.monotonic()
    result=run_attempts(
        guard=lambda:_guard(commands),
        sync=lambda:_parse_sync(run_fixed(commands['sync'])),
        old_audit=lambda:parse_audit(run_fixed(commands['old_audit'])),
        new_audit=lambda:parse_audit(run_fixed(commands['new_audit'])),
        record=records.append)
    elapsed=time.monotonic()-start
    _write_report(records,result,elapsed)
    public={'attempts':result['attempts'],'elapsed_seconds':round(elapsed,3),
            'success':result['success']}
    sys.stdout.write(json.dumps(public,sort_keys=True,separators=(',',':'))+'\n')
    return 0 if result['success'] else 2


def main(argv=None):
    argv=sys.argv[1:] if argv is None else argv
    if argv:return 64
    try:return execute()
    except VerifyError:
        sys.stderr.write('task5_verify: verification failed\n')
        return 1


if __name__=='__main__':sys.exit(main())
