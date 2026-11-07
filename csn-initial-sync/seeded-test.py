import sys,subprocess,threading,json,os
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'csn-dsync-lab'));import run
run.HOSTS['new']=(run.HOSTS['new'][0],'/home/mailadmin/csn-seeded-lab')
oldbefore=run.snapshot('old')
if os.environ.get('CSN_TEST_PROBE')=='1':run.save('new','destination-only-one-way-probe')
source=subprocess.Popen(run.cmd('old',['python3',run.HOSTS['old'][1]+'/backup-source-shadow.py','--lab']),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
sink=subprocess.Popen(run.cmd('new',['doveadm','-c',run.HOSTS['new'][1]+'/dovecot.conf','dsync-server']),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
def pump(a,b):
 try:
  while True:
   block=a.read1(65536)
   if not block:break
   b.write(block);b.flush()
 except BrokenPipeError:pass
 finally:
  try:b.close()
  except BrokenPipeError:pass
threads=[threading.Thread(target=pump,args=(source.stdout,sink.stdin),daemon=True),threading.Thread(target=pump,args=(sink.stdout,source.stdin),daemon=True)]
for t in threads:t.start()
try:
 source.wait(timeout=90);sink.wait(timeout=30)
 print(json.dumps({'source_exit':source.returncode,'sink_exit':sink.returncode}))
 assert source.returncode==0 and sink.returncode==0,(source.stderr.read().decode(),sink.stderr.read().decode())
finally:
 for p in (source,sink):
  if p.poll() is None:p.terminate()
oldafter=run.snapshot('old');newafter=run.snapshot('new')
assert oldbefore==oldafter
assert oldafter==newafter
assert not any('destination-only-one-way-probe' in x['hdr.message-id'] for x in oldafter)
print(json.dumps({'one_way_source_unchanged':True,'destination_mirrors_source':True,'destination_only_probe_not_written_back':True,'messages':len(oldafter)}))
