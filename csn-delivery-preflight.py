#!/usr/bin/python3
"""Read-only CSN delivery inventory; no message bodies or credentials printed."""
import json,os,pwd,stat,subprocess,sys
from pathlib import Path

def command(args):
 p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
 return p.returncode,p.stdout

def main():
 if os.geteuid()!=0:raise RuntimeError('Run with sudo python3')
 report={'read_only':True}
 u=pwd.getpwnam('csn');report['csn']={'uid':u.pw_uid,'gid':u.pw_gid,'home':u.pw_dir}
 rc,out=command(['/usr/bin/doveadm','user','csn'])
 report['userdb_exit']=rc
 report['userdb']=[line for line in out.splitlines() if line.split() and line.split()[0] in ('uid','gid','home','mail')]
 report['paths']=[]
 for name in ('Maildir','Maildir.migration','sieve','.dovecot.sieve'):
  p=Path(u.pw_dir)/name;item={'path':str(p)}
  try:
   s=p.lstat();item.update(uid=s.st_uid,gid=s.st_gid,mode=oct(stat.S_IMODE(s.st_mode)),symlink=p.is_symlink())
   if p.is_symlink():item['target']=os.readlink(str(p))
   elif p.is_dir() and name.startswith('Maildir'):
    counts={'cur':0,'new':0,'tmp':0};folders=0
    for base,dirs,files in os.walk(str(p),followlinks=False):
     part=Path(base).name
     if part in counts:counts[part]+=len(files)
     if 'cur' in dirs and 'new' in dirs:folders+=1
    item['file_counts']=counts;item['mailbox_count']=folders
   elif p.is_dir() and name=='sieve':item['entries']=sorted(x.name for x in p.iterdir())
  except FileNotFoundError:item['exists']=False
  report['paths'].append(item)
 report['settings']={}
 for key in ('mail_location','namespace/inbox/prefix','namespace/inbox/separator','plugin/sieve','plugin/sieve_before','plugin/sieve_after','protocols'):
  rc,out=command(['/usr/bin/doveconf','-h',key]);report['settings'][key]=out.strip() if rc==0 else 'unavailable'
 rc,out=command(['/usr/bin/doveconf','-n'])
 # Report only known configuration lines relevant to sieve/LMTP, never full config.
 report['plugin_lines']=[line.strip() for line in out.splitlines() if line.strip().startswith(('mail_plugins =','protocol lmtp {','sieve =','sieve_before =','sieve_after =','imapsieve_mailbox'))]
 report['fetch_state']={}
 for unit in ('fetchmail-csn.service','fetchmail-csn.timer'):
  rc,out=command(['/usr/bin/systemctl','show',unit,'-p','ActiveState','-p','UnitFileState']);report['fetch_state'][unit]=out.splitlines()
 report['activation_marker_exists']=os.path.lexists('/etc/csn-mail/enable-fetch')
 print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
