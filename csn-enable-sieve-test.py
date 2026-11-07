#!/usr/bin/python3
"""Enable only CSN Sieve and submit three local synthetic delivery probes."""
import datetime,hashlib,json,os,pwd,shutil,smtplib,stat,subprocess,sys,tempfile,time,uuid
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path
HOME=Path('/home/csn')
SOURCE=Path('/home/mailadmin/csn-migration/delivery-draft/csn.sieve')
HASH='29edccdffb2738edce8de3e047d5d84e16f42f7c5fb3b19a262dde9a0d5a4f03'

def run(args):
 p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 if p.returncode:raise RuntimeError('Command failed: '+args[0])
 return p.stdout

def inactive():
 for unit in ('fetchmail-csn.service','fetchmail-csn.timer'):
  if run(['/usr/bin/systemctl','show',unit,'-p','ActiveState']).strip()!=b'ActiveState=inactive':raise RuntimeError('Fetch service must stay inactive')
 if os.path.lexists('/etc/csn-mail/enable-fetch'):raise RuntimeError('Activation marker exists')

def mails():
 return [p for p in (HOME/'Maildir').rglob('*') if p.is_file() and p.parent.name in ('cur','new')]

def main():
 if os.geteuid()!=0:raise RuntimeError('Run with sudo python3')
 os.umask(0o077);inactive()
 owner=pwd.getpwnam('csn')
 if owner.pw_uid!=1001 or (HOME/'Maildir').is_symlink():raise RuntimeError('Unexpected account or mailbox')
 for name in ('sieve','.dovecot.sieve'):
  if os.path.lexists(str(HOME/name)):raise RuntimeError('Existing Sieve; stop for review')
 source=SOURCE.read_bytes()
 if hashlib.sha256(source).hexdigest()!=HASH:raise RuntimeError('Draft hash mismatch')
 previous={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in mails()}
 if len(previous)!=1:raise RuntimeError('Live mailbox changed; stop for review')
 with tempfile.TemporaryDirectory(prefix='csn-sieve-compile-',dir='/run') as d:
  path=Path(d)/'csn.sieve';path.write_bytes(source)
  run(['/usr/bin/sievec',str(path),d+'/csn.svbin'])
  binary=(Path(d)/'csn.svbin').read_bytes()
 stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
 backup=Path('/var/backups')/('csn-sieve-before-'+stamp);backup.mkdir(mode=0o700)
 shutil.copytree(str(HOME/'Maildir'),str(backup/'Maildir'),copy_function=shutil.copy2)
 (backup/'state.json').write_text(json.dumps({'previous_sieve_absent':True,'previous_mail_sha256':previous},indent=2)+'\n')
 sieve=HOME/'sieve';sieve.mkdir(mode=0o700);os.chown(str(sieve),owner.pw_uid,owner.pw_gid)
 for name,data in (('csn.sieve',source),('csn.svbin',binary)):
  p=sieve/name
  with p.open('xb') as f:f.write(data)
  os.chown(str(p),owner.pw_uid,owner.pw_gid);p.chmod(0o600)
 os.symlink('sieve/csn.sieve',str(HOME/'.dovecot.sieve'))
 os.lchown(str(HOME/'.dovecot.sieve'),owner.pw_uid,owner.pw_gid)
 print('CSN Sieve enabled. Backup: '+str(backup),flush=True)
 token=uuid.uuid4().hex
 specs=[('ordinary','CSN migration delivery check','INBOX'),('listing','CSN migration test 出品','x_Trash'),('spam','[SPAM] CSN migration delivery check','INBOX.Junk')]
 report={'backup':str(backup),'sieve_enabled':True,'fetch_started':False,'maildir_switched':False,'tests':[]}
 (backup/'delivery-report.json').write_text(json.dumps(report,indent=2)+'\n')
 for label,subject,expected in specs:
  mid='<csn-migration-'+token+'-'+label+'@imap.umeoka.co.jp>'
  m=EmailMessage();m['From']='csn@imap.umeoka.co.jp';m['To']='csn@imap.umeoka.co.jp'
  m['Date']=formatdate(localtime=False);m['Message-ID']=mid;m['Subject']=subject
  m.set_content('Synthetic CSN migration delivery test. No customer data.\nCase: '+label+'\n')
  with smtplib.SMTP('127.0.0.1',25,timeout=30) as smtp:
   smtp.ehlo('imap.umeoka.co.jp');refused=smtp.send_message(m,from_addr='csn@imap.umeoka.co.jp',to_addrs=['csn@imap.umeoka.co.jp'])
   if refused:raise RuntimeError('Local SMTP recipient refused')
  report['tests'].append({'case':label,'message_id':mid,'expected':expected,'smtp_accepted':True,'actual':[]})
  (backup/'delivery-report.json').write_text(json.dumps(report,indent=2)+'\n')
 for attempt in range(30):
  for test in report['tests']:
   found=[]
   for p in mails():
    with p.open('rb') as f:header=f.read(65536).split(b'\n\n',1)[0]
    if test['message_id'].encode() in header:
     folder=p.parent.parent
     found.append('INBOX' if folder==HOME/'Maildir' else folder.name.lstrip('.'))
   test['actual']=found
  if all(t['actual'] for t in report['tests']):break
  time.sleep(1)
 report['routing_ok']=all(t['actual']==[t['expected']] for t in report['tests'])
 report['original_message_unchanged']=all(Path(p).exists() and hashlib.sha256(Path(p).read_bytes()).hexdigest()==digest for p,digest in previous.items())
 inactive()
 (backup/'delivery-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps(report,ensure_ascii=False,indent=2))
 if not report['routing_ok'] or not report['original_message_unchanged']:raise RuntimeError('Delivery verification incomplete; do not start fetching')

if __name__=='__main__':
 try:main()
 except Exception:
  print('Stopped. Sieve or test messages may already exist; do not rerun or start fetching. Report output for inspection.',file=sys.stderr);sys.exit(1)
