#!/usr/bin/python3
"""Run the disabled CSN oneshot once, with retention checks and no timer enable."""
import datetime,hashlib,json,os,pwd,resource,shutil,stat,subprocess,sys,time,types
from pathlib import Path
MARKER=Path('/etc/csn-mail/enable-fetch')
CONFIG=Path('/home/fetchcsn/.fetchmailrc')
MAILDIR=Path('/home/csn/Maildir')
SERVICE_HASH='996ec1924d10004960b340ccd06b44f97123e65a3f451df7f95fa06b4cf0acd9'


def run(args):
 p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 if p.returncode:raise RuntimeError('Required command failed')
 return p.stdout

def state(unit):
 out=run(['/usr/bin/systemctl','show',unit,'-p','ActiveState','-p','UnitFileState','-p','Result','-p','ExecMainStatus']).decode()
 return dict(line.split('=',1) for line in out.splitlines() if '=' in line)

def queued():
 count=0
 for line in run(['/usr/sbin/postqueue','-j']).decode().splitlines():
  item=json.loads(line)
  count+=sum(r.get('address','').lower() in ('csn','csn@imap.umeoka.co.jp') for r in item.get('recipients',[]))
 return count

def snapshot():
 counts={};hashes={}
 for base,dirs,files in os.walk(str(MAILDIR),followlinks=False):
  path=Path(base)
  if path.name not in ('cur','new'):continue
  folder=str(path.parent.relative_to(MAILDIR))
  for name in files:
   p=path/name
   if not stat.S_ISREG(p.lstat().st_mode):raise RuntimeError('Unexpected mailbox entry')
   counts[folder]=counts.get(folder,0)+1
   hashes[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
 return counts,hashes

def main():
 if os.geteuid()!=0:raise RuntimeError('Root required')
 resource.setrlimit(resource.RLIMIT_CORE,(0,0));os.umask(0o077)
 service=state('fetchmail-csn.service');timer=state('fetchmail-csn.timer')
 if service['ActiveState']!='inactive' or timer['ActiveState']!='inactive' or timer['UnitFileState']!='disabled' or os.path.lexists(str(MARKER)):raise RuntimeError('Collection is not disabled')
 unit=Path('/etc/systemd/system/fetchmail-csn.service')
 if hashlib.sha256(unit.read_bytes()).hexdigest()!=SERVICE_HASH:raise RuntimeError('Service changed')
 if list(Path('/etc/systemd/system/fetchmail-csn.service.d').glob('*.conf')) or list(Path('/run/systemd/system/fetchmail-csn.service.d').glob('*.conf')):raise RuntimeError('Service override present')
 owner=pwd.getpwnam('fetchcsn');s=CONFIG.lstat()
 if not stat.S_ISREG(s.st_mode) or s.st_uid!=owner.pw_uid or stat.S_IMODE(s.st_mode)!=0o600:raise RuntimeError('Credential permissions changed')
 module=types.ModuleType('credential_parser')
 helper=Path('/usr/local/sbin/csn-import-credentials')
 if helper.stat().st_uid!=0 or stat.S_IMODE(helper.stat().st_mode)&0o022:raise RuntimeError('Untrusted parser')
 exec(compile(helper.read_bytes(),str(helper),'exec'),module.__dict__)
 config=module.parse_config(CONFIG.read_bytes());passwords=module.extract_passwords(config)
 if len(passwords)!=11:raise RuntimeError('Unexpected account count')
 for server in config['servers']:
  if not server.get('uidl'):raise RuntimeError('UIDL must be enabled')
  for u in server['users']:
   if not u.get('keep') or u.get('fetchall') or u.get('smtpname')!='csn@imap.umeoka.co.jp' or u.get('smtphunt')!=['127.0.0.1/25'] or u.get('mda'):raise RuntimeError('Retention or delivery policy mismatch')
 if not (Path('/home/csn/.dovecot.sieve').is_symlink()) or not (Path('/home/csn/.dovecot.sieve').resolve()==Path('/home/csn/sieve/csn.sieve')):raise RuntimeError('CSN Sieve is not active')
 if queued():raise RuntimeError('Existing CSN queue must be reviewed')
 before,original=snapshot()
 if len(original)!=4:raise RuntimeError('Expected original message plus three probes; review current mailbox')
 stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
 backup=Path('/var/backups')/('csn-fetch-once-'+stamp);backup.mkdir(mode=0o700)
 shutil.copy2(str(CONFIG),str(backup/'fetchmailrc'))
 ids=Path('/home/fetchcsn/.fetchids')
 if ids.exists():shutil.copy2(str(ids),str(backup/'fetchids'))
 report={'backup':str(backup),'account_count':11,'keep':True,'fetchall':False,'uidl':True,'before_messages':sum(before.values()),'before_folders':before,'timer_enabled':False,'maildir_switched':False}
 (backup/'report.json').write_text(json.dumps(report,indent=2)+'\n')
 print('Retention checks passed. Running one collection cycle; may take up to 15 minutes.',flush=True)
 print('Report directory: '+str(backup),flush=True)
 with MARKER.open('xb') as f:f.write(b'one-shot migration validation\n')
 try:
  result=subprocess.run(['/usr/bin/systemctl','start','fetchmail-csn.service'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  report['systemctl_exit']=result.returncode
 finally:
  MARKER.unlink()
 for attempt in range(30):
  if queued()==0:break
  time.sleep(1)
 after,current=snapshot()
 report.update(service=state('fetchmail-csn.service'),timer=state('fetchmail-csn.timer'),after_messages=sum(after.values()),after_folders=after,new_message_delta=sum(after.values())-sum(before.values()),csn_queued_recipients=queued(),activation_marker_exists=MARKER.exists(),original_messages_unchanged=all(current.get(p)==h for p,h in original.items()))
 report['collection_process_ok']=report['systemctl_exit']==0 and report['service'].get('Result')=='success' and report['service'].get('ExecMainStatus') in ('0','1') and report['service'].get('ActiveState')=='inactive'
 (backup/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps(report,ensure_ascii=False,indent=2))
 if not report['collection_process_ok'] or not report['original_messages_unchanged'] or report['csn_queued_recipients']:raise RuntimeError('Review required; do not repeat collection')

if __name__=='__main__':
 try:main()
 except Exception:
  print('Stopped; secret diagnostics suppressed. Share output before retrying. Collection/timer state requires verification.',file=sys.stderr);sys.exit(1)
