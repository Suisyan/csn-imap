"""Interrupt only the synthetic lab stream and verify lossless retry."""
import subprocess,threading,json,sys,time,uuid,hashlib
from pathlib import Path
import run
TAG='retry-'+uuid.uuid4().hex[:12]

def inject(label,large=False):
 code="""from pathlib import Path
import os,hashlib
root=Path.home()/'csn-dsync-lab/Maildir'
label=%r
body=(b'Synthetic payload 0123456789abcdef\\n'*65536) if %r else b'Synthetic arrival during synchronization.\\n'
raw=('From: lab@example.invalid\\nTo: lab@example.invalid\\nMessage-ID: <'+label+'@csn-dsync-lab.invalid>\\nSubject: Synthetic retry test\\n\\n').encode()+body
p=root/'tmp'/label;p.write_bytes(raw);os.rename(str(p),str(root/'new'/label))
"""%(label,large)
 subprocess.run(run.cmd('old',['python3','-']),input=code.encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True,timeout=20)

def attempt(cut=False,arrival=False):
 listener=subprocess.Popen(run.cmd('new',['python3',run.HOSTS['new'][1]+'/relay.py','listen']),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 assert listener.stderr.readline().strip()==b'READY'
 server=subprocess.Popen(run.cmd('old',['doveadm','-c',run.HOSTS['old'][1]+'/dovecot.conf','dsync-server']),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 transferred=[0];injected=[False];errors=[]
 def pump(src,dst,forward):
  try:
   while True:
    data=src.read1(16384)
    if not data:break
    dst.write(data);dst.flush()
    if forward:
     transferred[0]+=len(data)
     if arrival and transferred[0]>=32768 and not injected[0]:
      inject(TAG+'-during');injected[0]=True
     if cut and transferred[0]>=65536:break
  except (BrokenPipeError,ValueError):pass
  except Exception as e:errors.append(type(e).__name__)
  finally:
   dst.close()
   if cut and forward:src.close()
 ts=[threading.Thread(target=pump,args=(listener.stdout,server.stdin,False),daemon=True),threading.Thread(target=pump,args=(server.stdout,listener.stdin,True),daemon=True)]
 for t in ts:t.start()
 client=subprocess.Popen(run.cmd('new',['doveadm','-c',run.HOSTS['new'][1]+'/dovecot.conf','sync','-f','python3',run.HOSTS['new'][1]+'/relay.py','connect']),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 try:
  stdout,stderr=client.communicate(timeout=45)
  for p in (server,listener):p.wait(timeout=15)
 finally:
  for p in (client,server,listener):
   if p.poll() is None:p.terminate()
 for t in ts:t.join(timeout=3)
 assert not errors,errors
 print(json.dumps({'test':'interrupted' if cut else 'receiving-during-sync','stream_bytes':transferred[0],'client_exit':client.returncode,'arrival_during_stream':injected[0]}),flush=True)
 if cut:assert client.returncode!=0 and transferred[0]>=65536
 else:assert client.returncode in (0,2)
 return injected[0]

def digests(host):
 code="""from pathlib import Path
import hashlib,json,email
root=Path.home()/'csn-dsync-lab/Maildir'
result=[]
for p in root.rglob('*'):
 if p.is_file() and p.parent.name in ('cur','new'):
  raw=p.read_bytes().replace(b'\\r\\n',b'\\n')
  mid=email.message_from_bytes(raw).get('Message-ID','')
  if %r in mid:result.append([mid,hashlib.sha256(raw).hexdigest()])
print(json.dumps(sorted(result)))
"""%TAG
 p=subprocess.run(run.cmd(host,['python3','-']),input=code.encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True,timeout=20)
 return json.loads(p.stdout)

inject(TAG+'-large',True)
assert attempt(cut=True,arrival=True)
run.sync();a=digests('old');b=digests('new')
assert a==b and len(a)==2,(a,b)
run.sync();assert digests('new')==b
print(json.dumps({'test':'retry-and-repeat','messages':2,'content_hashes_match':True,'duplicates':False}),flush=True)
inject(TAG+'-large-second',True)
# Independent successful stream with another arrival; unique label to avoid overwriting prior fixture.
oldtag=TAG;TAG=oldtag+'-second'
assert attempt(arrival=True)
run.sync()
TAG=oldtag
a=digests('old');b=digests('new');assert a==b and len(a)==4,(a,b)
print(json.dumps({'test':'ongoing-delivery-catchup','messages':4,'content_hashes_match':True,'duplicates':False}),flush=True)
print('PASS: interrupted transfer rejects success; retry, repeat, concurrent arrival and content integrity')
