#!/usr/bin/python3
"""Set CSN retention only; preserve credentials and all unrelated settings."""
import ast, datetime, os, pwd, re, resource, shutil, stat, subprocess, sys, tempfile, time
from pathlib import Path

def run(args):
    p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if p.returncode: raise RuntimeError('Command failed: '+args[0])
    return p.stdout

def parse(data):
    with tempfile.TemporaryDirectory(prefix='csn-keep-',dir='/run' if os.geteuid()==0 else None) as d:
        p=Path(d)/'rc';p.write_bytes(data);p.chmod(0o600)
        out=run(['/usr/bin/fetchmail','--configdump','--nodetach','--daemon','0','--nosyslog','--logfile','/dev/null','--idfile','/dev/null','-f',str(p)])
    constants={}
    class Sub(ast.NodeTransformer):
        def visit_Name(self,node):
            if node.id not in constants:raise RuntimeError('Unknown constant')
            return ast.parse(repr(constants[node.id]),mode='eval').body
    for node in ast.parse(out.decode()).body:
        if isinstance(node,ast.Assign):
            value=ast.literal_eval(Sub().visit(node.value))
            for target in node.targets:
                if isinstance(target,ast.Name):
                    if target.id=='fetchmailrc':return value
                    constants[target.id]=value
    raise RuntimeError('Parse failed')

def change(data):
    s=data.decode()
    s,n=re.subn(r'(?m)^(\s*)fetchall([ \t]*(?:#[^\n]*)?)$',r'\1nofetchall\2',s)
    s=re.sub(r'(?m)^(\s*)nokeep([ \t]*(?:#[^\n]*)?)$',r'\1keep\2',s)
    before=parse(data);after=parse(s.encode())
    users=after['servers'][0]['users']
    if len(after['servers'])!=1 or len(users)!=11:raise RuntimeError('Unexpected account count')
    for u in users:
        if not u.get('keep') or u.get('fetchall'):raise RuntimeError('Retention policy not effective on every account')
    for config in (before,after):
        for server in config['servers']:
            for u in server['users']:
                u.pop('keep',None);u.pop('fetchall',None)
    if before!=after:raise RuntimeError('Unexpected configuration difference')
    return s.encode()

def processes():
    p=subprocess.run(['/usr/bin/pgrep','-u','csn','-x','fetchmail'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if p.returncode not in (0,1):raise RuntimeError('Process check failed')
    return p.returncode==0

def main():
    resource.setrlimit(resource.RLIMIT_CORE,(0,0));os.umask(0o077)
    if sys.argv[1:]==['--test']:
        data=Path('/home/mailadmin/csn-migration/delivery-draft/fetchmailrc.template').read_bytes()
        changed=change(data)
        if change(changed)!=changed:raise RuntimeError('Not idempotent')
        print('PASS: 11-account policy change, native parsing, unrelated settings preserved, idempotence');return
    if os.geteuid()!=0 or sys.argv[1:] not in (['old'],['new']):raise RuntimeError('Use sudo python3 script old|new')
    old=sys.argv[1]=='old';account='csn' if old else 'fetchcsn'
    path=Path('/home/'+account+'/.fetchmailrc');meta=path.lstat()
    if not stat.S_ISREG(meta.st_mode) or meta.st_uid!=pwd.getpwnam(account).pw_uid:raise RuntimeError('Unexpected configuration owner/type')
    if not old:
        for unit in ('fetchmail-csn.service','fetchmail-csn.timer'):
            if run(['/bin/systemctl','show',unit,'-p','ActiveState']).strip()!=b'ActiveState=inactive':raise RuntimeError('New collection must be inactive')
        if os.path.lexists('/etc/csn-mail/enable-fetch'):raise RuntimeError('Activation marker exists')
    original=path.read_bytes();changed=change(original)
    backup=Path('/var/backups')/('csn-keep-'+datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700);shutil.copy2(str(path),str(backup/'fetchmailrc'))
    fd,tmp=tempfile.mkstemp(prefix='.fetchmailrc.keep-',dir=str(path.parent))
    try:
        with os.fdopen(fd,'wb') as f:
            os.fchown(f.fileno(),meta.st_uid,meta.st_gid);os.fchmod(f.fileno(),0o600);f.write(changed);f.flush();os.fsync(f.fileno())
        os.replace(tmp,str(path))
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    if path.read_bytes()!=changed:raise RuntimeError('Write verification failed')
    print('Backup: '+str(backup),flush=True)
    print('11 accounts: keep=yes, fetchall=no; other settings unchanged.',flush=True)
    if old:
        run(['/bin/systemctl','stop','fetchmail-csn.service'])
        if processes():
            subprocess.run([('/usr/sbin/runuser' if Path('/usr/sbin/runuser').is_file() else '/sbin/runuser'),'-u','csn','--','/usr/bin/env','HOME=/home/csn','/usr/bin/fetchmail','-f',str(path),'--quit'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        for i in range(30):
            if not processes():break
            time.sleep(1)
        if processes():raise RuntimeError('Existing fetchmail has not exited; service left stopped')
        run(['/bin/systemctl','start','fetchmail-csn.service'])
        time.sleep(3)
        print(run(['/bin/systemctl','show','fetchmail-csn.service','-p','ActiveState','-p','SubState','-p','MainPID']).decode())
    else:print('New collection remains inactive.')

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('Stopped; secret diagnostics suppressed. Check service state before continuing.',file=sys.stderr);sys.exit(1)
