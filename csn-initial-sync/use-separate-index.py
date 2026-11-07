#!/usr/bin/python3
"""Change only the dedicated backup config to use a separate index directory."""
import datetime,hashlib,os,pwd,shutil,stat,subprocess,tempfile
from pathlib import Path
OLD_HASH='170e807d423ebdabdd676866f38826e8b2b8d4bdbae0edc3a5b773eb4cdaec02'
CONFIG=Path('/etc/csn-initial-source.conf')
INDEX=Path('/home/csn/.csn-initial-backup-index')

def main():
 if os.geteuid()!=0:raise RuntimeError('Run with sudo python3')
 os.umask(0o077)
 owner=pwd.getpwnam('csn')
 if (owner.pw_uid,owner.pw_gid,owner.pw_dir)!=(1001,1001,'/home/csn'):raise RuntimeError('Unexpected account')
 meta=CONFIG.lstat()
 if not stat.S_ISREG(meta.st_mode) or meta.st_uid!=0 or meta.st_mode&0o022:raise RuntimeError('Unexpected config ownership or type')
 original=CONFIG.read_bytes()
 if hashlib.sha256(original).hexdigest()!=OLD_HASH:raise RuntimeError('Installed backup config differs; stopped')
 if os.path.lexists(str(INDEX)):raise RuntimeError('Separate index path already exists; stopped')
 old=b'mail_location = maildir:/home/csn/Maildir\n'
 new=b'mail_location = maildir:/home/csn/Maildir:INDEX=/home/csn/.csn-initial-backup-index\n'
 if original.count(old)!=1:raise RuntimeError('Unexpected mail location')
 updated=original.replace(old,new)
 fd,tmp=tempfile.mkstemp(prefix='.csn-initial-source-',dir='/etc')
 backup=None;created=False;installed=False
 try:
  with os.fdopen(fd,'wb') as f:
   os.fchmod(f.fileno(),0o644);f.write(updated);f.flush();os.fsync(f.fileno())
  p=subprocess.run(['/usr/bin/doveconf','-c',tmp,'-h','mail_location'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)
  if p.stdout.strip()!=new.split(b' = ',1)[1].strip():raise RuntimeError('Config validation failed')
  backup=Path('/var/backups')/('csn-source-index-before-'+datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ'))
  backup.mkdir(mode=0o700);shutil.copy2(str(CONFIG),str(backup/CONFIG.name))
  INDEX.mkdir(mode=0o700);created=True;os.chown(str(INDEX),owner.pw_uid,owner.pw_gid)
  subprocess.run(['/sbin/runuser','-u','csn','--','/usr/bin/test','-w',str(INDEX)],check=True)
  os.replace(tmp,str(CONFIG));installed=True
  if CONFIG.read_bytes()!=updated:raise RuntimeError('Installed config verification failed')
 except Exception:
  if installed and backup:shutil.copy2(str(backup/CONFIG.name),str(CONFIG))
  if created and not list(INDEX.iterdir()):INDEX.rmdir()
  raise
 finally:
  if os.path.exists(tmp):os.unlink(tmp)
 print('Dedicated backup index configured: '+str(INDEX))
 print('Previous config backup: '+str(backup))
 print('No copy run; production Dovecot config/indexes unchanged; no service restarted.')
if __name__=='__main__':main()
