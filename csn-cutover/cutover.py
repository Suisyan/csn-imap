#!/usr/bin/python3
"""Fixed one-time CSN Maildir cutover and rollback helper."""
import datetime
import json
import os
import pwd
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid

SOURCE='/home/mailadmin/csn-migration/initial-sync-receiver/Maildir'
LIVE='/home/csn/Maildir'
READY='/home/csn/Maildir.cutover-ready'
BACKUP_ROOT='/var/backups'
FETCH_MARKER='/etc/csn-mail/enable-fetch'
STANDBY_MARKER='/etc/csn-standby-sync/armed'
STATE='/var/lib/csn-cutover/active-backup.json'


class CutoverError(Exception):pass


def parse_args(argv):
    choices={
        ('--preflight',):'preflight',
        ('--prepare','--confirm-old-is-frozen'):'prepare',
        ('--activate','--confirm-maildir-swap'):'activate',
        ('--rollback','--confirm-restore-old-new-maildir'):'rollback'}
    try:return choices[tuple(argv)]
    except KeyError:raise CutoverError('arguments rejected') from None


def prepare_command():
    return ['/usr/bin/rsync','-a','--delete','--no-owner','--no-group',
            '--exclude=dovecot.index*','--exclude=dovecot.mailbox.log*',
            '--exclude=fetchmail.log*','--exclude=procmail.log*',
            SOURCE+'/',READY+'/']


def uid_lookup(name):
    entry=pwd.getpwnam(name);return entry.pw_uid,entry.pw_gid


def run_command(argv):
    allowed=(
        argv in (["/usr/bin/systemctl","is-active","dovecot.service"],
                 ["/usr/bin/systemctl","is-active","fetchmail-csn.service"],
                 ["/usr/bin/systemctl","is-active","fetchmail-csn.timer"],
                 ["/usr/bin/systemctl","is-enabled","fetchmail-csn.timer"],
                 ["/usr/bin/systemctl","is-active","csn-standby-sync.timer"],
                 ["/usr/bin/systemctl","is-active","csn-standby-sync.service"],
                 ["/usr/bin/systemctl","is-enabled","csn-standby-sync.timer"],
                 ["/usr/bin/systemctl","stop","dovecot.service"],
                 ["/usr/bin/systemctl","start","dovecot.service"],
                 ["/usr/bin/systemctl","disable","--now","fetchmail-csn.timer"],
                 ["/usr/bin/doveadm","user","csn"],
                 ["/usr/bin/doveadm","-f","json","mailbox","status","-u","csn","messages","*"])
        or argv==prepare_command())
    if not allowed:raise CutoverError('command rejected')
    try:
        result=subprocess.run(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,timeout=1800,env={'PATH':'/usr/bin:/bin','LC_ALL':'C'})
    except (OSError,subprocess.SubprocessError):raise CutoverError('command failed') from None
    state=argv[0:2] in (["/usr/bin/systemctl","is-active"],["/usr/bin/systemctl","is-enabled"])
    if result.stderr or (result.returncode and not (state and result.returncode in (1,3))):
        raise CutoverError('command failed')
    return result.stdout


class Cutover:
    def __init__(self,root='/',runner=run_command,accounts=None):
        self.root=os.path.abspath(root);self.runner=runner
        self.accounts=accounts or {name:uid_lookup(name) for name in ('root','csn','mailadmin')}

    def path(self,logical):
        if not logical.startswith('/') or '..' in logical.split('/'):raise CutoverError('path rejected')
        return os.path.join(self.root,logical.lstrip('/'))

    def _lstat(self,logical):
        try:return os.lstat(self.path(logical))
        except OSError:raise CutoverError('path unavailable') from None

    def _directory(self,logical,owners):
        info=self._lstat(logical)
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or (info.st_uid,info.st_gid) not in owners or info.st_mode&0o022):
            raise CutoverError('unsafe directory')
        return info

    def _absent(self,logical):
        try:os.lstat(self.path(logical))
        except FileNotFoundError:return True
        raise CutoverError('path must be absent')

    def _state(self,unit,kind='is-active'):
        return self.runner(['/usr/bin/systemctl',kind,unit]).decode('ascii').strip()

    def _guards(self):
        if self._state('dovecot.service')!='active':raise CutoverError('dovecot not active')
        for unit in ('fetchmail-csn.service','csn-standby-sync.service'):
            if self._state(unit)!='inactive':raise CutoverError('service guard failed')
        for unit in ('fetchmail-csn.timer','csn-standby-sync.timer'):
            if self._state(unit)!='inactive' or self._state(unit,'is-enabled')!='disabled':
                raise CutoverError('timer guard failed')
        self._absent(FETCH_MARKER);self._absent(STANDBY_MARKER)

    def _safe_tree(self,logical,owner=None):
        root=self.path(logical);count=0;mailboxes=0
        def failed(_error):raise CutoverError('tree unavailable') from None
        for current,dirs,files in os.walk(root,topdown=True,followlinks=False,onerror=failed):
            if {'cur','new','tmp'}.issubset(dirs):mailboxes+=1
            for name in dirs+files:
                info=os.lstat(os.path.join(current,name))
                if stat.S_ISLNK(info.st_mode):raise CutoverError('symlink rejected')
                if owner is not None and (info.st_uid,info.st_gid)!=owner:
                    raise CutoverError('tree owner rejected')
            if os.path.basename(current) in ('cur','new'):
                count+=sum(1 for name in files if not name.startswith('dovecot.'))
        return count,mailboxes

    def preflight(self):
        root_owner=self.accounts['root'];csn=self.accounts['csn'];mailadmin=self.accounts['mailadmin']
        self._directory(SOURCE,(mailadmin,));self._directory(LIVE,(csn,));self._guards()
        self._safe_tree(SOURCE,mailadmin)
        try:self._directory(READY,(csn,));ready='present'
        except CutoverError:
            if os.path.lexists(self.path(READY)):raise
            ready='absent'
        return {'ready':ready,'fetch_timer':'disabled','standby_timer':'disabled','markers':'absent'}

    def _chown_ready(self):
        owner=self.accounts['csn'];root=self.path(READY)
        for current,dirs,files in os.walk(root,topdown=True,followlinks=False):
            info=os.lstat(current)
            if stat.S_ISLNK(info.st_mode):raise CutoverError('symlink rejected')
            os.chown(current,*owner);os.chmod(current,0o700)
            for name in files:
                path=os.path.join(current,name);info=os.lstat(path)
                if not stat.S_ISREG(info.st_mode):raise CutoverError('nonregular file rejected')
                os.chown(path,*owner);os.chmod(path,0o600)

    def prepare(self):
        report=self.preflight()
        if report['ready']!='absent':raise CutoverError('ready already exists')
        os.mkdir(self.path(READY),0o700);os.chown(self.path(READY),*self.accounts['csn'])
        try:
            self.runner(prepare_command());self._chown_ready()
            source_count=self._safe_tree(SOURCE,self.accounts['mailadmin'])
            ready_count=self._safe_tree(READY,self.accounts['csn'])
            if source_count!=ready_count:raise CutoverError('mail tree count mismatch')
            return {'messages':ready_count[0],'mailboxes':ready_count[1],'ready':READY}
        except BaseException:
            shutil.rmtree(self.path(READY),ignore_errors=True);raise

    def _write_state(self,backup):
        parent=os.path.dirname(self.path(STATE))
        fd,temp=tempfile.mkstemp(prefix='.state-',dir=parent)
        try:
            with os.fdopen(fd,'w') as stream:
                json.dump({'backup':backup},stream,sort_keys=True);stream.write('\n');stream.flush();os.fsync(stream.fileno())
                os.fchown(stream.fileno(),*self.accounts['root']);os.fchmod(stream.fileno(),0o600)
            os.replace(temp,self.path(STATE))
        finally:
            try:os.unlink(temp)
            except FileNotFoundError:pass

    def activate(self):
        self._guards();self._absent(STATE)
        self._directory(BACKUP_ROOT,(self.accounts['root'],))
        self._directory(os.path.dirname(STATE),(self.accounts['root'],))
        self._directory(READY,(self.accounts['csn'],))
        if self._safe_tree(SOURCE,self.accounts['mailadmin'])!=self._safe_tree(READY,self.accounts['csn']):
            raise CutoverError('ready not verified')
        stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        backup='/var/backups/csn-cutover-'+stamp+'-'+uuid.uuid4().hex[:8]
        os.mkdir(self.path(backup),0o700);os.chown(self.path(backup),*self.accounts['root'])
        prior=backup+'/Maildir.before';failed=backup+'/Maildir.failed-cutover'
        stopped=False;moved_prior=False;moved_ready=False
        try:
            self.runner(['/usr/bin/systemctl','stop','dovecot.service']);stopped=True
            os.rename(self.path(LIVE),self.path(prior));moved_prior=True
            os.rename(self.path(READY),self.path(LIVE));moved_ready=True
            self.runner(['/usr/bin/systemctl','start','dovecot.service']);stopped=False
            self.runner(['/usr/bin/doveadm','user','csn'])
            self.runner(['/usr/bin/doveadm','-f','json','mailbox','status','-u','csn','messages','*'])
            self._write_state(backup)
            return {'backup':backup,'maildir':LIVE}
        except BaseException:
            try:
                if not stopped:self.runner(['/usr/bin/systemctl','stop','dovecot.service'])
                if moved_ready and os.path.exists(self.path(LIVE)):os.rename(self.path(LIVE),self.path(failed))
                if moved_prior and os.path.exists(self.path(prior)):os.rename(self.path(prior),self.path(LIVE))
                self.runner(['/usr/bin/systemctl','start','dovecot.service'])
            except BaseException:pass
            raise

    def _read_state(self):
        path=self.path(STATE);info=os.lstat(path)
        if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or (info.st_uid,info.st_gid)!=self.accounts['root'] or stat.S_IMODE(info.st_mode)!=0o600):
            raise CutoverError('state rejected')
        with open(path) as stream:data=json.load(stream)
        backup=data.get('backup') if isinstance(data,dict) else None
        if not isinstance(backup,str) or not backup.startswith('/var/backups/csn-cutover-') or '..' in backup:
            raise CutoverError('state rejected')
        return backup

    def rollback(self):
        backup=self._read_state();prior=backup+'/Maildir.before';failed=backup+'/Maildir.failed-cutover'
        self.runner(['/usr/bin/systemctl','disable','--now','fetchmail-csn.timer'])
        try:
            info=os.lstat(self.path(FETCH_MARKER))
            if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
                    or (info.st_uid,info.st_gid)!=self.accounts['root'] or info.st_mode&0o022):
                raise CutoverError('fetch marker rejected')
            os.unlink(self.path(FETCH_MARKER))
        except FileNotFoundError:pass
        self.runner(['/usr/bin/systemctl','stop','dovecot.service'])
        os.rename(self.path(LIVE),self.path(failed));os.rename(self.path(prior),self.path(LIVE))
        self.runner(['/usr/bin/systemctl','start','dovecot.service']);os.unlink(self.path(STATE))
        return {'restored':LIVE,'failed_cutover':failed}


def main(argv=None,geteuid=os.geteuid):
    argv=sys.argv[1:] if argv is None else argv
    if geteuid()!=0:return 1
    try:
        action=parse_args(argv);worker=Cutover();report=getattr(worker,action)()
        print(json.dumps(report,sort_keys=True));return 0
    except (CutoverError,OSError,ValueError,KeyError):
        print(json.dumps({'success':False}));return 1


if __name__=='__main__':sys.exit(main())
