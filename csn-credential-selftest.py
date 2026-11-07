#!/usr/bin/python3
"""Use artificial values only; never read either live credential configuration."""
import importlib.util
from pathlib import Path

root=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('credentials',root/'csn-import-credentials.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
expected_names=('info','shop','auction','store','conform','pay','quote','support','bank','error-mail','2013')
assert m.ACCOUNTS=={name+'@coolingshop.net' for name in expected_names}, 'Expected all 11 legacy accounts'
template=(root/'delivery-draft'/'fetchmailrc.template').read_text()
values=['fake-simple','fake-quote"value','fake-back\\slash','fake#hash$','fake space',"fake'quote",'__CSN_INFO_PASSWORD__fake']
passwords={name+'@coolingshop.net':values[i % len(values)] for i,name in enumerate(m.NAMES)}
rendered=m.render(template,passwords)
parsed=m.parse_config(rendered.encode())
assert m.extract_passwords(parsed)==passwords
for value in [''.join(chr(n) for n in range(32,127)), r'fake\x22literal']:
    extra={name+'@coolingshop.net':value for name in m.NAMES}
    assert m.extract_passwords(m.parse_config(m.render(template,extra).encode()))==extra
users=parsed['servers'][0]['users']
assert len(users)==11
short_config=dict(parsed);short_config['servers']=[dict(parsed['servers'][0])]
short_config['servers'][0]['users']=users[:-1]
try:m.extract_passwords(short_config)
except RuntimeError:pass
else:raise AssertionError('Missing legacy account accepted')
duplicate_config=dict(parsed);duplicate_config['servers']=[dict(parsed['servers'][0])]
duplicate_config['servers'][0]['users']=users[:-1]+[users[0]]
try:m.extract_passwords(duplicate_config)
except RuntimeError:pass
else:raise AssertionError('Duplicate legacy account accepted')
assert all(u['smtpname']=='csn@imap.umeoka.co.jp' and u['smtphunt']==['127.0.0.1/25'] for u in users)
users[0]['password']=''
try:m.extract_passwords(parsed)
except RuntimeError:pass
else:raise AssertionError('Empty password accepted')
users[0]['password']='fake\nline'
try:m.extract_passwords(parsed)
except RuntimeError:pass
else:raise AssertionError('Control character accepted')
users[0]['password']='fake-日本語'
try:m.extract_passwords(parsed)
except RuntimeError:pass
else:raise AssertionError('Unsupported non-ASCII password accepted')
print('PASS: artificial-password native-parser round-trip, fixed SMTP recipient, empty/control-character rejection')
