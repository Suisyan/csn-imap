#!/usr/bin/python3
"""Read-only initial-merge inventory; outputs hashes, never headers/bodies."""
import email.policy,hashlib,json,os,pwd,re,sys
from email.parser import BytesHeaderParser
from pathlib import Path

def fingerprint(raw):
 wire=raw.replace(b'\r\n',b'\n')
 head,separator,body=wire.partition(b'\n\n')
 parsed=BytesHeaderParser(policy=email.policy.compat32).parsebytes(head+b'\n\n')
 mid=str(parsed.get('Message-ID','')).strip()
 names=('Message-ID','Date','From','To','Cc','Subject','MIME-Version','Content-Type','Content-Transfer-Encoding')
 stable=[(name,[re.sub(r'\s+',' ',str(v)).strip() for v in parsed.get_all(name,[])]) for name in names]
 return {'raw_sha256':hashlib.sha256(raw).hexdigest(),'message_id_present':bool(mid),'message_id_sha256':hashlib.sha256(mid.encode('utf-8','surrogatepass')).hexdigest(),'origin_headers_sha256':hashlib.sha256(json.dumps(stable,ensure_ascii=True,separators=(',',':')).encode()).hexdigest(),'body_sha256':hashlib.sha256(body).hexdigest(),'valid_header_boundary':bool(separator)}

def inventory(root):
 if not root.is_dir() or root.is_symlink():raise RuntimeError('Expected real Maildir')
 records=[]
 for base,dirs,files in os.walk(str(root),followlinks=False):
  path=Path(base)
  if path.name not in ('cur','new'):continue
  for name in sorted(files):
   p=path/name
   if p.is_symlink() or not p.is_file():raise RuntimeError('Unexpected mail entry')
   s=p.stat();raw=p.read_bytes();after=p.stat()
   if (s.st_size,s.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise RuntimeError('Mail changed while reading')
   row=fingerprint(raw)
   row.update(reference_sha256=hashlib.sha256(str(p.relative_to(root)).encode()).hexdigest(),folder=str(path.parent.relative_to(root)),flags=name.split(':2,',1)[1] if ':2,' in name else '',bytes=len(raw))
   records.append(row)
 return {'schema':'csn-fingerprint-v1','read_only':True,'message_count':len(records),'records':records,'automatic_deletion':False}

def main():
 if sys.argv[1:]==['--new']:
  if os.geteuid()!=0:raise RuntimeError('sudo required to read new CSN Maildir')
  result=inventory(Path('/home/csn/Maildir'))
  target=Path('/home/mailadmin/csn-migration/new-mail-fingerprints.json')
  owner=pwd.getpwnam('mailadmin')
  fd=os.open(str(target),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
  with os.fdopen(fd,'w') as stream:
   os.fchown(stream.fileno(),owner.pw_uid,owner.pw_gid)
   json.dump(result,stream,ensure_ascii=False,indent=2)
  print(json.dumps({'read_only_mail_inspection':True,'message_count':result['message_count'],'report':str(target),'message_contents_displayed':False},ensure_ascii=False,indent=2))
 elif sys.argv[1:]==['--source']:
  root=Path('.migration-data/csn-Maildir-sync-audit');result=inventory(root)
  out=Path('.migration-data/csn-source-fingerprints.json')
  with out.open('w') as f:json.dump(result,f,ensure_ascii=False)
  out.chmod(0o600)
  print(json.dumps({'source_messages':result['message_count'],'index_saved':str(out),'read_only':True}))
 elif sys.argv[1:]==['--selftest']:
  a=b'Message-ID: <test@invalid>\nFrom: test@example.invalid\nSubject: Synthetic\n\nOriginal body.\n'
  b=b'Received: by synthetic\nX-Spam-Status: No\n'+a
  c=a.replace(b'Original body.',b'Different body.')
  x,y,z=map(fingerprint,(a,b,c))
  assert x['origin_headers_sha256']==y['origin_headers_sha256'] and x['body_sha256']==y['body_sha256'] and x['raw_sha256']!=y['raw_sha256']
  assert x['message_id_sha256']==z['message_id_sha256'] and x['body_sha256']!=z['body_sha256']
  assert not fingerprint(b'Subject: Missing ID\n\nbody')['message_id_present']
  assert fingerprint(b'From: broken@\nMessage-ID: <test@invalid>\n\nbody')['message_id_present']
  print('PASS: delivery header changes are candidates; same Message-ID/different body distinguished; missing Message-ID flagged')
 else:raise RuntimeError('Use --new, --source or --selftest')
if __name__=='__main__':
 try:main()
 except Exception:
  print('Read-only fingerprint inspection stopped; message contents suppressed.',file=sys.stderr);sys.exit(1)
