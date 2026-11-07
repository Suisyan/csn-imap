#!/usr/bin/python3
import datetime,hashlib,os,shutil,stat,tempfile
from pathlib import Path
if os.geteuid()!=0:raise SystemExit('Run with sudo python3')
target=Path('/usr/local/sbin/csn-initial-backup-source')
source=Path('/home/umeoka/csn-initial-sync-setup/source-diagnostic.py')
if target.is_symlink() or source.is_symlink():raise SystemExit('Unexpected symlink')
if hashlib.sha256(target.read_bytes()).hexdigest()!='705defed8de405209692720a642cb4695b1f78c77d4bf559541e315d043b90f3':raise SystemExit('Installed helper differs; stopped')
data=source.read_bytes()
if hashlib.sha256(data).hexdigest()!='4b30ee5ff84091d5240fa36978cbb5ae436abd88184291d50ffa926ebfb423a7':raise SystemExit('Staged helper differs; stopped')
compile(data,str(source),'exec')
os.umask(0o077)
backup=Path('/var/backups')/('csn-source-helper-before-'+datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ'))
backup.mkdir(mode=0o700);shutil.copy2(str(target),str(backup/target.name))
fd,tmp=tempfile.mkstemp(prefix='.csn-initial-source-',dir=str(target.parent))
try:
 with os.fdopen(fd,'wb') as f:
  os.fchown(f.fileno(),0,0);os.fchmod(f.fileno(),0o700);f.write(data);f.flush();os.fsync(f.fileno())
 os.replace(tmp,str(target))
finally:
 if os.path.exists(tmp):os.unlink(tmp)
print('Source diagnostics enabled; backup: '+str(backup))
print('No copy run; no mail changed; no service restarted.')
