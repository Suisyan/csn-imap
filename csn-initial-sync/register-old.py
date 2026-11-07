#!/usr/bin/python3
"""Install the fixed source-only backup helper; do not run it or restart services."""
import hashlib,os,pwd,stat,subprocess,tempfile
from pathlib import Path
BASE=Path('/home/umeoka/csn-initial-sync-setup')
FILES=[('source.py', '/usr/local/sbin/csn-initial-backup-source', 448, '705defed8de405209692720a642cb4695b1f78c77d4bf559541e315d043b90f3'), ('connector.py', '/usr/local/libexec/csn-initial-connector.py', 493, 'afcd0d8e3f252989f95efeeb5385e947007c70d7a942aed47447b4e279222a0d'), ('source.conf', '/etc/csn-initial-source.conf', 420, '170e807d423ebdabdd676866f38826e8b2b8d4bdbae0edc3a5b773eb4cdaec02')]
RULE=Path('/etc/sudoers.d/csn-initial-backup-source')
RULE_DATA=b'umeoka ALL=(root) NOPASSWD: /usr/local/sbin/csn-initial-backup-source ""\n'
def run(args):
 p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 if p.returncode:raise RuntimeError('Validation command failed')
 return p.stdout

def main():
 if os.geteuid()!=0:raise RuntimeError('Run with sudo python3')
 os.umask(0o077)
 owner=pwd.getpwnam('csn')
 if (owner.pw_uid,owner.pw_gid,owner.pw_dir)!=(1001,1001,'/home/csn'):raise RuntimeError('Unexpected CSN account')
 path=Path('/home/csn/Maildir');s=path.lstat()
 if not stat.S_ISDIR(s.st_mode) or s.st_uid!=owner.pw_uid:raise RuntimeError('Unexpected live Maildir')
 if run(['/usr/bin/doveconf','-h','mail_location']).strip()!=b'maildir:~/Maildir':raise RuntimeError('Unexpected mail location')
 if run(['/usr/bin/doveconf','-h','namespace/inbox/prefix']).strip():raise RuntimeError('Unexpected namespace prefix')
 if not os.access('/sbin/runuser',os.X_OK):raise RuntimeError('runuser missing')
 for target in [row[1] for row in FILES]+[str(RULE)]:
  if os.path.lexists(target):raise RuntimeError('Already installed; do not overwrite')
 payload=[]
 for name,target,mode,digest in FILES:
  p=BASE/name
  if not stat.S_ISREG(p.lstat().st_mode):raise RuntimeError('Unexpected staged input')
  data=p.read_bytes()
  if hashlib.sha256(data).hexdigest()!=digest:raise RuntimeError('Staged file hash mismatch')
  if name.endswith('.py'):compile(data,str(p),'exec')
  payload.append((Path(target),mode,data))
 run(['/usr/sbin/visudo','-c'])
 libexec=Path('/usr/local/libexec')
 if not libexec.exists():
  libexec.mkdir(mode=0o755);libexec.chmod(0o755)
 created=[]
 try:
  for target,mode,data in payload+[(RULE,0o440,RULE_DATA)]:
   fd=os.open(str(target),os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,mode)
   created.append(target)
   with os.fdopen(fd,'wb') as f:
    os.fchown(f.fileno(),0,0);os.fchmod(f.fileno(),mode);f.write(data);f.flush();os.fsync(f.fileno())
  run(['/usr/sbin/visudo','-c'])
  run(['/usr/bin/doveconf','-c','/etc/csn-initial-source.conf','-h','mail_location'])
 except Exception:
  for target in reversed(created):target.unlink()
  raise
 print('Fixed CSN one-way backup helper registered.')
 print('No backup run; no mail modified; no service stopped or restarted.')
if __name__=='__main__':main()
