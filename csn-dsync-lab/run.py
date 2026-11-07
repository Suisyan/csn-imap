#!/usr/bin/python3
"""Exercise actual Dovecot 2.2/2.3 through two authenticated SSH streams."""
import subprocess,threading,shlex,json,csv,io,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SSH=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10']
HOSTS={
 'old':(SSH+['-o','UserKnownHostsFile='+str(ROOT/'.migration-ssh/csn-v2-known-hosts'),'-i',str(ROOT/'.migration-ssh/csn-v2-migration'),'umeoka@118.27.32.100'],'/home/umeoka/csn-dsync-lab'),
 'new':(SSH+['-o','UserKnownHostsFile=/tmp/conoha-imap-hostkey','-i',str(ROOT/'mail-vps3-key.pem'),'mailadmin@163.44.111.155'],'/home/mailadmin/csn-dsync-lab')}
def cmd(host,args):return HOSTS[host][0]+[shlex.join(args)]
def dove(host,args,data=None):
 p=subprocess.run(cmd(host,['doveadm','-c',HOSTS[host][1]+'/dovecot.conf']+args),input=data,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=40)
 if p.returncode:raise RuntimeError(host+' doveadm '+str(args)+' failed: '+p.stderr.decode())
 return p.stdout.decode()
def sync(mode="sync"):
 listener=subprocess.Popen(cmd('new',['python3',HOSTS['new'][1]+'/relay.py','listen']),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 if listener.stderr.readline().strip()!=b'READY':raise RuntimeError('Relay failed')
 server=subprocess.Popen(cmd('old',['doveadm','-c',HOSTS['old'][1]+'/dovecot.conf','dsync-server']),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 def pump(src,dst):
  try:
   while True:
    data=src.read1(65536)
    if not data:break
    dst.write(data);dst.flush()
  except BrokenPipeError:pass
  finally:dst.close()
 ts=[threading.Thread(target=pump,args=(listener.stdout,server.stdin),daemon=True),threading.Thread(target=pump,args=(server.stdout,listener.stdin),daemon=True)]
 for t in ts:t.start()
 try:
  operation=['backup','-R','-f'] if mode=='backup' else ['sync','-f']
  output=dove('new',operation+['python3',HOSTS['new'][1]+'/relay.py','connect'])
  for p in (server,listener):
   p.wait(timeout=15)
   if p.returncode:raise RuntimeError('Peer failed: '+p.stderr.read().decode())
 finally:
  for p in (listener,server):
   if p.poll() is None:p.terminate()
 return output
def snapshot(host):
 text=dove(host,['-f','tab','fetch','mailbox guid flags hdr.message-id','ALL'])
 rows=list(csv.DictReader(io.StringIO(text),delimiter='\t'))
 for row in rows:row['flags']=' '.join(sorted(flag for flag in row['flags'].split() if flag != r'\Recent'))
 return sorted(rows,key=lambda x:x['hdr.message-id'])
def save(host,label):
 raw=('From: lab@example.invalid\nTo: lab@example.invalid\nMessage-ID: <'+label+'@csn-dsync-lab.invalid>\nSubject: Synthetic '+label+'\nDate: Thu, 05 Nov 2026 09:00:00 +0900\n\nSynthetic synchronization test only.\n').encode()
 dove(host,['save','-m','INBOX'],raw)
def assert_pair(label,expected_count):
 a=snapshot('old');b=snapshot('new')
 assert a==b,(label,a,b)
 assert len(a)==expected_count,(label,a)
 print(json.dumps({'test':label,'messages':len(a),'both_equal':True,'state':a}),flush=True)
 return a
if __name__=='__main__':
 save('old','old-origin');save('new','new-origin');sync();assert_pair('initial-two-way',2)
 dove('new',['flags','add','\\Seen','HEADER','Message-ID','<old-origin@csn-dsync-lab.invalid>']);sync()
 a=assert_pair('seen-new-to-old',2);assert any('Seen' in r['flags'] for r in a if 'old-origin' in r['hdr.message-id'])
 dove('old',['flags','remove','\\Seen','HEADER','Message-ID','<old-origin@csn-dsync-lab.invalid>']);sync()
 a=assert_pair('unseen-old-to-new',2);assert all('Seen' not in r['flags'] for r in a)
 dove('new',['mailbox','create','Archive']);dove('new',['move','Archive','mailbox','INBOX','HEADER','Message-ID','<old-origin@csn-dsync-lab.invalid>']);sync()
 a=assert_pair('move-new-to-old',2);assert any(r['mailbox']=='Archive' for r in a)
 dove('old',['mailbox','rename','Archive','Renamed']);sync()
 a=assert_pair('rename-old-to-new',2);assert any(r['mailbox']=='Renamed' for r in a)
 dove('old',['expunge','mailbox','Renamed','HEADER','Message-ID','<old-origin@csn-dsync-lab.invalid>']);sync();assert_pair('expunge-old-to-new',1)
 dove('new',['expunge','mailbox','INBOX','HEADER','Message-ID','<new-origin@csn-dsync-lab.invalid>']);sync();assert_pair('expunge-new-to-old',0)
 print('PASS: isolated two-way save, seen/unseen, move, rename, expunge')
