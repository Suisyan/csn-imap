#!/usr/bin/python3
"""Install the pinned CSN draft without enabling mail collection or Sieve."""
import datetime
import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import stat
import subprocess
import sys
import tempfile

SOURCE = Path('/home/mailadmin/csn-migration/delivery-draft')
HOME = Path('/home/fetchcsn')
DRAFT = Path('/etc/csn-mail')
SYSTEMD = Path('/etc/systemd/system')
UNITS = ('fetchmail-csn.service', 'fetchmail-csn.timer')
HASHES = {
 'fetchmailrc.template':'4de42ffb5ee3a541075f696bffc08321c2f54f439555b94c6c863a6184e043d1',
 'fetchmail-csn.service':'996ec1924d10004960b340ccd06b44f97123e65a3f451df7f95fa06b4cf0acd9',
 'fetchmail-csn.timer':'202962834e190f7639a8bde4951bee7115175113cc2602db68490c97c26c9711',
 'csn.sieve':'29edccdffb2738edce8de3e047d5d84e16f42f7c5fb3b19a262dde9a0d5a4f03',
 'syntax-only-dovecot.conf':'67e9c1a7a2005c71b8773f463ba878401d5350c2be3738c54e4c04ed6333ef00',
}

def run(args, env=None):
    p = subprocess.run(args, capture_output=True, text=True, env=env)
    if p.returncode:
        raise RuntimeError('Command failed: ' + args[0] + '\n' + p.stderr[:2000])
    return p.stdout

def state(unit):
    result = subprocess.run(['/usr/bin/systemctl', 'show', unit, '-p', 'LoadState', '-p', 'ActiveState', '-p', 'UnitFileState'], capture_output=True, text=True)
    value = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if result.returncode and not (result.returncode == 1 and value.get('LoadState') == 'not-found'):
        raise RuntimeError('Unable to inspect unit: ' + unit)
    return value

def existing_services():
    return {name: state(name)['ActiveState'] for name in (
        'dovecot.service', 'postfix@-.service', 'rspamd.service', 'redis-server.service')}

def absent(path):
    if os.path.lexists(str(path)):
        raise RuntimeError('Existing path; no overwrite: ' + str(path))

def validate_bundle():
    payload = {}
    for name, expected in HASHES.items():
        path = SOURCE / name
        if not stat.S_ISREG(path.lstat().st_mode):
            raise RuntimeError('Expected a regular source file: ' + name)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise RuntimeError('Source checksum mismatch: ' + name)
        payload[name] = data
    if payload['fetchmailrc.template'].count(b'__CSN_') != 7:
        raise RuntimeError('Expected seven password placeholders')
    return payload

def validate_state():
    for lookup in (pwd.getpwnam, grp.getgrnam):
        try: lookup('fetchcsn')
        except KeyError: pass
        else: raise RuntimeError('fetchcsn already exists; stop for review')
    for path in (HOME, DRAFT, *(SYSTEMD / unit for unit in UNITS)):
        absent(path)
    for unit in UNITS:
        current = state(unit)
        if current.get('LoadState') != 'not-found' or current.get('ActiveState') != 'inactive':
            raise RuntimeError('Unexpected existing CSN unit state')
    for path in SYSTEMD.rglob('*'):
        if path.name in UNITS:
            raise RuntimeError('Existing CSN unit or activation link: ' + str(path))

def check_syntax(payload):
    with tempfile.TemporaryDirectory(prefix='csn-inactive-check-') as directory:
        root = Path(directory)
        for name, data in payload.items():
            (root / name).write_bytes(data)
            (root / name).chmod(0o600)
        env = os.environ.copy()
        env['SYSTEMD_UNIT_PATH'] = directory + ':/usr/lib/systemd/system'
        run(['/usr/bin/systemd-analyze','verify','--man=no',*(str(root/u) for u in UNITS)],env=env)
        run(['/usr/bin/sievec','-c',str(root/'syntax-only-dovecot.conf'),str(root/'csn.sieve'),str(root/'csn.svbin')])
        # Do not emit the configuration dump (it can contain credentials).
        run(['/usr/bin/fetchmail','--configdump','-f',str(root/'fetchmailrc.template')])

def write_new(path, data, mode, uid=0, gid=0):
    with path.open('xb') as stream:
        stream.write(data)
    os.chown(str(path), uid, gid)
    path.chmod(mode)

def main():
    if os.geteuid() != 0 or sys.argv[1:] not in (['--check'], ['--install']):
        raise RuntimeError('Use --check or --install as root')
    os.umask(0o077)
    payload = validate_bundle()
    validate_state()
    check_syntax(payload)
    before_services = existing_services()
    if sys.argv[1] == '--check':
        print(json.dumps({'preflight_ok':True,'installed':False,'existing_services':before_services},indent=2))
        return
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = Path('/var/backups') / ('csn-delivery-before-' + stamp)
    backup.mkdir(mode=0o700)
    for name in ('passwd','shadow','group','gshadow'):
        shutil.copy2('/etc/' + name, str(backup / name))
    (backup/'before.json').write_text(json.dumps({'services':before_services,'bundle_sha256':HASHES},indent=2)+'\n')
    run(['/usr/sbin/useradd','--system','--user-group','--create-home','--home-dir',str(HOME),'--shell','/usr/sbin/nologin','-K','UMASK=077','fetchcsn'])
    account = pwd.getpwnam('fetchcsn')
    HOME.chmod(0o700)
    write_new(HOME/'.fetchmailrc.template',payload['fetchmailrc.template'],0o600,account.pw_uid,account.pw_gid)
    DRAFT.mkdir(mode=0o700)
    write_new(DRAFT/'csn.sieve.draft',payload['csn.sieve'],0o600)
    write_new(DRAFT/'bundle-sha256.json',(json.dumps(HASHES,indent=2)+'\n').encode(),0o600)
    for unit in UNITS:
        write_new(SYSTEMD/unit,payload[unit],0o644)
    # Reload unit definitions only; never enable, start, stop, or restart a service.
    run(['/usr/bin/systemctl','daemon-reload'])
    after = {unit: state(unit) for unit in UNITS}
    for unit, value in after.items():
        if value.get('LoadState') != 'loaded' or value.get('ActiveState') != 'inactive':
            raise RuntimeError('Unexpected installed unit state; inspect without starting')
    if after['fetchmail-csn.timer'].get('UnitFileState') != 'disabled':
        raise RuntimeError('CSN timer is not disabled')
    for path in (HOME/'.fetchmailrc', DRAFT/'enable-fetch'):
        absent(path)
    after_services = existing_services()
    report = {'backup':str(backup),'installed_inactive':True,'fetchcsn_uid':account.pw_uid,
              'fetchcsn_gid':account.pw_gid,'fetchcsn_shell':account.pw_shell,
              'units':after,'real_passwords_installed':False,'activation_marker_exists':False,
              'sieve_activated':False,'live_maildir_touched':False,
              'existing_services_unchanged':before_services == after_services,
              'existing_services':after_services}
    (backup/'installation-report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    if before_services != after_services:
        raise RuntimeError('Existing service state changed; review before further work')

if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Inactive installation stopped: ' + str(error), file=sys.stderr)
        sys.exit(1)
