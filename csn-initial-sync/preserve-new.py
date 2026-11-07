#!/usr/bin/python3
"""Back up all twelve new-side messages; copy six unmatched messages offline."""
import datetime,hashlib,json,os,pwd,re,stat,subprocess
from pathlib import Path
LIVE=Path('/home/csn/Maildir')
PRESERVED=Path('/home/csn/Maildir.new-side-preserved')
EXPECTED={'97518651bd5c425b1c43ebaf464d2a4ad92703499e0b6a772b0bdcc8b34ee564': 'd89643313bc0f2cd43d9292ca404ecfb9c3dca4d457d8072fdbe1cb424d6c631', 'e5b4ab56062cbce63af755960efc5b2aa04881a91d77f417536b82b7cc1b0744': '77c731982b341bd80e3ca490f400d7cce1ed31052ff460b3584cb68852b37df5', 'b570ee3d013fcf428947d6b057f202b67797be9df5b465b08a85df0a5c7020fa': '8906f1abbc77d4f904c652883e33b60b2fcd337194752c8b58909d53de0132cd', 'a13662de93f8f18282df960566bd93e07740c7be652d5b89e2d3b17d504ffbea': 'f0ab18555051813bba8435c82dcb19d486f2d4b7cb1baac83e69da5e7ac10c92', '4d730b1ee4b5de69daebd21545b23c8792ced2a6a708fa5d330d8563e4803d4d': '2c31b41ce74d1e35791da906d5d9c6f56eecb15cb29ace76531a70c48ec2aa07', '762aadade92e07b28278c20161f856f757851411f006153220629ea32be7a972': '54537b3601af8c7714a0dbc96af5f5d9e0785453457b4106b24867a04d8bffb2', 'c749fe0ced8abb2771d7fd4399b6f444e72fba056d32786fab26d93ebec83064': '6212ed9d1b2c5d04d2a22a72825abf3f30056c15a6dce650a41fe6dbd2a65200', '4fe2d3aeb923684627ff8b140acb054535d5d9e6a085ebb652977f70aa3c2709': '093745119adb0cdc256c5b47570eac36feef4c36a9a6be753baa33f1fbd6fd97', 'a4bf28bde41b38709d83f68ce3f96140a95191a185005061a6dbdbf67b064a24': '49c852509c7dc5d3dc9fd82f4028edbc6dacc49e18cec45aeee3223aacaa45bf', 'd25d863909d6edae131263707499b5a79997981d8e5bdd97a96b63ad8aab9bef': '554c7abe37057ef2674168d45df70a0e3ba8f58521ea535099d1a68eceaf143a', 'b61d7a774a25a1a3d76905a2c03dc7938090df17fd5e1111d62e926b52a8fc80': '6760dbdcd3053c6ecb10880070fe7c2a9614c8f85e2880178b1858502d0be907', '6b8476eaea7b77d5ea9118b0d719c3197bd007f6b1f9a7c06b5d50ec92161b3d': '0d5cc0a61ec92ce67dbb9b54a0198c220085977d8cd8eb1fd4c386bc7d4b84dd'}
UNMATCHED={'97518651bd5c425b1c43ebaf464d2a4ad92703499e0b6a772b0bdcc8b34ee564', '6b8476eaea7b77d5ea9118b0d719c3197bd007f6b1f9a7c06b5d50ec92161b3d', '762aadade92e07b28278c20161f856f757851411f006153220629ea32be7a972', 'c749fe0ced8abb2771d7fd4399b6f444e72fba056d32786fab26d93ebec83064', '4d730b1ee4b5de69daebd21545b23c8792ced2a6a708fa5d330d8563e4803d4d', 'b61d7a774a25a1a3d76905a2c03dc7938090df17fd5e1111d62e926b52a8fc80'}
PROBES={'762aadade92e07b28278c20161f856f757851411f006153220629ea32be7a972', 'b61d7a774a25a1a3d76905a2c03dc7938090df17fd5e1111d62e926b52a8fc80', '97518651bd5c425b1c43ebaf464d2a4ad92703499e0b6a772b0bdcc8b34ee564'}

def inventory(root):
 records={}
 if root.is_symlink() or not root.is_dir():raise RuntimeError('Unexpected mailbox directory')
 for base,dirs,files in os.walk(str(root),followlinks=False):
  for name in dirs+files:
   p=Path(base)/name
   if p.is_symlink():raise RuntimeError('Unexpected symbolic link')
  if Path(base).name not in ('cur','new'):continue
  for name in files:
   p=Path(base)/name
   if not stat.S_ISREG(p.lstat().st_mode):raise RuntimeError('Unexpected mail entry')
   rel=str(p.relative_to(root));ref=hashlib.sha256(rel.encode()).hexdigest()
   records[ref]={'relative':rel,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
 return records

def verify(records):
 if {ref:r['sha256'] for ref,r in records.items()}!=EXPECTED:raise RuntimeError('New-side mail changed; stop for a new comparison')

def main():
 if os.geteuid()!=0:raise RuntimeError('Run with sudo python3')
 os.umask(0o077)
 for unit in ('fetchmail-csn.service','fetchmail-csn.timer'):
  p=subprocess.run(['/usr/bin/systemctl','show',unit,'-p','ActiveState'],capture_output=True,text=True,check=True)
  if p.stdout.strip()!='ActiveState=inactive':raise RuntimeError('New fetch must be inactive')
 if os.path.lexists('/etc/csn-mail/enable-fetch') or os.path.lexists(str(PRESERVED)):raise RuntimeError('Activation marker or preservation directory exists')
 owner=pwd.getpwnam('csn')
 if owner.pw_uid!=1001 or owner.pw_gid!=1001:raise RuntimeError('Unexpected owner')
 records=inventory(LIVE);verify(records)
 for ref in UNMATCHED:
  name=Path(records[ref]['relative']).name
  flags=name.split(':2,',1)[1] if ':2,' in name else ''
  if not re.fullmatch('[DFPRST]*',flags):raise RuntimeError('Custom keyword flags need review')
 stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
 backup=Path('/var/backups')/('csn-new-side-before-'+stamp);backup.mkdir(mode=0o700)
 subprocess.run(['/bin/cp','-a',str(LIVE),str(backup/'Maildir')],check=True)
 verify(inventory(backup/'Maildir'));verify(inventory(LIVE))
 PRESERVED.mkdir(mode=0o700);os.chown(str(PRESERVED),owner.pw_uid,owner.pw_gid)
 for sub in ('cur','new','tmp'):
  p=PRESERVED/sub;p.mkdir(mode=0o700);os.chown(str(p),owner.pw_uid,owner.pw_gid)
 mapping=[]
 for ref in sorted(UNMATCHED):
  rel=Path(records[ref]['relative']);name=rel.name
  flags=name.split(':2,',1)[1] if ':2,' in name else ''
  target=PRESERVED/rel.parent.name/(ref+'.preserved'+(':2,'+flags if rel.parent.name=='cur' else ''))
  subprocess.run(['/bin/cp','-p',str(backup/'Maildir'/rel),str(target)],check=True)
  os.chown(str(target),owner.pw_uid,owner.pw_gid);target.chmod(0o600)
  if hashlib.sha256(target.read_bytes()).hexdigest()!=EXPECTED[ref]:raise RuntimeError('Preservation hash mismatch')
  mapping.append({'reference':ref,'original_relative_path':str(rel),'preserved_relative_path':str(target.relative_to(PRESERVED)),'sha256':EXPECTED[ref]})
 verify(inventory(LIVE))
 if len(inventory(PRESERVED))!=6:raise RuntimeError('Unexpected preservation count')
 # Confirm the original single message against the pre-Sieve backup record.
 previous=json.loads(Path('/var/backups/csn-sieve-before-20261104T232348Z/state.json').read_text())
 original_hashes=set(previous['previous_mail_sha256'].values())
 original_matches=[ref for ref in UNMATCHED if EXPECTED[ref] in original_hashes]
 if len(original_matches)!=1:raise RuntimeError('Original single message identification needs review')
 report={'backup':str(backup),'all_new_messages_backed_up':len(records),'separate_maildir':str(PRESERVED),'unmatched_messages_preserved':len(UNMATCHED),'original_single_message_identified':len(original_matches),'known_synthetic_messages':len(PROBES),'other_unmatched_messages':len(UNMATCHED)-len(PROBES)-len(original_matches),'live_messages_unchanged':True,'copied_not_moved':True,'production_maildir_switched':False,'fetch_started':False,'included_in_sync':False}
 (backup/'preservation-map.json').write_text(json.dumps(mapping,indent=2)+'\n')
 (backup/'report.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':
 try:main()
 except Exception:
  print('Preservation stopped; originals were not intentionally modified. Do not rerun before inspection.',file=__import__('sys').stderr);raise SystemExit(1)
