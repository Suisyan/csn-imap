#!/usr/bin/python3
"""Import the fixed CSN credential packet while mail collection stays disabled."""
import ast
import datetime
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import resource
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = Path('/home/mailadmin/csn-migration/credentials-transfer')
HOME = Path('/home/fetchcsn')
CERT_HASH = 'b2b15bc28a2ab679e51f581bfe467227468b7c75f84e757d15c8b96eba30dd4a'
TEMPLATE_HASH = 'bd5b1b19eeb3307a8bfc68c53528f4bc7ade4aaefb1ccfa157d83232ba8de580'
NAMES = ('info','shop','auction','store','conform','pay','quote','support','bank','error-mail','2013')
ACCOUNTS = {name + '@coolingshop.net' for name in NAMES}


def run(args, data=None):
    result = subprocess.run(args, input=data, capture_output=True)
    if result.returncode:
        raise RuntimeError('A required command failed')
    return result.stdout


def parse_config(data):
    parent = '/run' if os.geteuid() == 0 else None
    with tempfile.TemporaryDirectory(prefix='csn-config-parse-', dir=parent) as directory:
        path = Path(directory) / 'fetchmailrc'
        path.write_bytes(data)
        path.chmod(0o600)
        output = run(['/usr/bin/fetchmail','--configdump','--nodetach','--daemon','0',
                      '--nosyslog','--logfile','/dev/null','--idfile',str(Path(directory)/'unused-ids'),'-f',str(path)])
    constants = {}
    class Substitute(ast.NodeTransformer):
        def visit_Name(self, node):
            if node.id not in constants:
                raise RuntimeError('Unsupported configuration constant')
            return ast.copy_location(ast.Constant(value=constants[node.id]), node)
    for node in ast.parse(output.decode('utf-8')).body:
        if not isinstance(node, ast.Assign):
            continue
        value = ast.literal_eval(Substitute().visit(node.value))
        for target in node.targets:
            if isinstance(target, ast.Name):
                if target.id == 'fetchmailrc':
                    return value
                if isinstance(value, (bool, int, str, type(None))):
                    constants[target.id] = value
    raise RuntimeError('Configuration could not be parsed')


def extract_passwords(config):
    servers = config.get('servers', [])
    if len(servers) != 1:
        raise RuntimeError('Unexpected server count')
    server = servers[0]
    if server.get('pollname') != 'mail.blue.shared-server.net' or server.get('protocol') != 'POP3' or str(server.get('service')) != '995':
        raise RuntimeError('Unexpected source server')
    users = server.get('users', [])
    if len(users) != len(NAMES) or {u.get('remote') for u in users} != ACCOUNTS:
        raise RuntimeError('Unexpected source accounts')
    passwords = {}
    for user in users:
        password = user.get('password')
        if not isinstance(password, str) or not password or any(not 32 <= ord(c) <= 126 for c in password):
            raise RuntimeError('Unsupported or missing password')
        if not user.get('ssl') or not user.get('sslcertck') or user.get('localnames') != ['csn']:
            raise RuntimeError('Unexpected account settings')
        passwords[user['remote']] = password
    return passwords


def render(template, passwords):
    def substitute(match):
        password = passwords[match.group(1).lower() + '@coolingshop.net']
        return ''.join(('\\x%02x' % ord(char)) if char in ('\\', '"') else char for char in password)
    return re.sub(r'__CSN_(INFO|SHOP|AUCTION|STORE|CONFORM|PAY|QUOTE|SUPPORT|BANK|ERROR-MAIL|2013)_PASSWORD__', substitute, template)


def inactive():
    for unit in ('fetchmail-csn.service', 'fetchmail-csn.timer'):
        result = run(['/usr/bin/systemctl','show',unit,'-p','ActiveState','-p','UnitFileState']).decode()
        values = dict(line.split('=', 1) for line in result.splitlines() if '=' in line)
        if values.get('ActiveState') != 'inactive':
            raise RuntimeError('Mail collection is not inactive')
        if unit.endswith('.timer') and values.get('UnitFileState') != 'disabled':
            raise RuntimeError('Mail timer is not disabled')
    if os.path.lexists('/etc/csn-mail/enable-fetch'):
        raise RuntimeError('Activation marker exists')


def regular(path, private=False):
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or (private and stat.S_IMODE(metadata.st_mode) & 0o077):
        raise RuntimeError('Unexpected input file or permissions')


def prepare():
    inactive()
    target = HOME / '.fetchmailrc'
    if os.path.lexists(str(target)):
        raise RuntimeError('Live credential file already exists')
    if not stat.S_ISDIR(HOME.lstat().st_mode) or HOME.stat().st_uid != pwd.getpwnam('fetchcsn').pw_uid:
        raise RuntimeError('Unexpected destination home')
    certificate = ROOT/'recipient.pem'
    key = ROOT/'recipient.key'
    packet = ROOT/'credentials.cms'
    for path in (certificate, key, packet):
        regular(path, private=(path != certificate))
    if hashlib.sha256(certificate.read_bytes()).hexdigest() != CERT_HASH:
        raise RuntimeError('Certificate mismatch')
    public_certificate = run(['/usr/bin/openssl','x509','-in',str(certificate),'-pubkey','-noout'])
    public_key = run(['/usr/bin/openssl','pkey','-in',str(key),'-pubout'])
    if public_certificate != public_key:
        raise RuntimeError('Key mismatch')
    if packet.stat().st_size > 131072:
        raise RuntimeError('Unexpected packet size')
    ciphertext = packet.read_bytes()
    plaintext = run(['/usr/bin/openssl','cms','-decrypt','-binary','-inform','DER','-recip',str(certificate),'-inkey',str(key)],ciphertext)
    if len(plaintext) > 65536:
        raise RuntimeError('Unexpected decrypted size')
    passwords = extract_passwords(parse_config(plaintext))
    template_path = HOME/'.fetchmailrc.template'
    regular(template_path, private=True)
    template = template_path.read_bytes()
    if hashlib.sha256(template).hexdigest() != TEMPLATE_HASH:
        raise RuntimeError('Template changed')
    rendered = render(template.decode('utf-8'), passwords).encode('utf-8')
    parsed = parse_config(rendered)
    if extract_passwords(parsed) != passwords:
        raise RuntimeError('Password round-trip mismatch')
    for server in parsed['servers']:
        for user in server['users']:
            if user.get('smtpname') != 'csn@imap.umeoka.co.jp' or user.get('smtphunt') != ['127.0.0.1/25'] or user.get('mda') or user.get('keep') or not user.get('fetchall'):
                raise RuntimeError('Destination delivery policy mismatch')
    inactive()
    return rendered, hashlib.sha256(ciphertext).hexdigest()


def main():
    if os.geteuid() != 0 or sys.argv[1:] not in (['--check'], ['--import']):
        raise RuntimeError('Invalid invocation')
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.umask(0o077)
    rendered, packet_hash = prepare()
    if sys.argv[1] == '--check':
        print(json.dumps({'credential_check_ok':True,'account_count':len(NAMES),'plaintext_displayed':False,
                          'ciphertext_sha256':packet_hash,'installed':False}))
        return
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = Path('/var/backups')/('csn-credentials-before-'+stamp)
    backup.mkdir(mode=0o700)
    shutil.copy2(str(HOME/'.fetchmailrc.template'),str(backup/'fetchmailrc.template'))
    owner = pwd.getpwnam('fetchcsn')
    descriptor, temporary = tempfile.mkstemp(prefix='.fetchmailrc.import-',dir=str(HOME))
    try:
        with os.fdopen(descriptor,'wb') as stream:
            os.fchown(stream.fileno(),owner.pw_uid,owner.pw_gid)
            os.fchmod(stream.fileno(),0o600)
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        inactive()
        os.link(temporary,str(HOME/'.fetchmailrc'))
    finally:
        os.unlink(temporary)
    installed = (HOME/'.fetchmailrc').read_bytes()
    metadata = (HOME/'.fetchmailrc').stat()
    if installed != rendered or metadata.st_uid != owner.pw_uid or metadata.st_gid != owner.pw_gid or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise RuntimeError('Installed file verification failed')
    inactive()
    report = {'credential_import_ok':True,'account_count':len(NAMES),'owner':'fetchcsn:fetchcsn',
              'mode':'600','backup':str(backup),'ciphertext_sha256':packet_hash,
              'service_inactive':True,'timer_disabled':True,'activation_marker_exists':False,
              'pop_connection_attempted':False,'plaintext_displayed':False}
    (backup/'import-report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Credential import stopped; no plaintext diagnostics emitted.', file=sys.stderr)
        sys.exit(1)
