import subprocess,sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import run
script='''from pathlib import Path
import os,time
root=Path.home()/'csn-dsync-lab/Maildir'
name='synthetic-external-'+str(time.time_ns() if hasattr(time,'time_ns') else int(time.time()*1000000))
raw=b'From: lab@example.invalid\\nTo: lab@example.invalid\\nMessage-ID: <external-maildir@csn-dsync-lab.invalid>\\nSubject: Synthetic direct Maildir delivery\\n\\nSynthetic only.\\n'
tmp=root/'tmp'/name
tmp.write_bytes(raw)
os.rename(str(tmp),str(root/'new'/name))
print('Synthetic direct Maildir delivery created')
'''
p=subprocess.run(run.cmd('old',['python3','-']),input=script.encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)
run.sync();rows=run.assert_pair('direct-maildir-delivery-old-to-new',1)
assert rows[0]['hdr.message-id']=='<external-maildir@csn-dsync-lab.invalid>'
run.dove('new',['flags','add','\\Seen','HEADER','Message-ID','<external-maildir@csn-dsync-lab.invalid>'])
run.sync();rows=run.assert_pair('direct-maildir-message-seen-back-to-old',1);assert 'Seen' in rows[0]['flags']
run.dove('new',['mailbox','create','EmptyFolder']);run.sync()
assert 'EmptyFolder' in run.dove('old',['mailbox','list']).splitlines()
run.dove('old',['mailbox','delete','EmptyFolder']);run.sync()
assert 'EmptyFolder' not in run.dove('new',['mailbox','list']).splitlines()
print('PASS: direct Maildir delivery detection and mailbox deletion')
