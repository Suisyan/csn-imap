#!/usr/bin/python3
"""Synthetic non-delivering routing tests; no live mail is read."""
from pathlib import Path
from email.message import EmailMessage
from email.header import Header
import json,re,subprocess,tempfile
base=Path('/home/mailadmin/csn-migration/delivery-draft')
cases=[
 ('ordinary','CSN migration test','INBOX',{}),
 ('spam-subject','[SPAM] 注文確認','INBOX.Junk',{}),
 ('spam-header','注文確認','INBOX.Junk',{'X-Spam-Status':'Yes'}),
 ('rspamd-header','注文確認','INBOX.Junk',{'X-Rspamd-Action':'add header'}),
 ('listing','出品のお知らせ','x_Trash',{}),
 ('relisting','Yahoo!オークション - 再出品','x_Trash',{}),
 ('ended','Yahoo!オークション - 終了','x_Trash',{}),
 ('cancelled','取り消し','x_Trash',{}),
 ('order','注文確認','_01注文確認',{}),
 ('question','クーリングショップ ドット ネットへのお問い合わせ','_03質問',{}),
 ('payment','評価 支払い完了','_04支払い完了',{}),
 ('payment-procedure','評価 支払い手続き完了','_04支払い完了',{}),
 ('payment-alone','支払い完了','INBOX',{}),
 ('rating','評価','_05評価',{}),
 ('customer','お客様情報変更','_06お客様情報',{}),
 ('catalog','特別会員資料請求','_07資料請求',{}),
 ('direct-order','注文番号','_08直接注文',{}),
 ('contact','お問合せ','_09直接問合せ',{}),
 ('fit','適合確認','_10適合確認',{}),
 ('shipment','発送案内','_11発送案内',{}),
 ('defect','不具合','_12不具合',{}),
 ('not-spam','SPAM 注文確認','_01注文確認',{})]
results=[]
with tempfile.TemporaryDirectory(prefix='csn-routing-test-') as d:
 root=Path(d)
 for name in ('cur','new','tmp'):(root/'Maildir'/name).mkdir(parents=True)
 for label,subject,expected,headers in cases:
  for charset in ('utf-8','iso-2022-jp'):
   m=EmailMessage();m['From']='test@example.invalid';m['To']='csn@imap.umeoka.co.jp'
   for key,value in headers.items():m[key]=value
   m.set_content('Synthetic routing validation. No real customer data.')
   raw=b'Subject: '+Header(subject,charset).encode().encode('ascii')+b'\n'+m.as_bytes()
   path=root/'message.eml';path.write_bytes(raw)
   p=subprocess.run(['/usr/bin/sieve-test','-c',str(base/'syntax-only-dovecot.conf'),'-l','maildir:'+str(root/'Maildir'),str(base/'csn.sieve'),str(path)],capture_output=True,text=True)
   folders=re.findall(r'store message in folder: (.+)',p.stdout)
   if expected=='INBOX' and not folders and 'keep' in p.stdout.lower() and '(none)' not in p.stdout.split('Implicit keep:')[-1]:folders=['INBOX']
   ok=p.returncode==0 and folders==[expected]
   results.append({'case':label,'charset':charset,'expected':expected,'actual':folders,'ok':ok})
   if not ok:print(p.stdout,p.stderr)
 report={'cases':len(results),'passed':sum(r['ok'] for r in results),'delivery_executed':False,'results':results}
 print(json.dumps(report,ensure_ascii=False,indent=2))
 (base/'routing-test-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
 if not all(r['ok'] for r in results):raise SystemExit(1)
