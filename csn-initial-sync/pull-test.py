import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'csn-dsync-lab'));import run
run.HOSTS['new']=(run.HOSTS['new'][0],'/home/mailadmin/csn-initial-empty-lab')
before=run.snapshot('old')
run.sync(mode='backup')
a=run.snapshot('old');b=run.snapshot('new')
assert before==a and a==b
assert not any('destination-only-one-way-probe' in x['hdr.message-id'] for x in a)
print(json.dumps({'test':'new-initiates-backup-R','source_unchanged':True,'destination_matches':True,'messages':len(a)}),flush=True)
run.save('new','new-side-only-pull-probe');run.save('old','old-new-arrival-pull-test')
before=run.snapshot('old');run.sync(mode='backup')
a=run.snapshot('old');b=run.snapshot('new');assert before==a and a==b
assert not any('new-side-only-pull-probe' in x['hdr.message-id'] for x in a)
print(json.dumps({'test':'repeat-with-source-arrival-and-destination-only-message','source_unchanged':True,'destination_matches':True,'messages':len(a)}),flush=True)
run.sync(mode='backup');assert run.snapshot('old')==a and run.snapshot('new')==b
print('PASS: new-side pull, one-way catchup and repeat without old-side message changes')
