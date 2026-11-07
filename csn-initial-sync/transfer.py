#!/usr/bin/python3
"""Relay fixed source backup to isolated receiver. Never print the protocol."""
import sys,subprocess,threading,json,time,os,datetime
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'csn-dsync-lab'));import run
os.umask(0o077)
root=run.ROOT/'.migration-data'
stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
reportfile=root/('initial-sync-'+stamp+'.json')
count=[0,0];errors=[]
with (root/('initial-source-'+stamp+'.stderr')).open('wb') as err1,(root/('initial-receiver-'+stamp+'.stderr')).open('wb') as err2:
 source=subprocess.Popen(run.cmd('old',['sudo','-n','/usr/local/sbin/csn-initial-backup-source']),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=err1)
 sink=subprocess.Popen(run.cmd('new',['doveadm','-c','/home/mailadmin/csn-migration/initial-sync-receiver/dovecot.conf','dsync-server']),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=err2)
 def pump(a,b,i):
  try:
   while True:
    block=a.read1(65536)
    if not block:break
    b.write(block);b.flush();count[i]+=len(block)
  except BrokenPipeError:pass
  except Exception as e:errors.append(type(e).__name__)
  finally:
   try:b.close()
   except BrokenPipeError:pass
 threads=[threading.Thread(target=pump,args=(source.stdout,sink.stdin,0),daemon=True),threading.Thread(target=pump,args=(sink.stdout,source.stdin,1),daemon=True)]
 for t in threads:t.start()
 start=time.monotonic()
 print(json.dumps({'started':True,'destination':'isolated initial-sync-receiver','production_maildir_switched':False,'report':str(reportfile)}),flush=True)
 try:
  while source.poll() is None or sink.poll() is None:
   time.sleep(10)
   if int(time.monotonic()-start)//10%3==0:print(json.dumps({'elapsed_seconds':round(time.monotonic()-start),'source_stream_bytes':count[0],'receiver_stream_bytes':count[1]}),flush=True)
   if time.monotonic()-start>7200:raise TimeoutError('Transfer exceeded two hours')
  for t in threads:t.join(timeout=10)
  report={'source_exit':source.returncode,'receiver_exit':sink.returncode,'elapsed_seconds':round(time.monotonic()-start),'source_stream_bytes':count[0],'receiver_stream_bytes':count[1],'relay_errors':errors,'production_maildir_switched':False,'complete':source.returncode==0 and sink.returncode==0 and not errors}
  reportfile.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)
  if not report['complete']:raise SystemExit(1)
 finally:
  for p in (source,sink):
   if p.poll() is None:p.terminate()
