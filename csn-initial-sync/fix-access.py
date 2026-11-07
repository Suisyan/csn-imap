#!/usr/bin/python3
"""Repair only the newly-created helper directory, after verifying its contents."""
import hashlib,os,stat,subprocess
from pathlib import Path
EXPECTED='afcd0d8e3f252989f95efeeb5385e947007c70d7a942aed47447b4e279222a0d'
if os.geteuid()!=0:raise SystemExit('Run with sudo python3')
folder=Path('/usr/local/libexec');entry=folder/'csn-initial-connector.py'
s=folder.lstat()
if not stat.S_ISDIR(s.st_mode) or s.st_uid!=0 or s.st_gid!=0 or stat.S_IMODE(s.st_mode) not in (0o700,0o755):raise SystemExit('Unexpected helper directory; stopped')
if sorted(p.name for p in folder.iterdir())!=['csn-initial-connector.py']:raise SystemExit('Other files exist; no permissions changed')
s=entry.lstat()
if not stat.S_ISREG(s.st_mode) or s.st_uid!=0 or stat.S_IMODE(s.st_mode)!=0o755 or hashlib.sha256(entry.read_bytes()).hexdigest()!=EXPECTED:raise SystemExit('Helper verification failed; stopped')
os.chmod(str(folder),0o755)
subprocess.run(['/sbin/runuser','-u','csn','--','/usr/bin/python3','-c','from pathlib import Path; p=Path("/usr/local/libexec/csn-initial-connector.py"); compile(p.read_bytes(),str(p),"exec")'],check=True)
print('Helper directory access repaired and verified as csn.')
print('No backup run; no mail modified; no service restarted.')
